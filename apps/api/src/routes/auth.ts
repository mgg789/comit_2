/** Email-code registration/login and revocable role-scoped sessions. */
import { randomBytes } from "node:crypto";
import { yandexCallback, yandexIdentity } from "../auth/yandex.js";
import type { FastifyInstance } from "fastify";
import type { AccountRole, AccountView, EmailCodeVerification } from "@comit/shared";
import type { AuthConfig } from "../auth/config.js";
import { cookieName, originAllowed, resolveSession, signSession } from "../auth/session.js";
import { AuthError, type AuthStore, type AuthAccount } from "../auth/store.js";
import { requireEmail } from "../auth/crypto.js";

/** Shared auth dependencies allow real PostgreSQL and SMTP acceptance without global test state. */
export interface AuthOptions {
  config: AuthConfig;
  store: AuthStore;
  mailConfigured: boolean;
  /** Injectable provider HTTP boundary for offline acceptance tests. */
  oauthTransport?: typeof fetch;
}

/** Canonicalize mailbox identities once; quoted and international local parts are intentionally unsupported. */
export function normalizeEmail(value: string): string {
  try { return requireEmail(value); } catch { throw new AuthError(422, "invalid_email"); }
}

/** Expose the recorded processing consent rather than inferring it from email verification. */
export function accountView(account: AuthAccount): AccountView {
  return account;
}

const accountSchema = {
  type: "object", additionalProperties: false, required: ["id", "email", "role", "email_verified", "created_at", "consent"],
  properties: { id: { type: "string", format: "uuid" }, email: { type: "string" },
    role: { enum: ["candidate", "employer"] }, email_verified: { const: true }, created_at: { type: "string" },
    consent: { type: "object", required: ["processing", "version", "accepted_at"],
      properties: { processing: { const: true }, version: { type: "string" }, accepted_at: { type: "string" } } } },
} as const;

/** Register fixed candidate/employer endpoints. No account is created before a valid one-time code. */
export async function authRoutes(app: FastifyInstance, options: AuthOptions): Promise<void> {
  const { config, store } = options;
  app.setErrorHandler((error, _request, reply) => {
    if (error instanceof AuthError) return reply.code(error.status).header("cache-control", "no-store").send({ detail: error.code });
    if (error && typeof error === "object" && "validation" in error) return reply.code(422).send({ detail: "invalid_request" });
    return reply.code(503).send({ detail: "auth_unavailable" });
  });
  for (const role of ["candidate", "employer"] satisfies AccountRole[]) {
    const root = `/auth/${role}`;
    const oauthCookie = `${config.secureCookies ? "__Host-" : ""}comit_${role}_oauth`;
    const oauthCookieOptions = { path: "/", httpOnly: true, secure: config.secureCookies, sameSite: "lax" as const };
    app.get(`${root}/providers`, { schema: { tags: ["auth"], response: { 200: {
      type: "object", required: ["yandex"], properties: { yandex: { type: "boolean" } } } } } }, async (_request, reply) =>
      reply.header("cache-control", "no-store").send({ yandex: Boolean(config.yandex) }));
    app.post<{ Body: { consent: { processing: true; version: string } } }>(`${root}/yandex/start`, {
      schema: { tags: ["auth"], body: { type: "object", additionalProperties: false, required: ["consent"], properties: {
        consent: { type: "object", additionalProperties: false, required: ["processing", "version"], properties: {
          processing: { const: true }, version: { const: config.consentVersion } } } } }, response: { 200: {
        type: "object", required: ["authorization_url"], properties: { authorization_url: { type: "string" } } } } },
    }, async (request, reply) => {
      if (!config.yandex) return reply.code(503).send({ detail: "oauth_not_configured" });
      if (!originAllowed(request, config)) return reply.code(403).send({ detail: "origin_forbidden" });
      const state = randomBytes(32).toString("base64url");
      await store.issueOAuthState(role, state, request.body.consent.version);
      reply.setCookie(oauthCookie, state, { ...oauthCookieOptions, maxAge: 600 });
      const url = new URL("https://oauth.yandex.ru/authorize");
      url.search = new URLSearchParams({ response_type: "code", client_id: config.yandex.clientId,
        redirect_uri: yandexCallback(config.yandex, role), scope: "login:email", state }).toString();
      return reply.header("cache-control", "no-store").send({ authorization_url: url.href });
    });
    app.get<{ Querystring: { state?: string; code?: string; error?: string } }>(`${root}/yandex/callback`, {
      // Authorization codes and state must not appear in Fastify request URL logs.
      logLevel: "silent",
      schema: { tags: ["auth"], querystring: { type: "object", properties: {
        state: { type: "string", maxLength: 128 }, code: { type: "string", maxLength: 4096 }, error: { type: "string", maxLength: 128 } } } },
    }, async (request, reply) => {
      reply.header("cache-control", "no-store").header("referrer-policy", "no-referrer");
      reply.clearCookie(oauthCookie, oauthCookieOptions);
      if (!config.yandex) return reply.code(503).send({ detail: "oauth_not_configured" });
      const destination = `${config.yandex.siteOrigin}/${role}/`;
      const failure = (reason: string) => reply.redirect(`${destination}#oauth-error=${reason}`);
      const { state, code, error } = request.query;
      if (!state || !/^[A-Za-z0-9_-]{43}$/.test(state) || state !== request.cookies[oauthCookie]) return failure("oauth_state_invalid");
      if (!await store.consumeOAuthState(role, state)) return failure("oauth_state_invalid");
      if (error || !code) return failure("oauth_denied");
      try {
        const identity = await yandexIdentity(config.yandex, role, code, options.oauthTransport);
        const session = await store.loginYandex(role, identity.subject, identity.email);
        const token = await signSession(config, role, session.account.id, session.session_id, session.expires_at);
        reply.setCookie(cookieName(role, config), token, { ...oauthCookieOptions, expires: new Date(session.expires_at) });
        return reply.redirect(destination);
      } catch (reason) {
        return failure(reason instanceof AuthError && ["oauth_email_conflict", "role_mismatch"].includes(reason.code)
          ? reason.code : "oauth_provider_unavailable");
      }
    });
    app.post<{ Body: { email: string } }>(`${root}/code`, {
      schema: { tags: ["auth"], body: { type: "object", additionalProperties: false, required: ["email"],
        properties: { email: { type: "string", minLength: 3, maxLength: 254 } } },
        response: { 202: { type: "object", required: ["challenge_id", "expires_at", "resend_after"],
          properties: { challenge_id: { type: "string", format: "uuid" }, expires_at: { type: "string" }, resend_after: { type: "string" }, dev_code: { type: "string", pattern: "^[0-9]{6}$" } } } } },
    }, async (request, reply) => {
      if (!originAllowed(request, config)) return reply.code(403).send({ detail: "origin_forbidden" });
      if (config.developmentCodeDelivery && process.env.NODE_ENV !== "development") return reply.code(503).send({ detail: "auth_not_configured" });
      if (!options.mailConfigured && !config.developmentCodeDelivery) return reply.code(503).send({ detail: "mail_not_configured" });
      const receipt = await store.issue(role, normalizeEmail(request.body.email), request.ip, config.developmentCodeDelivery);
      const { dev_code, ...publicReceipt } = receipt;
      return reply.code(202).header("cache-control", "no-store").send({ ...publicReceipt,
        ...(config.developmentCodeDelivery && dev_code ? { dev_code } : {}) });
    });
    app.post<{ Body: EmailCodeVerification }>(`${root}/verify`, {
      schema: { tags: ["auth"], body: { type: "object", additionalProperties: false,
        required: ["email", "challenge_id", "code"], properties: {
          email: { type: "string", minLength: 3, maxLength: 254 }, challenge_id: { type: "string", format: "uuid" },
          code: { type: "string", pattern: "^[0-9]{6}$" }, consent: { type: "object", additionalProperties: false,
            required: ["processing", "version"], properties: { processing: { const: true }, version: { const: config.consentVersion } } },
        } }, response: { 200: { type: "object", required: ["account", "access_token", "token_type", "expires_at"],
          properties: { account: accountSchema, access_token: { type: "string" }, token_type: { const: "Bearer" }, expires_at: { type: "string" } } } } },
    }, async (request, reply) => {
      if (!originAllowed(request, config)) return reply.code(403).send({ detail: "origin_forbidden" });
      const body = request.body;
      const session = await store.verify(role, normalizeEmail(body.email), body.challenge_id, body.code, body.consent?.version, request.ip);
      const token = await signSession(config, role, session.account.id, session.session_id, session.expires_at);
      reply.setCookie(cookieName(role, config), token, { path: "/", httpOnly: true, secure: config.secureCookies,
        sameSite: "lax", expires: new Date(session.expires_at) });
      return reply.header("cache-control", "no-store").send({ account: accountView(session.account), access_token: token,
        token_type: "Bearer", expires_at: session.expires_at });
    });
    app.get<{ Reply: unknown }>(`${root}/me`, { schema: { tags: ["auth"], security: [{ bearerAuth: [] }, { [`${role}Session`]: [] }],
      response: { 200: accountSchema } } }, async (request, reply) => {
      const identity = await resolveSession(request, role, config, store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      const account = await store.session(role, identity.accountId, identity.sessionId);
      if (!account) return reply.code(401).send({ detail: "unauthenticated" });
      return reply.header("cache-control", "no-store").send(accountView(account));
    });
    for (const all of [false, true]) {
      app.post<{ Reply: unknown }>(`${root}/${all ? "logout-all" : "logout"}`, { schema: { tags: ["auth"],
        security: [{ bearerAuth: [] }, { [`${role}Session`]: [] }], response: { 200: { type: "object", required: ["ok"], properties: { ok: { const: true } } } } } },
      async (request, reply) => {
        const identity = await resolveSession(request, role, config, store);
        if (!originAllowed(request, config, identity?.transport === "bearer")) return reply.code(403).send({ detail: "origin_forbidden" });
        if (identity) {
          if (all) await store.revokeAll(role, identity.accountId);
          else await store.revoke(role, identity.accountId, identity.sessionId);
        }
        reply.clearCookie(cookieName(role, config), { path: "/", httpOnly: true, secure: config.secureCookies, sameSite: "lax" });
        return reply.header("cache-control", "no-store").send({ ok: true });
      });
    }
  }
}

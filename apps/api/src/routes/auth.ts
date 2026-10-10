/** Email-code registration/login and revocable role-scoped sessions. */
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

/** Operator-only support console: reads every Comit conversation, replies as Comit. */
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { SupportLoginRequest, SystemChatMessageRequest } from "@comit/shared";
import type { AuthConfig } from "../auth/config.js";
import { cookieName, originAllowed } from "../auth/session.js";
import { SystemNotificationsError, type SystemNotificationsStore } from "../notifications/system-store.js";
import { systemNotificationSchema, systemChatMessageSchema } from "../notifications/schemas.js";
import type { SupportConfig } from "../support/config.js";
import type { SupportConsoleStore } from "../support/store.js";
import { supportLoginRequestSchema, supportSessionSchema, supportOperatorSchema, supportConversationPageSchema,
  supportMessagePageSchema, supportReadRequestSchema, supportReadStatusSchema } from "../support/schemas.js";

/** Inject persistence and the shared Comit conversation producer. */
export interface SupportOptions {
  config: AuthConfig; support: SupportConfig; sessions: SupportConsoleStore; notifications: SystemNotificationsStore;
}
/** Cookie name mirrors account sessions; __Host- is added for HTTPS deployments. */
export const supportCookieName = (config: AuthConfig): string => `${config.secureCookies ? "__Host-" : ""}comit_support_session`;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const LOGIN_WINDOW_MS = 15 * 60_000, LOGIN_MAX_FAILURES = 10;

/** Register operator routes only when the console is configured; absent config means absent surface. */
export async function supportRoutes(app: FastifyInstance, options: SupportOptions): Promise<void> {
  const { config, support } = options;
  const cookie = supportCookieName(config);
  const identities = new WeakMap<FastifyRequest, string>();
  const failures = new Map<string, { count: number; until: number }>();
  const clientKey = (request: FastifyRequest): string => request.ip;
  const throttled = (key: string): boolean => {
    const entry = failures.get(key);
    return entry !== undefined && entry.count >= LOGIN_MAX_FAILURES && entry.until > Date.now();
  };
  const recordFailure = (key: string): void => {
    const entry = failures.get(key);
    if (entry === undefined || entry.until <= Date.now()) failures.set(key, { count: 1, until: Date.now() + LOGIN_WINDOW_MS });
    else { entry.count += 1; entry.until = Date.now() + LOGIN_WINDOW_MS; }
  };
  const fail = (reply: FastifyReply, status: number, code: string) => reply.code(status).send({ detail: code });
  const guard = async (request: FastifyRequest, reply: FastifyReply): Promise<void> => {
    reply.header("cache-control", "no-store");
    const authorization = request.headers.authorization;
    const token = authorization !== undefined ? /^Bearer ([^\s]+)$/.exec(authorization)?.[1] : request.cookies[cookie];
    if (!token || token.length > 4096) return void (await fail(reply, 401, "unauthenticated"));
    let live = false;
    try { live = await options.sessions.session(token); } catch { return void (await fail(reply, 503, "support_unavailable")); }
    if (!live) return void (await fail(reply, 401, "unauthenticated"));
    if (request.method !== "GET" && !originAllowed(request, config, authorization !== undefined)) {
      return void (await fail(reply, 403, "origin_forbidden"));
    }
    identities.set(request, token);
  };
  const operator = (request: FastifyRequest): string | null => (identities.has(request) ? support.email : null);
  const conversationId = (request: FastifyRequest<{ Params: { account_id?: string } }>): string | null => {
    const id = request.params.account_id;
    return id && UUID.test(id) ? id : null;
  };

  app.post<{ Body: SupportLoginRequest }>("/support/login", {
    schema: { tags: ["support"], security: [], body: supportLoginRequestSchema, response: { 200: supportSessionSchema,
      401: { type: "object", required: ["detail"], properties: { detail: { type: "string" } } } } },
  }, async (request, reply) => {
    reply.header("cache-control", "no-store");
    if (!originAllowed(request, config)) return fail(reply, 403, "origin_forbidden");
    const key = clientKey(request);
    if (throttled(key)) { reply.header("retry-after", "60"); return fail(reply, 429, "support_login_throttled"); }
    let session;
    try { session = await options.sessions.login(String(request.body.email).toLowerCase(), request.body.access_key, support); }
    catch { return fail(reply, 503, "support_unavailable"); }
    if (!session) { recordFailure(key); return fail(reply, 401, "support_login_rejected"); }
    failures.delete(key);
    reply.setCookie(cookie, session.token, { path: "/", httpOnly: true, secure: config.secureCookies,
      sameSite: "lax", expires: new Date(session.expires_at) });
    return reply.send({ operator: { email: support.email }, expires_at: session.expires_at });
  });

  app.get("/support/me", { schema: { tags: ["support"], security: [{ supportSession: [] }], response: { 200: supportOperatorSchema } }, onRequest: guard },
    async (request, reply) => reply.send({ email: operator(request) }));

  app.post("/support/logout", { schema: { tags: ["support"], security: [{ supportSession: [] }], response: { 200: { type: "object", required: ["ok"], properties: { ok: { const: true } } } } }, onRequest: guard },
    async (request, reply) => {
      const token = identities.get(request);
      if (token) await options.sessions.revoke(token);
      reply.clearCookie(cookie, { path: "/" });
      return reply.send({ ok: true });
    });

  app.get<{ Querystring: { offset?: string; limit?: string } }>("/support/conversations", {
    schema: { tags: ["support"], security: [{ supportSession: [] }], querystring: { type: "object", additionalProperties: false,
      properties: { offset: { type: "string", pattern: "^(?:0|[1-9][0-9]{0,7})$" }, limit: { type: "string", pattern: "^(?:[1-9]|[1-9][0-9]|100)$" } } },
      response: { 200: supportConversationPageSchema } }, onRequest: guard },
  async (request, reply) => {
    try { return reply.send(await options.sessions.conversations(Number(request.query.offset ?? 0), Number(request.query.limit ?? 50))); }
    catch (error) { return error instanceof SystemNotificationsError ? fail(reply, error.status, error.code) : fail(reply, 503, "support_unavailable"); }
  });

  app.get<{ Params: { account_id?: string }; Querystring: { after_sequence?: string; limit?: string } }>("/support/conversations/:account_id/messages", {
    schema: { tags: ["support"], security: [{ supportSession: [] }], params: { type: "object", additionalProperties: false,
      required: ["account_id"], properties: { account_id: { type: "string", format: "uuid" } } },
      querystring: { type: "object", additionalProperties: false, properties: {
        after_sequence: { type: "string", pattern: "^(?:0|[1-9][0-9]{0,15})$" }, limit: { type: "string", pattern: "^(?:[1-9]|[1-9][0-9]|100)$" } } },
      response: { 200: supportMessagePageSchema } }, onRequest: guard },
  async (request, reply) => {
    const accountId = conversationId(request);
    if (!accountId) return fail(reply, 404, "account_not_found");
    try { return reply.send(await options.sessions.messages(accountId, Number(request.query.after_sequence ?? 0), Number(request.query.limit ?? 100))); }
    catch (error) { return error instanceof SystemNotificationsError ? fail(reply, error.status, error.code) : fail(reply, 503, "support_unavailable"); }
  });

  app.post<{ Params: { account_id?: string }; Body: SystemChatMessageRequest }>("/support/conversations/:account_id/messages", {
    schema: { tags: ["support"], security: [{ supportSession: [] }], params: { type: "object", additionalProperties: false,
      required: ["account_id"], properties: { account_id: { type: "string", format: "uuid" } } },
      body: systemChatMessageSchema, response: { 200: systemNotificationSchema } }, onRequest: guard },
  async (request, reply) => {
    const accountId = conversationId(request);
    if (!accountId) return fail(reply, 404, "account_not_found");
    try {
      // Account-scoped idempotency: the prefixed key cannot collide with domain producers.
      const item = await options.notifications.publish(accountId, `support-reply:${request.body.idempotency_key}`, "support.reply", request.body.text);
      return reply.send(item);
    } catch (error) { return error instanceof SystemNotificationsError ? fail(reply, error.status, error.code) : fail(reply, 503, "support_unavailable"); }
  });

  app.post<{ Params: { account_id?: string }; Body: { read_sequence: number } }>("/support/conversations/:account_id/read", {
    schema: { tags: ["support"], security: [{ supportSession: [] }], params: { type: "object", additionalProperties: false,
      required: ["account_id"], properties: { account_id: { type: "string", format: "uuid" } } },
      body: supportReadRequestSchema, response: { 200: supportReadStatusSchema } }, onRequest: guard },
  async (request, reply) => {
    const accountId = conversationId(request);
    if (!accountId) return fail(reply, 404, "account_not_found");
    try { return reply.send(await options.sessions.markRead(accountId, request.body.read_sequence)); }
    catch (error) { return error instanceof SystemNotificationsError ? fail(reply, error.status, error.code) : fail(reply, 503, "support_unavailable"); }
  });
}

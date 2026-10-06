/** Authenticate every chat call in Comit; private QGramm identities and credentials never reach clients. */
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { AccountRole, SendChatMessage, UploadChatFile } from "@comit/shared";
import { originAllowed, resolveSession, type SessionIdentity } from "../auth/session.js";
import { RecruitmentError } from "../recruitment/store.js";
import type { RecruitmentOptions } from "./recruitment.js";
import { chatPageSchema, chatMessageSchema, chatFileSchema, chatSendSchema, chatUploadSchema, chatDownloadSchema } from "../chat/schemas.js";

/** Explicit optional chat wiring; missing configuration returns 503 without creating fake history. */
export interface ChatOptions extends RecruitmentOptions { chatUrl?: string; chatToken?: string; chatTransport?: typeof fetch }

/** Load a private HTTP(S) endpoint and sufficiently long token; malformed partial config fails closed. */
export function chatConfiguration(env: NodeJS.ProcessEnv = process.env): { url: string; token: string } | null {
  if (!env.CHAT_SERVICE_URL || !env.CHAT_SERVICE_TOKEN || env.CHAT_SERVICE_TOKEN.length < 32) return null;
  try {
    const url = new URL(env.CHAT_SERVICE_URL);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/") return null;
    return { url: url.origin, token: env.CHAT_SERVICE_TOKEN };
  } catch { return null; }
}

/** Register role-specific OpenAPI routes for messages and encrypted attachments in invited processes. */
export async function chatRoutes(app: FastifyInstance, options: ChatOptions): Promise<void> {
  const config = chatConfiguration();
  const chatUrl = options.chatUrl ?? config?.url, chatToken = options.chatToken ?? config?.token;
  const transport = options.chatTransport ?? fetch;
  // Production shares a ten-connection pool with accounts/profiles: chat may retain at most three clients.
  let activeOperations = 0;
  const identities = new WeakMap<FastifyRequest, SessionIdentity>();
  const params = { type: "object", additionalProperties: false, required: ["id"], properties: { id: { type: "string", format: "uuid" } } };
  const guard = (role: AccountRole) => async (request: FastifyRequest, reply: FastifyReply) => {
    reply.header("cache-control", "no-store").header("x-content-type-options", "nosniff");
    try {
      const identity = await resolveSession(request, role, options.config, options.store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      if (request.method !== "GET" && !originAllowed(request, options.config, identity.transport === "bearer")) return reply.code(403).send({ detail: "origin_forbidden" });
      identities.set(request, identity);
    } catch { return reply.code(503).send({ detail: "auth_unavailable" }); }
  };
  const invoke = async (request: FastifyRequest<{ Params: { id: string } }>, reply: FastifyReply,
    role: AccountRole, operation: string, payload: object, schema: Record<string, unknown>, write: boolean) => {
    if (activeOperations >= 3) return reply.header("retry-after", "1").code(429).send({ detail: "chat_busy" });
    activeOperations++;
    try {
      const identity = identities.get(request);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      const result = await options.recruitment.withChatGrant(role, identity.accountId, request.params.id, write, async grant => {
        if (!chatUrl || !chatToken) throw new RecruitmentError(503, "chat_not_configured");
        const response = await transport(new URL(`/internal/chat/${operation}`, chatUrl), {
          method: "POST", headers: { "content-type": "application/json", "x-internal-token": chatToken },
          body: JSON.stringify({ ...payload, ...grant }), signal: AbortSignal.timeout(15000), redirect: "error",
        });
        if (!response.ok) {
          const status = response.status === 403 ? 404 : [400, 404, 409, 413, 422, 429, 507].includes(response.status) ? response.status : 503;
          throw new RecruitmentError(status, status === 409 ? "chat_idempotency_conflict" : status === 404 ? "chat_resource_not_found" : "chat_unavailable");
        }
        // Bound even a faulty upstream body before parsing; never expose its error text or credentials.
        const reader = response.body?.getReader();
        if (!reader) throw new RecruitmentError(503, "chat_invalid_response");
        const chunks: Uint8Array[] = [];
        let size = 0;
        while (true) {
          const item = await reader.read();
          if (item.done) break;
          size += item.value.byteLength;
          if (size > 12 * 1024 * 1024) { await reader.cancel(); throw new RecruitmentError(503, "chat_invalid_response"); }
          chunks.push(item.value);
        }
        const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        if (!request.compileValidationSchema(schema)(parsed)) throw new RecruitmentError(503, "chat_invalid_response");
        return parsed;
      });
      return reply.send(result);
    } catch (error) {
      if (error instanceof RecruitmentError) return reply.code(error.status).send({ detail: error.code });
      return reply.code(503).send({ detail: "chat_unavailable" });
    } finally { activeOperations--; }
  };
  for (const role of ["candidate", "employer"] as const) {
    const security = [{ bearerAuth: [] }, { [role === "candidate" ? "candidateSession" : "employerSession"]: [] }];
    const base = `/${role}/recruitment/:id/chat`;
    app.get<{ Params: { id: string }; Querystring: { after_sequence?: string; limit?: string } }>(`${base}/messages`, {
      schema: { tags: ["chat"], security, params, querystring: { type: "object", additionalProperties: false, properties: {
        after_sequence: { type: "string", pattern: "^(?:0|[1-9][0-9]{0,14})$" },
        limit: { type: "string", pattern: "^(?:[1-9]|[1-9][0-9]|100)$" },
      } }, response: { 200: chatPageSchema } }, onRequest: guard(role),
    }, (request, reply) => invoke(request, reply, role, "history", { after_sequence: Number(request.query.after_sequence ?? 0), limit: Number(request.query.limit ?? 50) }, chatPageSchema, false));
    app.post<{ Params: { id: string }; Body: SendChatMessage }>(`${base}/messages`, {
      schema: { tags: ["chat"], security, params, body: chatSendSchema, response: { 200: chatMessageSchema } }, onRequest: guard(role),
    }, (request, reply) => invoke(request, reply, role, "send", request.body, chatMessageSchema, true));
    app.post<{ Params: { id: string }; Body: UploadChatFile }>(`${base}/files`, {
      bodyLimit: 12 * 1024 * 1024,
      schema: { tags: ["chat"], security, params, body: chatUploadSchema, response: { 200: chatFileSchema } }, onRequest: guard(role),
    }, (request, reply) => invoke(request, reply, role, "upload", request.body, chatFileSchema, true));
    app.get<{ Params: { id: string; file_id: string } }>(`${base}/files/:file_id`, {
      schema: { tags: ["chat"], security, params: { ...params, required: ["id", "file_id"], properties: { ...params.properties, file_id: params.properties.id } },
        response: { 200: chatDownloadSchema } }, onRequest: guard(role),
    }, (request, reply) => invoke(request, reply, role, "download", { file_id: request.params.file_id }, chatDownloadSchema, false));
  }
}

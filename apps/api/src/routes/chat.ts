/** Authenticate every chat call in Comit; private QGramm identities and credentials never reach clients. */
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { AccountRole, ChatEventPage, SendChatMessage, UploadChatFile, ChatReadRequest } from "@comit/shared";
import { originAllowed, resolveSession, type SessionIdentity } from "../auth/session.js";
import { RecruitmentError } from "../recruitment/store.js";
import type { RecruitmentOptions } from "./recruitment.js";
import { chatPageSchema, chatMessageSchema, chatFileSchema, chatSendSchema, chatUploadSchema, chatDownloadSchema, chatReadSchema, chatReadStatusSchema } from "../chat/schemas.js";
import { correlatedFetch } from "../operations/index.js";
import { serveStream, streamCursor } from "../chat/sse.js";

/** Explicit optional chat wiring; missing configuration returns 503 without creating fake history. */
export interface ChatOptions extends RecruitmentOptions {
  chatUrl?: string; chatToken?: string; chatTransport?: typeof fetch;
  streamLifetimeMs?: number; streamPollMs?: number;
}

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
  const transport = correlatedFetch(options.chatTransport ?? fetch);
  // Production shares a ten-connection pool with accounts/profiles: chat may retain at most three clients.
  let activeOperations = 0;
  const streams = new Map<string, number>();
  const enterStream = (actorId: string): boolean => {
    if ([...streams.values()].reduce((total, value) => total + value, 0) >= 32 || (streams.get(actorId) ?? 0) >= 2) return false;
    streams.set(actorId, (streams.get(actorId) ?? 0) + 1);
    return true;
  };
  const leaveStream = (actorId: string) => {
    const remaining = (streams.get(actorId) ?? 1) - 1;
    if (remaining) streams.set(actorId, remaining); else streams.delete(actorId);
  };
  const shutdown = new AbortController();
  app.addHook("preClose", async () => { shutdown.abort(); });
  const lifetimeMs = Math.min(60000, options.streamLifetimeMs ?? 60000);
  const pollMs = options.streamPollMs ?? 1000;
  if (lifetimeMs < 1 || pollMs < 1) throw new Error("invalid_stream_configuration");
  const eventPageSchema = { type: "object", additionalProperties: false, required: ["events", "next_sequence"], properties: {
    next_sequence: { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER }, events: { type: "array", maxItems: 100, items: { oneOf: [
      { type: "object", additionalProperties: false, required: ["sequence", "type", "message"], properties: {
        sequence: { type: "integer", minimum: 1, maximum: Number.MAX_SAFE_INTEGER }, type: { const: "message.created" }, message: chatMessageSchema } },
      { type: "object", additionalProperties: false, required: ["sequence", "type"], properties: {
        sequence: { type: "integer", minimum: 1, maximum: Number.MAX_SAFE_INTEGER }, type: { const: "cursor" } } },
    ] } } } };
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
    app.get<{ Querystring: { after_sequence?: string }; Reply: unknown }>(`/${role}/recruitment-events/stream`, {
      schema: { tags: ["recruitment"], security, querystring: { type: "object", additionalProperties: false,
        properties: { after_sequence: { type: "string", pattern: "^(?:0|[1-9][0-9]{0,15})$" } } },
        headers: { type: "object", properties: { "last-event-id": { type: "string", maxLength: 16 } } },
        produces: ["text/event-stream"], response: { 200: { type: "string" } } }, onRequest: guard(role),
    }, async (request, reply) => {
      const identity = identities.get(request);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      let cursor: number;
      try { cursor = streamCursor(request.headers["last-event-id"], request.query.after_sequence); }
      catch { return reply.code(400).send({ detail: "recruitment_cursor_invalid" }); }
      if (!enterStream(identity.accountId)) return reply.code(429).header("retry-after", "1").send({ detail: "chat_stream_busy" });
      const poll = async (after: number) => {
        if (activeOperations >= 3) throw new RecruitmentError(429, "chat_busy");
        activeOperations++;
        try {
          const current = await resolveSession(request, role, options.config, options.store);
          if (!current || current.accountId !== identity.accountId) throw new RecruitmentError(401, "session_revoked");
          const page = await options.recruitment.events(role, identity.accountId, after, 100);
          if (!Number.isSafeInteger(page.next_sequence) || page.next_sequence < after) throw new RecruitmentError(503, "recruitment_invalid_response");
          let previous = after;
          for (const item of page.items) {
            if (!Number.isSafeInteger(item.sequence) || item.sequence <= previous) throw new RecruitmentError(503, "recruitment_invalid_response");
            previous = item.sequence;
          }
          return { events: page.items.map(event => ({ sequence: event.sequence, type: "recruitment.changed", event })), next_sequence: page.next_sequence };
        } finally { activeOperations--; }
      };
      try {
        const initial = await poll(cursor);
        await serveStream(request, reply, initial, poll, { lifetimeMs, pollMs, signal: shutdown.signal, afterSequence: cursor }, error =>
          error instanceof RecruitmentError ? { status: error.status, code: error.code } : { status: 503, code: "recruitment_unavailable" });
      } catch (error) {
        if (error instanceof RecruitmentError) return reply.code(error.status).send({ detail: error.code });
        return reply.code(503).send({ detail: "recruitment_unavailable" });
      } finally { leaveStream(identity.accountId); }
    });
    app.get<{ Params: { id: string }; Querystring: { after_sequence?: string }; Reply: unknown }>(`${base}/events`, {
      schema: { tags: ["chat"], security, params, querystring: { type: "object", additionalProperties: false,
        properties: { after_sequence: { type: "string", pattern: "^(?:0|[1-9][0-9]{0,15})$" } } },
        headers: { type: "object", properties: { "last-event-id": { type: "string", maxLength: 16 } } },
        produces: ["text/event-stream"], response: { 200: { type: "string" } } }, onRequest: guard(role),
    }, async (request, reply) => {
      const identity = identities.get(request);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      let cursor: number;
      try { cursor = streamCursor(request.headers["last-event-id"], request.query.after_sequence); }
      catch { return reply.code(400).send({ detail: "chat_cursor_invalid" }); }
      if (!enterStream(identity.accountId)) {
        return reply.code(429).header("retry-after", "1").send({ detail: "chat_stream_busy" });
      }
      const poll = async (after: number, signal: AbortSignal = shutdown.signal): Promise<ChatEventPage> => {
        if (activeOperations >= 3) throw new RecruitmentError(429, "chat_busy");
        activeOperations++;
        try {
          const current = await resolveSession(request, role, options.config, options.store);
          if (!current || current.accountId !== identity.accountId) throw new RecruitmentError(401, "chat_session_revoked");
          return await options.recruitment.withChatGrant(role, identity.accountId, request.params.id, false, async grant => {
            if (!chatUrl || !chatToken) throw new RecruitmentError(503, "chat_not_configured");
            const response = await transport(new URL("/internal/chat/events", chatUrl), { method: "POST",
              headers: { "content-type": "application/json", "x-internal-token": chatToken },
              body: JSON.stringify({ ...grant, after_sequence: after, limit: 100 }),
              signal: AbortSignal.any([shutdown.signal, signal, AbortSignal.timeout(5000)]), redirect: "error" });
            if (!response.ok) throw new RecruitmentError(response.status === 410 ? 410 : response.status === 400 ? 400 : response.status === 429 ? 429 : 503,
              response.status === 410 ? "chat_resync_required" : response.status === 400 ? "chat_cursor_invalid" : "chat_unavailable");
            const reader = response.body?.getReader();
            if (!reader) throw new RecruitmentError(503, "chat_invalid_response");
            const chunks: Uint8Array[] = []; let size = 0;
            while (true) {
              const part = await reader.read(); if (part.done) break;
              size += part.value.byteLength;
              if (size > 4 * 1024 * 1024) { await reader.cancel(); throw new RecruitmentError(503, "chat_invalid_response"); }
              chunks.push(part.value);
            }
            const page: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
            if (!request.compileValidationSchema(eventPageSchema)(page)) throw new RecruitmentError(503, "chat_invalid_response");
            const result = page as ChatEventPage;
            let previous = after;
            for (const event of result.events) {
              if (event.sequence <= previous || event.type === "message.created" && ![grant.owner_id, grant.candidate_id].includes(event.message.sender_id)) throw new RecruitmentError(503, "chat_invalid_response");
              previous = event.sequence;
            }
            if (result.next_sequence < previous) throw new RecruitmentError(503, "chat_invalid_response");
            return result;
          });
        } finally { activeOperations--; }
      };
      try {
        const initial = await poll(cursor);
        await serveStream(request, reply, initial, poll, { lifetimeMs, pollMs, signal: shutdown.signal, afterSequence: cursor }, error =>
          error instanceof RecruitmentError ? { status: error.status, code: error.code } : { status: 503, code: "chat_unavailable" });
      } catch (error) {
        if (error instanceof RecruitmentError) return reply.code(error.status).send({ detail: error.code });
        return reply.code(503).send({ detail: "chat_unavailable" });
      } finally {
        leaveStream(identity.accountId);
      }
    });
    app.get<{ Params: { id: string }; Querystring: { after_sequence?: string; limit?: string } }>(`${base}/messages`, {
      schema: { tags: ["chat"], security, params, querystring: { type: "object", additionalProperties: false, properties: {
        after_sequence: { type: "string", pattern: "^(?:0|[1-9][0-9]{0,14})$" },
        limit: { type: "string", pattern: "^(?:[1-9]|[1-9][0-9]|100)$" },
      } }, response: { 200: chatPageSchema } }, onRequest: guard(role),
    }, (request, reply) => invoke(request, reply, role, "history", { after_sequence: Number(request.query.after_sequence ?? 0), limit: Number(request.query.limit ?? 50) }, chatPageSchema, false));
    app.post<{ Params: { id: string }; Body: ChatReadRequest }>(`${base}/read`, {
      schema: { tags: ["chat"], security, params, body: chatReadSchema, response: { 200: chatReadStatusSchema } }, onRequest: guard(role),
    }, (request, reply) => invoke(request, reply, role, "read", request.body, chatReadStatusSchema, false));
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

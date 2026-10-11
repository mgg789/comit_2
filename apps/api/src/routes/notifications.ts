/** Revocable account sessions expose the Comit conversation: inbox, receipts and own messages. */
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { AccountRole, SystemChatFileDownload, SystemChatFileUpload, SystemChatReadRequest, SystemChatMessageRequest } from "@comit/shared";
import type { AuthOptions } from "./auth.js";
import { resolveSession, originAllowed, type SessionIdentity } from "../auth/session.js";
import { correlatedFetch } from "../operations/index.js";
import { chatConfiguration } from "./chat.js";
import { SystemNotificationsError, type SystemNotificationsStore } from "../notifications/system-store.js";
import { systemChatPageSchema, systemReadRequestSchema, systemReadStatusSchema, systemChatMessageSchema, systemNotificationSchema, chatUnreadSchema, systemChatFileUploadSchema, systemChatFileSchema, systemChatFileDownloadSchema } from "../notifications/schemas.js";
/** Inject persistence and the existing private chat metadata transport. */
export interface NotificationOptions extends AuthOptions {
  notifications: SystemNotificationsStore; chatUrl?: string; chatToken?: string; chatTransport?: typeof fetch;
}
/** Register owned inbox/read, own Comit messages and all-chat counters for both account roles. */
export async function notificationRoutes(app: FastifyInstance, options: NotificationOptions): Promise<void> {
  const identities = new WeakMap<FastifyRequest, SessionIdentity>();
  const configured = chatConfiguration(), url = options.chatUrl ?? configured?.url, token = options.chatToken ?? configured?.token;
  const configuration = url && token ? { url, token } : null;
  const transport = correlatedFetch(options.chatTransport ?? fetch);
  const guard = (role: AccountRole) => async (request: FastifyRequest, reply: FastifyReply) => {
    reply.header("cache-control", "no-store");
    try {
      const identity = await resolveSession(request, role, options.config, options.store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      if (request.method !== "GET" && !originAllowed(request, options.config, identity.transport === "bearer"))
        return reply.code(403).send({ detail: "origin_forbidden" });
      identities.set(request, identity);
    } catch { return reply.code(503).send({ detail: "auth_unavailable" }); }
  };
  const perform = async (request: FastifyRequest, reply: FastifyReply, operation: (accountId: string) => Promise<unknown>) => {
    const identity = identities.get(request);
    if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
    try { return reply.send(await operation(identity.accountId)); }
    catch (error) {
      return error instanceof SystemNotificationsError ? reply.code(error.status).send({ detail: error.code })
        : reply.code(503).send({ detail: "system_inbox_unavailable" });
    }
  };
  for (const role of ["employer", "candidate"] as const) {
    const security = [{ bearerAuth: [] }, { [role === "employer" ? "employerSession" : "candidateSession"]: [] }];
    app.get<{ Querystring: { after_sequence?: string; limit?: string } }>(`/${role}/system-chat`, {
      schema: { tags: ["system-inbox"], security, querystring: { type: "object", additionalProperties: false, properties: {
        after_sequence: { type: "string", pattern: "^(?:0|[1-9][0-9]{0,15})$" }, limit: { type: "string", pattern: "^(?:[1-9]|[1-9][0-9]|100)$" },
      } }, response: { 200: systemChatPageSchema } }, onRequest: guard(role),
    }, (request, reply) => perform(request, reply, id => options.notifications.page(role, id, Number(request.query.after_sequence ?? 0), Number(request.query.limit ?? 50))));
    app.post<{ Body: SystemChatReadRequest }>(`/${role}/system-chat/read`, {
      schema: { tags: ["system-inbox"], security, body: systemReadRequestSchema, response: { 200: systemReadStatusSchema } }, onRequest: guard(role),
    }, (request, reply) => perform(request, reply, id => options.notifications.read(role, id, request.body.read_sequence)));
    app.post<{ Body: SystemChatMessageRequest }>(`/${role}/system-chat/messages`, {
      schema: { tags: ["system-inbox"], security, body: systemChatMessageSchema, response: { 200: systemNotificationSchema } }, onRequest: guard(role),
    }, (request, reply) => perform(request, reply, id => options.notifications.sendMessage(role, id, `support-user:${request.body.idempotency_key}`, request.body.text || " ", request.body.files ?? [])));
    app.post<{ Body: SystemChatFileUpload }>(`/${role}/system-chat/files`, {
      bodyLimit: 12 * 1024 * 1024,
      schema: { tags: ["system-inbox"], security, body: systemChatFileUploadSchema, response: { 200: systemChatFileSchema } }, onRequest: guard(role),
    }, (request, reply) => perform(request, reply, id => options.notifications.uploadFile(id, request.body)));
    app.get<{ Params: { file_id: string } }>(`/${role}/system-chat/files/:file_id`, {
      schema: { tags: ["system-inbox"], security, params: { type: "object", additionalProperties: false, required: ["file_id"], properties: { file_id: { type: "string", format: "uuid" } } }, response: { 200: systemChatFileDownloadSchema } }, onRequest: guard(role),
    }, (request, reply) => perform(request, reply, async id => {
      reply.header("x-content-type-options", "nosniff").header("cache-control", "private, no-store").header("content-disposition", "attachment; filename=\"support-attachment.json\"");
      return options.notifications.downloadFile(id, request.params.file_id) as Promise<SystemChatFileDownload>;
    }));
    app.get(`/${role}/chat-unread`, { schema: { tags: ["system-inbox"], security, response: { 200: chatUnreadSchema } }, onRequest: guard(role) },
      (request, reply) => perform(request, reply, id => options.notifications.unread(role, id, configuration, transport)));
  }
}

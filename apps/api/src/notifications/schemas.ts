/** OpenAPI schemas for the permanent account-scoped system chat. */
const integer = { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER };
const nullable = (schema: object) => ({ anyOf: [schema, { type: "null" }] });
const uuid = { type: "string", format: "uuid" };
export const systemChatFileSchema = { type: "object", additionalProperties: false, required: ["id", "name", "media_type", "size", "sha256"], properties: {
  id: uuid, name: { type: "string", minLength: 1, maxLength: 255, pattern: "^[^\\u0000-\\u001f\\u007f/\\\\]+$" },
  media_type: { type: "string", minLength: 3, maxLength: 127 }, size: { type: "integer", minimum: 1, maximum: 8388608 }, sha256: { type: "string", pattern: "^[a-f0-9]{64}$" },
} };
const systemChatFiles = { type: "array", maxItems: 8, uniqueItems: true, items: systemChatFileSchema };
/** Notification navigation hints never confer permission to their target. */
export const systemNotificationSchema = { type: "object", additionalProperties: false,
  required: ["id", "sequence", "type", "text", "created_at", "target", "files"], properties: {
    id: { type: "string", format: "uuid" }, sequence: { ...integer, minimum: 1 }, type: { type: "string" }, text: { type: "string" },
    created_at: { type: "string", format: "date-time" }, target: nullable({ type: "object", additionalProperties: false,
      required: ["kind", "path"], properties: { kind: { enum: ["recruitment", "vacancy", "candidate_profile", "settings"] },
        id: { type: "string", format: "uuid" }, path: { type: "string", pattern: "^/(?!/)[a-zA-Z0-9/_-]*$" } } }), files: systemChatFiles,
  } };
/** Read requests are bounded later by the actual durable delivery watermark. */
export const systemReadRequestSchema = { type: "object", additionalProperties: false, required: ["read_sequence"], properties: { read_sequence: integer } };
/** Shared message body for writing into the Comit conversation: UUID retry key plus bounded text or attachments. */
export const systemChatMessageSchema = { type: "object", additionalProperties: false,
  required: ["idempotency_key"], properties: { idempotency_key: { type: "string", format: "uuid" }, text: { type: "string", maxLength: 2000 }, files: systemChatFiles },
  anyOf: [{ required: ["text"], properties: { text: { type: "string", minLength: 1 } } }, { required: ["files"], properties: { files: { type: "array", minItems: 1 } } }] };
export const systemChatFileUploadSchema = { type: "object", additionalProperties: false, required: ["idempotency_key", "name", "media_type", "data_base64"], properties: {
  idempotency_key: uuid, name: systemChatFileSchema.properties.name, media_type: { type: "string", minLength: 3, maxLength: 127 },
  data_base64: { type: "string", minLength: 4, maxLength: 11184812, pattern: "^[A-Za-z0-9+/]+={0,2}$" },
} };
export const systemChatFileDownloadSchema = { type: "object", additionalProperties: false, required: ["id", "name", "media_type", "size", "sha256", "data_base64"], properties: {
  ...systemChatFileSchema.properties, data_base64: systemChatFileUploadSchema.properties.data_base64,
} };
/** Monotonic receipt and exact durable unread count. */
export const systemReadStatusSchema = { type: "object", additionalProperties: false, required: ["read_sequence", "unread_count"], properties: { read_sequence: integer, unread_count: integer } };
/** Cursor pages preserve the stable Comit system chat identity. */
export const systemChatPageSchema = { type: "object", additionalProperties: false,
  required: ["id", "title", "items", "next_sequence", "read_sequence", "unread_count", "has_more"], properties: {
    id: { const: "comit-system" }, title: { const: "Comit" }, items: { type: "array", items: systemNotificationSchema },
    next_sequence: integer, read_sequence: integer, unread_count: integer, has_more: { type: "boolean" },
  } };
/** Private chat outages remain explicit instead of showing a misleading zero badge. */
export const chatUnreadSchema = { type: "object", additionalProperties: false,
  required: ["system_unread_count", "recruitment_unread_count", "total_unread_count"], properties: {
    system_unread_count: integer, recruitment_unread_count: nullable(integer), total_unread_count: nullable(integer),
  } };

/** Strict public chat contracts: bounded JSON uploads and process-local durable history. */
const uuid = { type: "string", format: "uuid" };
const integer = { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER };
export const chatFileSchema = { type: "object", additionalProperties: false,
  required: ["id", "name", "media_type", "size", "sha256"], properties: {
    id: uuid, name: { type: "string", minLength: 1, maxLength: 255, pattern: "^[^\\u0000-\\u001f/\\\\]+$" },
    media_type: { type: "string", minLength: 3, maxLength: 127, pattern: "^[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+$" },
    size: { type: "integer", minimum: 1, maximum: 8388608 }, sha256: { type: "string", pattern: "^[a-f0-9]{64}$" },
  } };
export const chatMessageSchema = { type: "object", additionalProperties: false,
  required: ["id", "sequence", "sender_id", "created_at", "text", "files"], properties: {
    id: uuid, sequence: integer, sender_id: uuid, created_at: { type: "string", format: "date-time" },
    text: { type: "string", maxLength: 16000 }, files: { type: "array", maxItems: 8, items: chatFileSchema },
  } };
export const chatPageSchema = { type: "object", additionalProperties: false,
  required: ["messages", "next_sequence"], properties: {
    messages: { type: "array", maxItems: 100, items: chatMessageSchema }, next_sequence: integer,
  } };
export const chatSendSchema = { type: "object", additionalProperties: false,
  required: ["idempotency_key", "text", "files"], properties: {
    idempotency_key: uuid, text: chatMessageSchema.properties.text,
    files: { type: "array", maxItems: 8, uniqueItems: true, items: chatFileSchema },
  }, anyOf: [{ properties: { text: { type: "string", minLength: 1, pattern: "\\S" } } }, { properties: { files: { type: "array", minItems: 1 } } }] };
export const chatUploadSchema = { type: "object", additionalProperties: false,
  required: ["idempotency_key", "name", "media_type", "data_base64"], properties: {
    idempotency_key: uuid, name: chatFileSchema.properties.name, media_type: chatFileSchema.properties.media_type,
    data_base64: { type: "string", minLength: 4, maxLength: 11184812,
      pattern: "^[A-Za-z0-9+/]+={0,2}$" },
  } };
export const chatDownloadSchema = { ...chatFileSchema,
  required: [...chatFileSchema.required, "data_base64"], properties: {
    ...chatFileSchema.properties, data_base64: chatUploadSchema.properties.data_base64,
  } };

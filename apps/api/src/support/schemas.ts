/** OpenAPI schemas for the operator-side support console. */
import { systemNotificationSchema, systemChatMessageSchema } from "../notifications/schemas.js";

const integer = { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER };
const operatorEmail = { type: "string", format: "email" };
/** Login body; both values are compared in constant time and never logged. */
export const supportLoginRequestSchema = { type: "object", additionalProperties: false,
  required: ["email", "access_key"], properties: { email: operatorEmail, access_key: { type: "string", minLength: 1, maxLength: 200 } } };
export const supportSessionSchema = { type: "object", additionalProperties: false,
  required: ["operator", "expires_at"], properties: {
    operator: { type: "object", additionalProperties: false, required: ["email"], properties: { email: operatorEmail } },
    expires_at: { type: "string", format: "date-time" },
  } };
export const supportOperatorSchema = { type: "object", additionalProperties: false, required: ["email"], properties: { email: operatorEmail } };
export const supportConversationSchema = { type: "object", additionalProperties: false,
  required: ["account_id", "email", "role", "last_sequence", "last_text", "last_created_at", "unread_count"], properties: {
    account_id: { type: "string", format: "uuid" }, email: { type: "string" }, role: { enum: ["candidate", "employer"] },
    last_sequence: integer, last_text: { anyOf: [{ type: "string" }, { type: "null" }] },
    last_created_at: { anyOf: [{ type: "string", format: "date-time" }, { type: "null" }] }, unread_count: integer,
  } };
export const supportConversationPageSchema = { type: "object", additionalProperties: false,
  required: ["items", "has_more"], properties: { items: { type: "array", items: supportConversationSchema }, has_more: { type: "boolean" } } };
/** Message page for the operator; intentionally excludes the account's own read state. */
export const supportMessagePageSchema = { type: "object", additionalProperties: false,
  required: ["items", "next_sequence", "has_more"], properties: { items: { type: "array", items: systemNotificationSchema },
    next_sequence: integer, has_more: { type: "boolean" } } };
/** Operator-side unread counter for one conversation. */
export const supportReadRequestSchema = { type: "object", additionalProperties: false, required: ["read_sequence"], properties: { read_sequence: integer } };
export const supportReadStatusSchema = { type: "object", additionalProperties: false,
  required: ["read_sequence", "unread_count"], properties: { read_sequence: integer, unread_count: integer } };

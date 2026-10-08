/** Strict recruitment contracts; all state transitions and salary ordering are validated by the store. */
const uuid = { type: "string", format: "uuid" };
const timestamp = { type: "string", format: "date-time" };
const integer = (minimum: number, maximum = Number.MAX_SAFE_INTEGER) => ({ type: "integer", minimum, maximum });
const text = (maxLength: number, minLength = 0) => ({ type: "string", minLength, maxLength });
const nonempty = (maxLength: number) => ({ ...text(maxLength, 1), pattern: "\\S" });
const nullable = (schema: object) => ({ anyOf: [schema, { type: "null" }] });
const object = (properties: Record<string, object>, required = Object.keys(properties)) => ({
  type: "object", additionalProperties: false, properties, required,
});
const enumeration = (values: readonly string[]) => ({ type: "string", enum: values });
const states = ["sent", "viewed", "accepted", "declined", "rejected", "offer_sent", "hired", "offer_declined", "withdrawn"];
const actions = ["view", "accept", "decline", "withdraw", "reject", "interview_propose", "interview_accept", "interview_decline", "interview_cancel", "interview_complete", "offer_send", "offer_accept", "offer_decline"];
const salary = object({ from_rub: integer(0), to_rub: integer(0) });
const interviewFields = {
  kind: enumeration(["technical", "general"]), starts_at: timestamp,
  time_zone: nonempty(100), duration_minutes: integer(1, 480),
  format: enumeration(["remote", "onsite"]), location: nonempty(2000),
};
const outcome = enumeration(["passed", "failed", "neutral"]);
const identity = { expected_revision: integer(1), idempotency_key: uuid };
const command = (action: string | readonly string[], fields: Record<string, object> = {}) => object({
  ...identity, action: enumeration(typeof action === "string" ? [action] : action), ...fields,
});

/** Invitation creation never accepts owner IDs, state, profile revision, or contact information. */
export const invitationBodySchema = object({
  candidate_id: uuid, idempotency_key: uuid, company_name: nonempty(300), description: nonempty(10000), salary,
}, ["candidate_id", "idempotency_key", "company_name", "description"]);

/** Rejecting a saved candidate accepts no invitation terms or participant identities. */
export const rejectSavedBodySchema = object({ candidate_id: uuid, idempotency_key: uuid, reason: nonempty(10000) });

/** Each action branch closes additional properties and requires CAS plus a UUID retry key. */
export const recruitmentActionSchema = { anyOf: [
  command(["view", "accept"]),
  command(["decline", "withdraw", "reject"], { reason: nonempty(10000) }),
  command("interview_propose", interviewFields),
  command("interview_accept", { interview_id: uuid }),
  command(["interview_decline", "interview_cancel"], { interview_id: uuid, reason: nonempty(10000) }),
  command("interview_complete", { interview_id: uuid, outcome, feedback: nonempty(10000) }),
  command("offer_send", { salary_rub: integer(1), description: nonempty(10000), expires_at: timestamp }),
  command("offer_accept", { offer_id: uuid }),
  command("offer_decline", { offer_id: uuid, reason: nonempty(10000) }),
] };

const interview = object({ id: uuid, ...interviewFields,
  state: enumeration(["proposed", "scheduled", "declined", "cancelled", "completed"]),
  completed_at: nullable(timestamp), outcome: nullable(outcome), feedback: nullable(text(10000)),
});
const offer = object({ id: uuid, state: enumeration(["sent", "accepted", "declined", "expired"]),
  salary_rub: integer(1), description: nonempty(10000), expires_at: timestamp, sent_at: timestamp, resolved_at: nullable(timestamp),
});

/** Shared snapshots contain immutable terms and schedule history but no candidate contacts. */
export const recruitmentViewSchema = object({
  id: uuid, owner_id: uuid, candidate_id: uuid, vacancy_id: uuid, draft_id: uuid,
  profile_revision: integer(1), revision: integer(1), state: enumeration(states),
  terms: nullable(object({ company_name: nonempty(300), description: nonempty(10000), salary,
    contact_method: enumeration(["platform"]), vacancy_title: nullable(text(300)), city: text(300), daily: text(10000), work_type: text(300),
  })),
  invited_at: nullable(timestamp),
  closure: nullable(object({ reason: nonempty(10000), actor: enumeration(["candidate", "employer"]), at: timestamp })),
  interviews: { type: "array", items: interview }, offers: { type: "array", items: offer },
  allowed_actions: { type: "array", items: enumeration(actions), uniqueItems: true },
  chat_available: { type: "boolean" }, chat_writable: { type: "boolean" }, created_at: timestamp, updated_at: timestamp,
});

/** Participant-scoped list response with explicit pagination bounds. */
export const recruitmentPageSchema = object({ items: { type: "array", items: recruitmentViewSchema },
  limit: integer(1, 100), offset: integer(0), has_more: { type: "boolean" },
  next_cursor: nullable(text(1024)),
});

/** Separate authorized contact response, never nested in recruitment snapshots. */
export const contactsSchema = object({ candidate_id: uuid, display_name: text(300),
  email: { ...text(320, 1), format: "email" }, phone: nullable(text(100)), profile_revision: integer(1),
});

/** Durable participant notifications expose only process references and lifecycle metadata. */
export const recruitmentEventPageSchema = object({
  items: { type: "array", items: object({ event_id: uuid, sequence: integer(1), process_id: uuid,
    revision: integer(1), type: nonempty(100), state: enumeration(states), created_at: timestamp,
  }) }, next_sequence: integer(0), has_more: { type: "boolean" },
});

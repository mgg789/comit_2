/** OpenAPI company setup contract; owner identity is always session-derived. */
export const employerProfileSchema = { type: "object", additionalProperties: false,
  required: ["company_name", "revision", "updated_at"], properties: {
    company_name: { anyOf: [{ type: "string", minLength: 1, maxLength: 200 }, { type: "null" }] },
    revision: { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER },
    updated_at: { anyOf: [{ type: "string", format: "date-time" }, { type: "null" }] },
  } };
/** Revision zero creates the initial company, higher revisions protect concurrent settings edits. */
export const employerProfileMutationSchema = { type: "object", additionalProperties: false,
  required: ["company_name", "expected_revision"], properties: {
    company_name: { type: "string", minLength: 1, maxLength: 200, pattern: "\\S" },
    expected_revision: { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER },
  } };

/** Strict browser contracts accept declarations and reject identity, proof and consent timestamps. */
const text = (maxLength: number) => ({ type: "string", maxLength });
const nullable = (schema: object) => ({ anyOf: [schema, { type: "null" }] });
const key = { type: "string", minLength: 1, maxLength: 100, pattern: "^[a-z][a-z0-9_-]*$" };
const array = (items: object, maxItems: number) => ({ type: "array", items, maxItems });
const object = (properties: Record<string, object>, required: string[] = []) => ({
  type: "object", additionalProperties: false, properties, ...(required.length ? { required } : {}),
});
const roles = ["developer", "lead", "architect", "analyst", "designer", "researcher", "qa", "devops", "other"];
const skill = object({ key, level: { type: "integer", minimum: 0, maximum: 7 } }, ["key", "level"]);
const narrative = object({
  id: { type: "string", format: "uuid" }, title: { ...text(300), minLength: 1 }, description: text(10000),
  link: nullable({ type: "string", maxLength: 2000, pattern: "^https?://" }), organization: nullable(text(300)),
  facts: nullable(object({ domains: { ...array(key, 1000), uniqueItems: true }, stacks: { ...array(key, 1000), uniqueItems: true },
    duration_months: nullable({ type: "integer", minimum: 0, maximum: 1200 }),
    role: nullable({ type: "string", enum: roles }), production_support: nullable({ type: "boolean" }),
  })),
}, ["title"]);

/** One editable profile snapshot or shallow patch; all skill levels are integer ordinals 0..7. */
export const candidateProfileFieldsSchema = object({
  display_name: text(300), phone: nullable(text(100)), city: text(300),
  work_types: nullable({ ...array({ type: "string", enum: ["onsite", "remote", "hybrid"] }, 3), uniqueItems: true }),
  relocation_ready: nullable({ type: "boolean" }), relocation_cities: array({ ...text(300), minLength: 1 }, 100),
  salary: nullable(object({ from_rub: { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER },
    to_rub: { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER } }, ["from_rub", "to_rub"])),
  primary_direction: nullable({ type: "string", enum: ["backend", "frontend", "fullstack", "mobile", "devops", "data", "qa", "ml"] }),
  declared_grade: nullable({ type: "string", enum: ["intern", "junior", "middle", "senior"] }),
  domains: array(skill, 1000), stacks: array(skill, 1000), soft_skills: array(skill, 1000), description: text(10000),
  projects: array(narrative, 100), experience: array(narrative, 100), achievements: array(narrative, 100),
  preferences: array({ ...text(1000), minLength: 1 }, 100),
  fsp_id: nullable({ type: "string", maxLength: 120, pattern: "^[A-Za-z0-9._:-]+$" }),
  fsp_claim_id: nullable({ type: "string", maxLength: 120, pattern: "^[A-Za-z0-9._:-]+$" }),
});

/** CAS revision zero creates a profile; explicit consent never accepts client-supplied timestamps. */
export const candidateProfileMutationSchema = object({
  expected_revision: { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER }, fields: candidateProfileFieldsSchema,
  consent: object({ processing: { type: "boolean" }, pool: { type: "boolean" },
    training: { type: "boolean" }, training_version: { type: "string", enum: ["training-v1", "training-v2"] },
    version: { type: "string", minLength: 1, maxLength: 100 } }, ["processing", "pool", "version"]),
}, ["expected_revision", "fields"]);

/** Private response schema includes evidence only as a read-only, separately persisted channel. */
export const candidateProfileViewSchema = object({
  candidate_id: { type: "string", format: "uuid" }, revision: { type: "integer", minimum: 1 }, fields: candidateProfileFieldsSchema,
  evidence: { type: "object", additionalProperties: object({
    input_hash: { type: "string", pattern: "^[a-f0-9]{64}$" },
    status: { type: "string", enum: ["self_reported", "pending", "confirmed", "partial", "rejected"] },
    verified_value: {}, provenance: array(text(1000), 30), checked_at: nullable({ type: "string", format: "date-time" }),
  }, ["input_hash", "status", "verified_value", "provenance", "checked_at"]) },
  consents: object({ processing: { type: "boolean" }, pool: { type: "boolean" }, version: text(100),
    training: { type: "boolean" }, training_version: nullable({ type: "string", enum: ["training-v1", "training-v2"] }),
    training_accepted_at: nullable({ type: "string", format: "date-time" }),
    accepted_at: nullable({ type: "string", format: "date-time" }) }, ["processing", "pool", "version", "accepted_at", "training", "training_version", "training_accepted_at"]),
  projection: object({ state: { type: "string", enum: ["queued", "processing", "retrying", "failed", "delivered"] },
    source_revision: { type: "integer" }, projected_revision: { type: "integer" }, attempts: { type: "integer" },
    error_code: nullable(text(100)) }, ["state", "source_revision", "projected_revision", "attempts", "error_code"]),
  updated_at: { type: "string", format: "date-time" },
}, ["candidate_id", "revision", "fields", "evidence", "consents", "projection", "updated_at"]);

/** Active canonical skill choices and account-owned accepted test cooldowns. */
export const candidateSkillCatalogSchema = object({
  version: { type: "integer", minimum: 1 }, schema_hash: text(128),
  skills: array(object({ group: { enum: ["domains", "stacks", "soft_skills"] }, key, label: text(300),
    test_subject: nullable({ enum: ["algorithms", "sql", "rest_api", "python", "react", "docker", "teamwork"] }),
    confirmed_level: nullable({ type: "integer", minimum: 1, maximum: 7 }),
    next_attempt_at: nullable({ type: "string", format: "date-time" }), upgrade_at: nullable({ type: "string", format: "date-time" }),
  }, ["group", "key", "label", "test_subject", "confirmed_level", "next_attempt_at", "upgrade_at"]), 1000),
}, ["version", "schema_hash", "skills"]);

/** Pure source-profile projection helpers. Matching receives only this snapshot. */
import type { CandidateClaimEvidence, CandidateDeclaredNarrative, CandidateIntake, CandidateProfileFields, ClaimStatus, JsonValue } from "@comit/shared";

type ProjectionSkill = NonNullable<CandidateIntake["domains"]>[number];
type ProjectionNarrative = NonNullable<CandidateIntake["projects"]>[number];
type ProjectionFacts = NonNullable<ProjectionNarrative["facts"]>;
type ProjectionReference = NonNullable<ProjectionFacts["domains"]>[number];
type ProjectionValue<T> = { value: T; status: ClaimStatus; provenance?: string[] };

/** Persisted server proof for one canonical source value; candidates cannot write this channel. */
export interface ProfileEvidenceRow {
  evidence_key: string;
  input_hash: string;
  status: ClaimStatus;
  verified_value: JsonValue | null;
  provenance: string[];
  checked_at: Date | string | null;
}

/** Authenticated account identity supplies the verified email to the internal snapshot. */
export interface ProjectionAccount {
  id: string;
  email: string;
  email_verified: boolean;
}

/** Profile-specific consent includes only the acceptance timestamp recorded by the server. */
export interface ProjectionConsent {
  processing: boolean;
  pool: boolean;
  training?: boolean;
  training_version?: "training-v1" | "training-v2" | null;
  training_accepted_at?: Date | string | null;
  version: string;
  accepted_at: Date | string | null;
}

/** Convert a JSON object into a stable canonical representation for evidence hashes. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const object = value as Record<string, unknown>;
  return `{${Object.keys(object).filter(key => object[key] !== undefined).sort()
    .map(key => `${JSON.stringify(key)}:${canonicalJson(object[key])}`).join(",")}}`;
}

/** Convert a PostgreSQL timestamp into the stable HTTP representation used by shared DTOs. */
export function projectionTimestamp(value: Date | string | null): string | null {
  return value === null ? null : value instanceof Date ? value.toISOString() : new Date(value).toISOString();
}

function evidenceMap(rows: ProfileEvidenceRow[]): Map<string, ProfileEvidenceRow> {
  return new Map(rows.map(row => [row.evidence_key, row]));
}

function claim(map: Map<string, ProfileEvidenceRow>, key: string): CandidateClaimEvidence {
  const row = map.get(key);
  return row ? {
    input_hash: row.input_hash,
    status: row.status,
    verified_value: row.verified_value,
    provenance: row.provenance,
    checked_at: projectionTimestamp(row.checked_at),
  } : {
    input_hash: "",
    status: "self_reported",
    verified_value: null,
    provenance: [],
    checked_at: null,
  };
}

function status(map: Map<string, ProfileEvidenceRow>, key: string, input?: unknown): ClaimStatus {
  const row = map.get(key);
  if (!row) return "self_reported";
  if (row.status === "confirmed" && row.verified_value !== null && input !== undefined) {
    const same = canonicalJson(row.verified_value) === canonicalJson(input)
      || (typeof input === "object" && input !== null && typeof (input as { level?: unknown }).level === "number"
        && row.verified_value === (input as { level: number }).level);
    if (!same) return "self_reported";
  }
  return row.status;
}

function provenance(map: Map<string, ProfileEvidenceRow>, key: string): string[] {
  return claim(map, key).provenance;
}

function skillList(fields: CandidateProfileFields, group: "domains" | "stacks" | "soft_skills", map: Map<string, ProfileEvidenceRow>): ProjectionSkill[] {
  return (fields[group] ?? []).map(skill => ({
    key: skill.key,
    level: skill.level,
    status: status(map, `${group}:${skill.key}`, skill),
    provenance: provenance(map, `${group}:${skill.key}`),
  }));
}

function factValue<T>(map: Map<string, ProfileEvidenceRow>, key: string, value: T): ProjectionValue<T> {
  return { value, status: status(map, key, value), provenance: provenance(map, key) };
}

function references(map: Map<string, ProfileEvidenceRow>, prefix: string, values: string[]): ProjectionReference[] {
  return values.map(value => ({
    key: value,
    status: status(map, `${prefix}:${value}`, value),
    provenance: provenance(map, `${prefix}:${value}`),
  }));
}

function factsFor(map: Map<string, ProfileEvidenceRow>, group: string, id: string, input: NonNullable<CandidateDeclaredNarrative["facts"]>): ProjectionFacts {
  const prefix = `${group}:${id}:fact`;
  return {
    domains: references(map, `${prefix}:domains`, input.domains ?? []),
    stacks: references(map, `${prefix}:stacks`, input.stacks ?? []),
    duration_months: input.duration_months === undefined || input.duration_months === null
      ? null : factValue(map, `${prefix}:duration_months`, input.duration_months),
    role: input.role === undefined || input.role === null ? null : factValue(map, `${prefix}:role`, input.role),
    production_support: input.production_support === undefined || input.production_support === null
      ? null : factValue(map, `${prefix}:production_support`, input.production_support),
  };
}

/**
 * Grade result from the declared grade, upgraded to "approved" when the
 * trusted testing contour has confirmed exactly this grade. Any mismatch
 * (different grade, missing session) falls back to self_reported - the
 * approved status is never inferred.
 */
function trustedGradeResult(map: Map<string, ProfileEvidenceRow>, declaredGrade: "intern" | "junior" | "middle" | "senior"): {
  grade: "intern" | "junior" | "middle" | "senior";
  status: "approved" | "self_reported";
  test_session_id: string | null; approved_at: string | null;
} {
  const row = map.get("grade:trusted");
  if (row?.status === "confirmed" && row.verified_value
      && typeof row.verified_value === "object" && !Array.isArray(row.verified_value)
      && (row.verified_value as Record<string, JsonValue>)["grade"] === declaredGrade) {
    return { grade: declaredGrade, status: "approved",
             test_session_id: String((row.verified_value as Record<string, JsonValue>)["session_id"] ?? ""),
             approved_at: projectionTimestamp(row.checked_at) };
  }
  return { grade: declaredGrade, status: "self_reported", test_session_id: null, approved_at: null };
}

function narratives(fields: CandidateProfileFields, group: "projects" | "experience" | "achievements", map: Map<string, ProfileEvidenceRow>): ProjectionNarrative[] {
  return (fields[group] ?? []).map((item) => {
    const id = item.id as string;
    const key = `${group}:${id}`;
    const details: Record<string, JsonValue> = {};
    if (item.link !== undefined && item.link !== null) details.link = item.link;
    if (item.organization !== undefined && item.organization !== null) details.organization = item.organization;
    return {
      title: item.title,
      ...(item.description !== undefined ? { description: item.description } : {}),
      status: status(map, key, { ...item, facts: undefined }),
      ...(item.facts ? { facts: factsFor(map, group, id, item.facts) } : {}),
      ...(Object.keys(details).length ? { details } : {}),
      provenance: provenance(map, key),
    };
  });
}

/**
 * Build the exact matching intake from the API-owned source row.
 * Email is copied only from a verified account; declared grade is deliberately
 * self-reported and has no invented test session or approval timestamp.
 * The FSP block is copied only from a server-confirmed registry evidence row;
 * the registry display name never enters the snapshot.
 */
export function projectCandidateIntake(
  account: ProjectionAccount,
  fields: CandidateProfileFields,
  evidenceRows: ProfileEvidenceRow[],
  revision: number,
  consent: ProjectionConsent,
): CandidateIntake {
  const map = evidenceMap(evidenceRows);
  const declaredGrade = fields.declared_grade ?? null;
  const externalEvidence = evidenceRows.flatMap(row => {
    const match = /^(external:(?:fsp|repository|hh|weekly_challenge)):(.+)$/.exec(row.evidence_key);
    if (!match) return [];
    const subject = match[2];
    const prefix = match[1];
    if (!subject || !prefix) return [];
    return [{ kind: prefix.slice("external:".length) as "fsp" | "repository" | "hh" | "weekly_challenge", subject,
      // External input is kept in the evidence hash, while the provider result is already server-bound.
      status: row.status, verified_value: row.verified_value }];
  });
  const intake = {
    source_revision: revision,
    ...(fields.display_name !== undefined ? { display_name: fields.display_name } : {}),
    ...(account.email_verified ? { email: account.email, email_verified: true } : { email_verified: false }),
    ...(fields.phone !== undefined ? { phone: fields.phone } : {}),
    ...(fields.city !== undefined ? { city: fields.city } : {}),
    ...(fields.work_types !== undefined ? { work_types: fields.work_types } : {}),
    ...(fields.relocation_ready !== undefined ? { relocation_ready: fields.relocation_ready } : {}),
    ...(fields.relocation_cities !== undefined ? { relocation_cities: fields.relocation_cities } : {}),
    ...(fields.salary !== undefined ? { salary: fields.salary } : {}),
    ...(fields.primary_direction !== undefined ? { primary_direction: fields.primary_direction } : {}),
    ...(declaredGrade
      ? { grade_result: trustedGradeResult(map, declaredGrade as "intern" | "junior" | "middle" | "senior") }
      : {}),
    consents: {
      processing: consent.processing,
      pool: consent.pool,
      training: consent.training ?? false,
      training_version: consent.training_version ?? null,
      training_accepted_at: projectionTimestamp(consent.training_accepted_at ?? null),
      version: consent.version,
      accepted_at: projectionTimestamp(consent.accepted_at),
    },
    domains: skillList(fields, "domains", map),
    stacks: skillList(fields, "stacks", map),
    soft_skills: skillList(fields, "soft_skills", map),
    ...(fields.description !== undefined ? { description: fields.description } : {}),
    projects: narratives(fields, "projects", map),
    experience: narratives(fields, "experience", map),
    achievements: narratives(fields, "achievements", map),
    preferences: fields.preferences ?? [],
    ...(externalEvidence.length ? { external_evidence: externalEvidence } : {}),
  };
  return intake;
}

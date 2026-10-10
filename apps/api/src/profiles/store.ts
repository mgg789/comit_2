/** Candidate-owned source profile store and its transactional projection outbox. */
import { createCipheriv, createDecipheriv, createHash, randomBytes, randomUUID } from "node:crypto";
import type { Pool, PoolClient, QueryResultRow } from "pg";
import type {
  CandidateClaimEvidence,
  ClaimStatus,
  CandidateProfileFields,
  CandidateProfileMutation,
  CandidateProfileView,
  CandidateProjectionDelivery,
  CandidateVerificationDocument,
  CandidateVerificationDocumentUpload,
  InvitationTerms,
  JsonValue,
} from "@comit/shared";
import { transaction } from "../auth/database.js";
import {
  canonicalJson,
  projectCandidateIntake,
  projectionTimestamp,
  type ProfileEvidenceRow,
  type ProjectionAccount,
  type ProjectionConsent,
} from "./projection.js";

const PROFILE_CONSENT_VERSION = "profile-v1";
const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const KEY = /^[a-z][a-z0-9_-]*$/;
const FIELD_NAMES = new Set([
  "display_name", "phone", "city", "work_types", "relocation_ready", "relocation_cities", "salary",
  "primary_direction", "declared_grade", "domains", "stacks", "soft_skills", "description", "projects",
  "experience", "achievements", "preferences", "fsp_id", "fsp_claim_id",
]);
const FORBIDDEN_NAMES = new Set([
  "id", "candidate_id", "email", "email_verified", "revision", "status", "evidence", "consent", "consents",
  "projection", "source_revision", "grade_result", "test_session_id", "verified_value", "provenance",
]);
const SKILL_GROUPS = ["domains", "stacks", "soft_skills"] as const;
const NARRATIVE_GROUPS = ["projects", "experience", "achievements"] as const;

/** Safe API/domain error whose code can be mapped by the owning route. */
export class ProfileError extends Error {
  public constructor(public readonly status: number, public readonly code: string) {
    super(code);
    this.name = "ProfileError";
  }
}

interface AccountRow extends QueryResultRow, ProjectionAccount {
  role: "candidate" | "employer";
}

interface ProfileRow extends QueryResultRow {
  candidate_id: string;
  revision: number | string;
  fields: CandidateProfileFields;
  processing_consent: boolean;
  pool_consent: boolean;
  training_consent: boolean;
  training_consent_version: "training-v1" | "training-v2" | null;
  training_consent_accepted_at: Date | string | null;
  consent_version: string;
  consent_accepted_at: Date | string | null;
  updated_at: Date | string;
}

interface EvidenceDbRow extends QueryResultRow, ProfileEvidenceRow {
  provenance: string[];
}

interface DeliveryRow extends QueryResultRow {
  state: CandidateProjectionDelivery["state"] | "obsolete";
  source_revision: number | string;
  projected_revision: number | string | null;
  attempts: number;
  error_code: string | null;
}

interface OutboxJobRow extends QueryResultRow {
  id: string;
  candidate_id: string;
  source_revision: number | string;
  payload: unknown;
  attempts: number;
  lease_token: string;
  lease_until: Date | string;
}

interface DocumentRow extends QueryResultRow {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: "pending";
  uploaded_at: Date | string;
  claim_kind: "projects" | "experience" | "achievements";
  claim_id: string;
  nonce?: Buffer;
  ciphertext?: Buffer;
}

/** A leased exact snapshot handed to the matching engine. */
export interface ProfileProjectionJob {
  job_id: string;
  candidate_id: string;
  source_revision: number;
  payload: unknown;
  attempts: number;
  lease_token: string;
  lease_until: string;
}

function invalid(code: string): ProfileError {
  return new ProfileError(400, code);
}

function conflict(code: string): ProfileError {
  return new ProfileError(409, code);
}

function uuid(value: string): boolean {
  return UUID.test(value);
}

function stableClaimUuid(value: string): string {
  const bytes = createHash("sha256").update(`fsp-claim:${value}`, "utf8").digest().subarray(0, 16);
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x50;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function integer(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= MAX_SAFE_INTEGER;
}

function timestamp(value: Date | string | null): string | null {
  if (value === null) return null;
  return value instanceof Date ? value.toISOString() : new Date(value).toISOString();
}

function jsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function normalizeNarratives(fields: CandidateProfileFields, current: CandidateProfileFields | null, allowNewIds = false): CandidateProfileFields {
  const result = clone(fields);
  const seen = new Set<string>();
  const existing = new Map<string, string>();
  for (const group of NARRATIVE_GROUPS) for (const item of current?.[group] ?? []) {
    if (item.id) existing.set(item.id, group);
  }
  for (const group of NARRATIVE_GROUPS) {
    result[group] = (result[group] ?? []).map(item => {
      const id = item.id ?? randomUUID();
      if (!uuid(id)) throw new ProfileError(422, "unknown_narrative_id");
      if (item.id !== undefined && !allowNewIds && existing.get(id) !== group) throw new ProfileError(422, "unknown_narrative_id");
      if (seen.has(id)) throw invalid("duplicate_narrative_id");
      seen.add(id);
      return { ...item, id };
    });
  }
  return result;
}

function validateFields(value: unknown): asserts value is CandidateProfileFields {
  if (!jsonObject(value)) throw invalid("invalid_profile_fields");
  for (const key of Object.keys(value)) {
    if (FORBIDDEN_NAMES.has(key)) throw invalid("profile_field_forbidden");
    if (!FIELD_NAMES.has(key)) throw invalid("unknown_profile_field");
  }
  const fields = value as CandidateProfileFields;
  const arrayOfStrings = (input: unknown): input is string[] => Array.isArray(input)
    && input.every(item => typeof item === "string" && item.trim().length > 0);
  if (fields.display_name !== undefined && (typeof fields.display_name !== "string" || fields.display_name.length > 300)) throw invalid("invalid_profile_fields");
  if (fields.phone !== undefined && fields.phone !== null && (typeof fields.phone !== "string" || fields.phone.length > 100)) throw invalid("invalid_profile_fields");
  if (fields.city !== undefined && typeof fields.city !== "string") throw invalid("invalid_city");
  if (fields.city !== undefined && (!fields.city.trim() || fields.city.length > 300)) throw invalid("invalid_city");
  if (fields.work_types !== undefined && fields.work_types !== null
    && (!Array.isArray(fields.work_types) || fields.work_types.some(item => !["onsite", "remote", "hybrid"].includes(item)))) throw invalid("invalid_profile_fields");
  if (fields.relocation_ready !== undefined && fields.relocation_ready !== null && typeof fields.relocation_ready !== "boolean") throw invalid("invalid_profile_fields");
  if (fields.relocation_cities !== undefined && (!arrayOfStrings(fields.relocation_cities) || new Set(fields.relocation_cities).size !== fields.relocation_cities.length)) throw invalid("invalid_profile_fields");
  if (fields.salary !== undefined && fields.salary !== null) {
    if (!jsonObject(fields.salary) || Object.keys(fields.salary).some(key => !["from_rub", "to_rub"].includes(key))
      || !integer(fields.salary.from_rub) || !integer(fields.salary.to_rub)
      || fields.salary.to_rub < fields.salary.from_rub) throw invalid("invalid_salary");
  }
  if (fields.primary_direction !== undefined && fields.primary_direction !== null
    && !["backend", "frontend", "fullstack", "mobile", "devops", "data", "ml", "qa"].includes(fields.primary_direction)) throw invalid("invalid_profile_fields");
  if (fields.declared_grade !== undefined && fields.declared_grade !== null
    && !["intern", "junior", "middle", "senior"].includes(fields.declared_grade)) throw invalid("invalid_profile_fields");
  if (fields.description !== undefined && (typeof fields.description !== "string" || fields.description.length > 10000)) throw invalid("invalid_profile_fields");
  if (fields.fsp_id !== undefined && fields.fsp_id !== null && (typeof fields.fsp_id !== "string" || !/^[A-Za-z0-9._:-]{1,120}$/.test(fields.fsp_id))) throw invalid("invalid_fsp_id");
  if (fields.fsp_claim_id !== undefined && fields.fsp_claim_id !== null && (typeof fields.fsp_claim_id !== "string" || !/^[A-Za-z0-9._:-]{1,120}$/.test(fields.fsp_claim_id))) throw invalid("invalid_fsp_claim_id");
  if (fields.preferences !== undefined && (!arrayOfStrings(fields.preferences) || new Set(fields.preferences).size !== fields.preferences.length)) throw invalid("invalid_profile_fields");
  for (const group of SKILL_GROUPS) {
    const skills = fields[group];
    if (skills === undefined) continue;
    if (!Array.isArray(skills)) throw invalid("invalid_skills");
    const keys = new Set<string>();
    for (const skill of skills) {
      if (!jsonObject(skill) || Object.keys(skill).some(key => !["key", "level"].includes(key))
        || typeof skill.key !== "string" || !KEY.test(skill.key)
        || !integer(skill.level) || skill.level > 7 || keys.has(skill.key)) throw invalid("invalid_skills");
      keys.add(skill.key);
    }
  }
  for (const group of NARRATIVE_GROUPS) {
    const narratives = fields[group];
    if (narratives === undefined) continue;
    if (!Array.isArray(narratives)) throw invalid("invalid_narratives");
    for (const narrative of narratives) {
      if (!jsonObject(narrative) || Object.keys(narrative).some(key => !["id", "title", "description", "link", "organization", "facts"].includes(key))
        || typeof narrative.title !== "string" || !narrative.title.trim()) {
        throw invalid("invalid_narrative");
      }
      if (narrative.description !== undefined && (typeof narrative.description !== "string" || narrative.description.length > 10000)) throw invalid("invalid_narrative");
      if (narrative.link !== undefined && narrative.link !== null && (typeof narrative.link !== "string" || !/^https?:\/\//.test(narrative.link))) throw invalid("invalid_narrative");
      if (narrative.organization !== undefined && narrative.organization !== null && (typeof narrative.organization !== "string" || narrative.organization.length > 300)) throw invalid("invalid_narrative");
      if (narrative.id !== undefined && (typeof narrative.id !== "string" || !uuid(narrative.id))) {
        throw invalid("invalid_narrative_id");
      }
      if (narrative.facts !== undefined && narrative.facts !== null) {
        if (!jsonObject(narrative.facts) || Object.keys(narrative.facts).some(key => !["domains", "stacks", "duration_months", "role", "production_support"].includes(key))) throw invalid("invalid_narrative_facts");
        for (const factGroup of ["domains", "stacks"] as const) {
          const refs = narrative.facts[factGroup];
          if (refs === undefined) continue;
          if (!Array.isArray(refs) || refs.some(item => typeof item !== "string" || !KEY.test(item))
            || new Set(refs).size !== refs.length) throw invalid("invalid_narrative_facts");
        }
        if (narrative.facts.duration_months !== undefined && narrative.facts.duration_months !== null
          && (!integer(narrative.facts.duration_months) || narrative.facts.duration_months > 1200)) throw invalid("invalid_narrative_facts");
        const role = narrative.facts.role;
        if (role !== undefined && role !== null
          && (typeof role !== "string" || !new Set(["developer", "lead", "architect", "analyst", "designer", "researcher", "qa", "devops", "other"]).has(role))) throw invalid("invalid_narrative_facts");
        if (narrative.facts.production_support !== undefined && narrative.facts.production_support !== null
          && typeof narrative.facts.production_support !== "boolean") throw invalid("invalid_narrative_facts");
      }
    }
  }
}

function validateConsent(value: unknown): asserts value is NonNullable<CandidateProfileMutation["consent"]> {
  if (!jsonObject(value) || Object.keys(value).some(key => !["processing", "pool", "version", "training", "training_version"].includes(key))
    || typeof value.processing !== "boolean" || typeof value.pool !== "boolean"
    || typeof value.version !== "string" || value.version !== PROFILE_CONSENT_VERSION
    || "accepted_at" in value) throw invalid("invalid_profile_consent");
  if (value.pool && !value.processing) throw invalid("processing_consent_required");
  if (value.training !== undefined && typeof value.training !== "boolean") throw invalid("invalid_training_consent");
  if (value.training ? (!value.processing || !value.pool || !["training-v1", "training-v2"].includes(String(value.training_version))) : value.training_version !== undefined) throw invalid("invalid_training_consent");
}

function evidenceValueRows(fields: CandidateProfileFields): Array<{ key: string; value: unknown }> {
  const result: Array<{ key: string; value: unknown }> = [];
  for (const key of ["display_name", "phone", "city", "work_types", "relocation_ready", "relocation_cities", "salary", "primary_direction", "declared_grade", "description", "preferences"] as const) {
    if (fields[key] !== undefined) {
      const value = key === "declared_grade"
        ? { declared_grade: fields.declared_grade, primary_direction: fields.primary_direction ?? null }
        : fields[key];
      result.push({ key: `field:${key}`, value });
    }
  }
  for (const group of SKILL_GROUPS) {
    for (const skill of fields[group] ?? []) result.push({ key: `${group}:${skill.key}`, value: skill });
  }
  for (const group of NARRATIVE_GROUPS) {
    for (const narrative of fields[group] ?? []) {
      const id = narrative.id as string;
      const base = `${group}:${id}`;
      result.push({ key: base, value: { ...narrative, facts: undefined } });
      for (const factGroup of ["domains", "stacks"] as const) {
        for (const key of narrative.facts?.[factGroup] ?? []) result.push({ key: `${base}:fact:${factGroup}:${key}`, value: key });
      }
      for (const fact of ["duration_months", "role", "production_support"] as const) {
        const value = narrative.facts?.[fact];
        if (value !== undefined && value !== null) result.push({ key: `${base}:fact:${fact}`, value });
      }
    }
  }
  return result;
}

function evidenceRows(fields: CandidateProfileFields, previous: Map<string, EvidenceDbRow>): Array<{
  key: string; inputHash: string; status: string; verifiedValue: JsonValue | null; provenance: string[]; checkedAt: Date | string | null;
}> {
  return evidenceValueRows(fields).map(({ key, value }) => {
    const inputHash = createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
    const prior = previous.get(key);
    if (prior?.input_hash === inputHash) {
      return { key, inputHash, status: prior.status, verifiedValue: prior.verified_value, provenance: prior.provenance, checkedAt: prior.checked_at };
    }
    return { key, inputHash, status: "self_reported", verifiedValue: null, provenance: [], checkedAt: null };
  });
}

function isFspImportedAchievement(row: EvidenceDbRow): boolean {
  if (!row.evidence_key.startsWith("achievements:")) return false;
  const value = row.verified_value;
  return row.provenance.some(item => item.startsWith("fsp_hackathon_registry:"))
    && jsonObject(value) && typeof value.id === "string";
}

/** Keep provider evidence across unrelated profile edits; a removed/changed bound URL invalidates it. */
function externalEvidenceRows(fields: CandidateProfileFields, previous: Map<string, EvidenceDbRow>): Array<{
  key: string; inputHash: string; status: string; verifiedValue: JsonValue | null; provenance: string[]; checkedAt: Date | string | null;
}> {
  return [...previous.values()].filter(row => {
    if (!row.evidence_key.startsWith("external:")) return false;
    if (row.evidence_key.startsWith("external:fsp:")) {
      const subject = row.evidence_key.slice("external:fsp:".length).split(":");
      return subject[0] === fields.fsp_id && (!subject[1] || subject[1] === fields.fsp_claim_id);
    }
    if (row.evidence_key.startsWith("external:hh:")) return true;
    const subject = row.evidence_key.slice("external:repository:".length);
    const parts = subject.split(":");
    if (parts.length !== 2 || !NARRATIVE_GROUPS.includes(parts[0] as typeof NARRATIVE_GROUPS[number])) return false;
    const narrative = (fields[parts[0] as "projects" | "experience" | "achievements"] ?? []).find(item => item.id === parts[1]);
    if (!narrative?.link || !narrative.description) return false;
    const value = row.verified_value;
    const verified = typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, JsonValue> : null;
    return verified?.url === narrative.link && verified.contribution_narrative === narrative.description;
  }).map(row => ({ key: row.evidence_key, inputHash: row.input_hash, status: row.status,
    verifiedValue: row.verified_value, provenance: row.provenance, checkedAt: row.checked_at }));
}

async function accountFor(client: PoolClient, accountId: string, lock = true): Promise<AccountRow> {
  if (!uuid(accountId)) throw invalid("invalid_account_id");
  const row = (await client.query<AccountRow>(
    `SELECT id, email, email_verified, role FROM accounts WHERE id = $1${lock ? " FOR UPDATE" : ""}`,
    [accountId],
  )).rows[0];
  if (!row) throw new ProfileError(404, "account_not_found");
  if (row.role !== "candidate") throw new ProfileError(403, "candidate_role_required");
  if (!row.email_verified) throw new ProfileError(409, "email_verification_required");
  return row;
}

async function profileRow(client: PoolClient, accountId: string, lock = false): Promise<ProfileRow | null> {
  const result = await client.query<ProfileRow>(
    `SELECT candidate_id, revision, fields, processing_consent, pool_consent, training_consent, training_consent_version, training_consent_accepted_at, consent_version,
            consent_accepted_at, updated_at
       FROM candidate_profiles WHERE candidate_id = $1${lock ? " FOR UPDATE" : ""}`,
    [accountId],
  );
  return result.rows[0] ?? null;
}

async function evidenceFor(client: PoolClient, accountId: string): Promise<EvidenceDbRow[]> {
  return (await client.query<EvidenceDbRow>(
    `SELECT evidence_key, input_hash, status, verified_value, provenance, checked_at
       FROM candidate_profile_evidence WHERE candidate_id = $1 ORDER BY evidence_key`, [accountId],
  )).rows;
}

function delivery(row: DeliveryRow | null, revision: number): CandidateProjectionDelivery {
  return row ? {
    state: row.state === "obsolete" ? "queued" : row.state,
    source_revision: Number(row.source_revision),
    projected_revision: row.projected_revision === null ? 0 : Number(row.projected_revision),
    attempts: row.attempts,
    error_code: row.error_code,
  } : { state: "queued", source_revision: revision, projected_revision: 0, attempts: 0, error_code: null };
}

async function latestDelivery(client: PoolClient, accountId: string, revision: number): Promise<DeliveryRow | null> {
  const result = await client.query<DeliveryRow>(
    `SELECT state, source_revision, projected_revision, attempts, error_code
       FROM candidate_profile_outbox
      WHERE candidate_id = $1 AND source_revision = $2
      ORDER BY created_at DESC LIMIT 1`, [accountId, revision],
  );
  return result.rows[0] ?? null;
}

async function viewInTransaction(client: PoolClient, accountId: string, account: AccountRow, profile: ProfileRow): Promise<CandidateProfileView> {
  const evidence = await evidenceFor(client, accountId);
  const outbox = await latestDelivery(client, accountId, Number(profile.revision));
  const evidenceView: Record<string, CandidateClaimEvidence> = Object.fromEntries(evidence.map(row => [row.evidence_key, {
    input_hash: row.input_hash,
    status: row.status,
    verified_value: row.verified_value,
    provenance: row.provenance,
    checked_at: projectionTimestamp(row.checked_at),
  }]));
  return {
    candidate_id: account.id,
    revision: Number(profile.revision),
    fields: profile.fields,
    evidence: evidenceView,
    consents: {
      processing: profile.processing_consent,
      pool: profile.pool_consent,
      training: profile.training_consent,
      training_version: profile.training_consent_version,
      training_accepted_at: timestamp(profile.training_consent_accepted_at),
      version: profile.consent_version,
      accepted_at: timestamp(profile.consent_accepted_at),
    },
    projection: delivery(outbox, Number(profile.revision)),
    updated_at: timestamp(profile.updated_at) ?? new Date(0).toISOString(),
  };
}

async function enqueue(client: PoolClient, account: AccountRow, profile: ProfileRow, evidence: EvidenceDbRow[]): Promise<void> {
  const consent: ProjectionConsent = {
    processing: profile.processing_consent, pool: profile.pool_consent,
    training: profile.training_consent, training_version: profile.training_consent_version,
    training_accepted_at: profile.training_consent_accepted_at,
    version: profile.consent_version, accepted_at: profile.consent_accepted_at,
  };
  const payload = projectCandidateIntake(account, profile.fields, evidence, Number(profile.revision), consent);
  await client.query(
    `INSERT INTO candidate_profile_outbox(id, candidate_id, source_revision, payload)
     VALUES ($1, $2, $3, $4::jsonb)`, [randomUUID(), account.id, profile.revision, JSON.stringify(payload)],
  );
}

/** Atomically append an idempotent hiring claim and withdraw publication with an exact projection snapshot.
 * Caller holds the hiring aggregate and takes the account before the profile lock, like ordinary profile writes.
 * The new experience remains a declaration; accepting an offer does not fabricate external verification.
 */
export async function applyRecruitmentHire(client: PoolClient, processId: string, candidateId: string, terms: InvitationTerms): Promise<boolean> {
  const account = await accountFor(client, candidateId);
  const current = await profileRow(client, candidateId, true);
  if (!current?.processing_consent) throw new ProfileError(409, "processing_consent_required");
  const marker = await client.query("INSERT INTO recruitment_hire_applications(process_id,candidate_id) VALUES($1,$2) ON CONFLICT DO NOTHING RETURNING process_id", [processId, candidateId]);
  if (!marker.rowCount) return false;
  const claimId = processId;
  const experience = [...(current.fields.experience ?? [])];
  if (!experience.some(item => item.id === claimId)) experience.push({ id: claimId, title: terms.vacancy_title ?? "Работа",
    organization: terms.company_name, description: "Оффер принят на платформе Comit.", link: null });
  const fields: CandidateProfileFields = { ...current.fields, experience };
  validateFields(fields);
  const profile = (await client.query<ProfileRow>(`UPDATE candidate_profiles SET fields=$2,revision=revision+1,pool_consent=false,training_consent=false,training_consent_version=NULL,training_consent_accepted_at=NULL,updated_at=now()
    WHERE candidate_id=$1 RETURNING *`, [candidateId, fields])).rows[0]!;
  const previous = new Map((await evidenceFor(client, candidateId)).map(row => [row.evidence_key, row]));
  await client.query("DELETE FROM candidate_profile_evidence WHERE candidate_id=$1", [candidateId]);
  for (const row of [...evidenceRows(fields, previous), ...externalEvidenceRows(fields, previous)]) await client.query(`INSERT INTO candidate_profile_evidence
    (candidate_id,evidence_key,input_hash,status,verified_value,provenance,checked_at) VALUES($1,$2,$3,$4,$5,$6,$7)`,
    [candidateId, row.key, row.inputHash, row.status, row.verifiedValue === null ? null : JSON.stringify(row.verifiedValue), JSON.stringify(row.provenance), row.checkedAt]);
  await client.query("UPDATE candidate_profile_outbox SET state='obsolete',lease_token=NULL,lease_until=NULL,updated_at=now() WHERE candidate_id=$1 AND state IN ('queued','processing','retrying','failed')", [candidateId]);
  await enqueue(client, account, profile, await evidenceFor(client, candidateId));
  return true;
}

/**
 * PostgreSQL-backed source profile store. CAS writes and outbox insertion are
 * one transaction, so matching never observes a profile without its snapshot.
 */
export class CandidateProfileStore {
  public constructor(private readonly pool: Pool) {}

  /** Read a private profile after enforcing the candidate account boundary. */
  public async get(accountId: string): Promise<CandidateProfileView | null> {
    return transaction(this.pool, async client => {
      const account = await accountFor(client, accountId, false);
      const profile = await profileRow(client, accountId);
      return profile ? viewInTransaction(client, accountId, account, profile) : null;
    });
  }

  /** Save a CAS patch (or complete replacement when `replace` is true). */
  public async save(accountId: string, mutation: CandidateProfileMutation, replace = false): Promise<CandidateProfileView> {
    if (!integer(mutation?.expected_revision)) throw invalid("invalid_profile_revision");
    validateFields(mutation?.fields);
    if (Object.prototype.hasOwnProperty.call(mutation.fields, "fsp_id") || Object.prototype.hasOwnProperty.call(mutation.fields, "fsp_claim_id")) throw invalid("server_managed_field");
    const hasConsent = mutation.consent !== undefined;
    if (hasConsent) validateConsent(mutation.consent);
    return transaction(this.pool, async client => {
      const account = await accountFor(client, accountId);
      const current = await profileRow(client, accountId, true);
      const currentRevision = current ? Number(current.revision) : 0;
      if (currentRevision !== mutation.expected_revision) throw conflict("profile_revision_conflict");
      const rawFields = replace || !current ? mutation.fields : { ...current.fields, ...mutation.fields };
      const fields = normalizeNarratives(rawFields, current?.fields ?? null);
      validateFields(fields);
      const consent: NonNullable<CandidateProfileMutation["consent"]> = hasConsent ? mutation.consent! : {
        processing: current?.processing_consent ?? false,
        pool: current?.pool_consent ?? false,
        training: current?.training_consent ?? false,
        ...(current?.training_consent_version ? { training_version: current.training_consent_version } : {}),
        version: current?.consent_version ?? PROFILE_CONSENT_VERSION,
      };
      if (consent.version !== PROFILE_CONSENT_VERSION) throw invalid("invalid_profile_consent");
      if (consent.pool && !consent.processing) throw invalid("processing_consent_required");
      const acceptedAt = hasConsent
        ? (consent.processing ? new Date() : null)
        : (current?.consent_accepted_at ?? null);
      const training = consent.training ?? false;
      const trainingAt = training ? (hasConsent ? new Date() : current?.training_consent_accepted_at ?? null) : null;
      const revision = currentRevision + 1;
      const inserted = await client.query<ProfileRow>(
        `INSERT INTO candidate_profiles
          (candidate_id, revision, fields, processing_consent, pool_consent, training_consent, training_consent_version, training_consent_accepted_at, consent_version, consent_accepted_at)
         VALUES ($1, $2, $3::jsonb, $4, $5, $8, $9, $10, $6, $7)
         ON CONFLICT (candidate_id) DO UPDATE SET revision=EXCLUDED.revision, fields=EXCLUDED.fields,
           processing_consent=EXCLUDED.processing_consent, pool_consent=EXCLUDED.pool_consent,
           training_consent=EXCLUDED.training_consent, training_consent_version=EXCLUDED.training_consent_version,
           training_consent_accepted_at=EXCLUDED.training_consent_accepted_at,
           consent_version=EXCLUDED.consent_version, consent_accepted_at=EXCLUDED.consent_accepted_at, updated_at=now()
         RETURNING candidate_id, revision, fields, processing_consent, pool_consent, training_consent, training_consent_version, training_consent_accepted_at, consent_version,
                   consent_accepted_at, updated_at`,
        [accountId, revision, JSON.stringify(fields), consent.processing, consent.pool, consent.version, acceptedAt, training, training ? consent.training_version : null, trainingAt],
      );
      const profile = inserted.rows[0];
      if (!profile) throw new Error("profile insert returned no row");
      const previousRows = current ? await evidenceFor(client, accountId) : [];
      const bindingChanged = current?.fields.fsp_id !== fields.fsp_id || current?.fields.fsp_claim_id !== fields.fsp_claim_id;
      const previous = new Map((bindingChanged ? previousRows.filter(row => !isFspImportedAchievement(row)) : previousRows)
        .map(row => [row.evidence_key, row]));
      await client.query("DELETE FROM candidate_profile_evidence WHERE candidate_id = $1", [accountId]);
      const rows = [...evidenceRows(fields, previous), ...externalEvidenceRows(fields, previous)];
      for (const row of rows) {
        await client.query(
          `INSERT INTO candidate_profile_evidence(candidate_id, evidence_key, input_hash, status, verified_value, provenance, checked_at)
           VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)`,
          [accountId, row.key, row.inputHash, row.status, row.verifiedValue === null ? null : JSON.stringify(row.verifiedValue), JSON.stringify(row.provenance), row.checkedAt],
        );
      }
      await client.query(
        `UPDATE candidate_profile_outbox SET state='obsolete', lease_token=NULL, lease_until=NULL, updated_at=now()
          WHERE candidate_id=$1 AND state IN ('queued','processing','retrying','failed')`, [accountId],
      );
      const currentEvidence = await evidenceFor(client, accountId);
      await enqueue(client, account, profile, currentEvidence);
      return viewInTransaction(client, accountId, account, profile);
    });
  }

  /** Bind a provider response to the current canonical input and reproject it atomically.
   * Revisions advance so a later edit cannot leave a stale matching snapshot acknowledged.
   */
  public async applyVerification(accountId: string, expectedRevision: number, evidenceKey: string, input: unknown, binding: {
    kind: "fsp" | "repository" | "hh"; subject: string; claim_id?: string; claim_kind?: "projects" | "experience" | "achievements"; url?: string; narrative?: string;
  }, result: {
    status: ClaimStatus; verified_value: JsonValue | null; provenance: string[]; checked_at?: string | null;
  }): Promise<CandidateProfileView> {
    if (!/^external:(fsp|repository|hh):.{1,2000}$/.test(evidenceKey)) throw invalid("invalid_verification_key");
    if (!Array.isArray(result.provenance) || result.provenance.some(value => typeof value !== "string" || value.length > 1000)) throw invalid("invalid_verification_result");
    const inputHash = createHash("sha256").update(canonicalJson(input), "utf8").digest("hex");
    return transaction(this.pool, async client => {
      const account = await accountFor(client, accountId);
      const current = await profileRow(client, accountId, true);
      if (!current) throw new ProfileError(404, "profile_not_found");
      if (Number(current.revision) !== expectedRevision) throw conflict("profile_revision_conflict");
      if (binding.kind === "fsp" && (current.fields.fsp_id !== binding.subject || (binding.claim_id && current.fields.fsp_claim_id !== binding.claim_id))) throw conflict("fsp_claim_not_current");
      if (binding.kind === "repository") {
        if (!binding.claim_id || !binding.url || !binding.narrative) throw invalid("repository_claim_required");
        const narrative = (current.fields[binding.subject as "projects" | "experience" | "achievements"] ?? []).find(item => item.id === binding.claim_id);
        if (!narrative || narrative.link !== binding.url || narrative.description !== binding.narrative) throw conflict("repository_claim_not_current");
      }
      if (binding.kind === "hh") {
        if (binding.claim_kind !== "experience" || !binding.claim_id) throw invalid("hh_claim_required");
        const narrative = (current.fields.experience ?? []).find(item => item.id === binding.claim_id);
        const verified = result.verified_value;
        const entries = verified && typeof verified === "object" && !Array.isArray(verified)
          ? (verified as { experience?: unknown }).experience : undefined;
        const matches = narrative && Array.isArray(entries) && entries.some(item => item && typeof item === "object"
          && (item as { title?: unknown }).title === narrative.title
          && (narrative.organization === undefined || narrative.organization === null || (item as { company?: unknown }).company === narrative.organization));
        if (!matches) throw conflict("hh_experience_not_found");
      }
      const previous = await evidenceFor(client, accountId);
      const evidence = new Map(previous.map(row => [row.evidence_key, row]));
      evidence.set(evidenceKey, { evidence_key: evidenceKey, input_hash: inputHash, status: result.status,
        verified_value: result.verified_value, provenance: result.provenance,
        checked_at: result.checked_at ?? new Date().toISOString() });
      await client.query(
        `INSERT INTO candidate_verification_ledger(id,candidate_id,evidence_key,input_hash,status,verified_value,provenance)
         VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb)`,
        [randomUUID(), accountId, evidenceKey, inputHash, result.status,
          result.verified_value === null ? null : JSON.stringify(result.verified_value), JSON.stringify(result.provenance)],
      );
      const revision = Number(current.revision) + 1;
      const updated = await client.query<ProfileRow>(
        `UPDATE candidate_profiles SET revision=$2, updated_at=now() WHERE candidate_id=$1
         RETURNING candidate_id, revision, fields, processing_consent, pool_consent, training_consent,
           training_consent_version, training_consent_accepted_at, consent_version, consent_accepted_at, updated_at`,
        [accountId, revision],
      );
      const profile = updated.rows[0];
      if (!profile) throw new Error("verification profile update returned no row");
      await client.query("DELETE FROM candidate_profile_evidence WHERE candidate_id=$1", [accountId]);
      for (const row of evidence.values()) await client.query(
        `INSERT INTO candidate_profile_evidence(candidate_id,evidence_key,input_hash,status,verified_value,provenance,checked_at)
         VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7)`,
        [accountId, row.evidence_key, row.input_hash, row.status, row.verified_value === null ? null : JSON.stringify(row.verified_value), JSON.stringify(row.provenance), row.checked_at],
      );
      await client.query(
        `UPDATE candidate_profile_outbox SET state='obsolete',lease_token=NULL,lease_until=NULL,updated_at=now()
         WHERE candidate_id=$1 AND state IN ('queued','processing','retrying','failed')`, [accountId]);
      await enqueue(client, account, profile, await evidenceFor(client, accountId));
      return viewInTransaction(client, accountId, account, profile);
    });
  }

  /** Start a candidate-bound, short-lived synthetic FSP authorization state. */
  public async startFspDemoAuthorization(accountId: string, expectedRevision: number, fixtureId: "fsp-001" | "fsp-002"): Promise<{ state: string; expires_at: string }> {
    if (!integer(expectedRevision) || expectedRevision < 1) throw invalid("invalid_profile_revision");
    return transaction(this.pool, async client => {
      await accountFor(client, accountId);
      await client.query(`WITH expired AS (SELECT id FROM candidate_fsp_demo_auth_states WHERE expires_at < now() LIMIT 4096)
        DELETE FROM candidate_fsp_demo_auth_states WHERE id IN (SELECT id FROM expired)`);
      const profile = await profileRow(client, accountId, true);
      if (!profile || Number(profile.revision) !== expectedRevision) throw conflict("profile_revision_conflict");
      const state = randomBytes(32).toString("base64url");
      const expires = new Date(Date.now() + 120_000);
      await client.query(
        `INSERT INTO candidate_fsp_demo_auth_states(id,candidate_id,state_hash,expected_revision,fixture_id,expires_at)
         VALUES ($1,$2,$3,$4,$5,$6)`, [randomUUID(), accountId, createHash("sha256").update(state, "utf8").digest("hex"), expectedRevision, fixtureId, expires],
      );
      return { state, expires_at: expires.toISOString() };
    });
  }

  /** Check a callback state before any provider code is consumed. */
  public async assertFspDemoState(accountId: string, expectedRevision: number, state: string): Promise<"fsp-001" | "fsp-002"> {
    const stateHash = createHash("sha256").update(state, "utf8").digest("hex");
    return transaction(this.pool, async client => {
      await accountFor(client, accountId, false);
      const row = (await client.query<{ fixture_id: "fsp-001" | "fsp-002"; expected_revision: number | string; expires_at: Date | string; used_at: Date | string | null }>(
        `SELECT fixture_id,expected_revision,expires_at,used_at FROM candidate_fsp_demo_auth_states WHERE candidate_id=$1 AND state_hash=$2`, [accountId, stateHash])).rows[0];
      if (!row || row.used_at || Number(row.expected_revision) !== expectedRevision || new Date(row.expires_at).getTime() <= Date.now()) throw conflict("demo_state_invalid_or_replayed");
      return row.fixture_id;
    });
  }

  /** Consume one candidate-bound demo code and import all registry achievements atomically. */
  public async completeFspDemoAuthorization(accountId: string, expectedRevision: number, state: string, participant: unknown): Promise<CandidateProfileView> {
    if (!state || state.length > 256 || !jsonObject(participant)) throw invalid("invalid_demo_callback");
    const fspId = participant.fsp_id;
    const rank = participant.rank;
    const rating = participant.rating;
    const achievements = participant.achievements;
    if (typeof fspId !== "string" || !/^[A-Za-z0-9._:-]{1,120}$/.test(fspId)
      || typeof rank !== "string" || !Number.isSafeInteger(rating) || !Array.isArray(achievements) || achievements.length > 100) throw invalid("invalid_demo_provider_response");
    const ratingValue = rating as number;
    const providerVersion = typeof participant.provider_version === "string" && participant.provider_version.length <= 100
      ? participant.provider_version : "unknown";
    const imported = achievements.map(item => {
      if (!jsonObject(item) || typeof item.claim_id !== "string" || !/^[A-Za-z0-9._:-]{1,120}$/.test(item.claim_id) || typeof item.title !== "string" || !item.title.trim()) throw invalid("invalid_demo_provider_response");
      return { id: stableClaimUuid(item.claim_id), title: item.title.slice(0, 300), description: `ФСП: ${String(item.year ?? "")} место ${String(item.place ?? "")}`.slice(0, 10000), organization: "ФСП" };
    });
    const safeParticipant = { fsp_id: fspId, rank, rating: ratingValue, achievements: imported,
      events: Array.isArray(participant.events) ? participant.events.slice(0, 50) : [],
      certificates: Array.isArray(participant.certificates) ? participant.certificates.slice(0, 50) : [],
      provider: "fsp_hackathon_registry", provider_version: providerVersion, imported_at: new Date().toISOString(),
      demo_registry: true, ownership_proven: false };
    return transaction(this.pool, async client => {
      const account = await accountFor(client, accountId);
      const stateHash = createHash("sha256").update(state, "utf8").digest("hex");
      const authState = (await client.query<{ fixture_id: "fsp-001" | "fsp-002"; expected_revision: number | string; expires_at: Date | string; used_at: Date | string | null }>(
        `SELECT fixture_id,expected_revision,expires_at,used_at FROM candidate_fsp_demo_auth_states
         WHERE candidate_id=$1 AND state_hash=$2 FOR UPDATE`, [accountId, stateHash])).rows[0];
      if (!authState || authState.used_at || new Date(authState.expires_at).getTime() <= Date.now()) throw conflict("demo_state_invalid_or_replayed");
      if (Number(authState.expected_revision) !== expectedRevision) throw conflict("profile_revision_conflict");
      if (authState.fixture_id !== fspId) throw conflict("demo_fixture_mismatch");
      const current = await profileRow(client, accountId, true);
      if (!current || Number(current.revision) !== expectedRevision) throw conflict("profile_revision_conflict");
      const previous = await evidenceFor(client, accountId);
      const providerAchievementIds = new Set(previous.filter(isFspImportedAchievement).map(row => row.evidence_key.slice("achievements:".length)));
      const rawClaimId = achievements.length > 0 && jsonObject(achievements[0]) && typeof achievements[0].claim_id === "string" ? achievements[0].claim_id : null;
      if (current.fields.fsp_id && current.fields.fsp_id !== fspId) throw conflict("fsp_relink_requires_unlink");
      const fields = normalizeNarratives({ ...current.fields, fsp_id: fspId, fsp_claim_id: rawClaimId,
        achievements: [...(current.fields.achievements ?? []).filter(item => !item.id || (!providerAchievementIds.has(item.id) && !imported.some(next => next.id === item.id))), ...imported] }, current.fields, true);
      validateFields(fields);
      await client.query(`UPDATE candidate_fsp_demo_auth_states SET used_at=now() WHERE state_hash=$1`, [stateHash]);
      const revision = expectedRevision + 1;
      const updated = await client.query<ProfileRow>(
        `UPDATE candidate_profiles SET revision=$2,fields=$3::jsonb,updated_at=now() WHERE candidate_id=$1
         RETURNING candidate_id,revision,fields,processing_consent,pool_consent,training_consent,training_consent_version,training_consent_accepted_at,consent_version,consent_accepted_at,updated_at`,
        [accountId, revision, JSON.stringify(fields)]);
      const profile = updated.rows[0];
      if (!profile) throw new Error("demo profile update returned no row");
      const retained = new Map(previous.filter(row => !row.evidence_key.startsWith("external:fsp:")).map(row => [row.evidence_key, row]));
      const evidence = new Map(evidenceRows(fields, retained).map(row => [row.key, {
        evidence_key: row.key, input_hash: row.inputHash, status: row.status, verified_value: row.verifiedValue,
        provenance: row.provenance, checked_at: row.checkedAt,
      }]));
      for (const item of achievements) {
        if (!jsonObject(item) || typeof item.claim_id !== "string") continue;
        const importedId = stableClaimUuid(item.claim_id);
        const claimKey = `achievements:${importedId}`;
        const importedClaim = fields.achievements?.find(claim => claim.id === importedId);
        const claimValue = importedClaim ? {
          id: importedId, title: importedClaim.title, description: importedClaim.description ?? "", organization: importedClaim.organization ?? "ФСП",
        } : { id: importedId, title: typeof item.title === "string" ? item.title : "", description: "", organization: "ФСП" };
        evidence.set(claimKey, { evidence_key: claimKey,
          input_hash: createHash("sha256").update(canonicalJson(fields.achievements?.find(claim => claim.id === importedId) ?? claimValue), "utf8").digest("hex"),
          status: "confirmed", verified_value: claimValue, provenance: ["fsp_hackathon_registry:demo-2026-10"], checked_at: new Date().toISOString() });
      }
      const evidenceKey = `external:fsp:${fspId}${rawClaimId ? `:${rawClaimId}` : ""}`;
      const inputHash = createHash("sha256").update(canonicalJson(safeParticipant), "utf8").digest("hex");
      evidence.set(evidenceKey, { evidence_key: evidenceKey, input_hash: inputHash, status: "partial", verified_value: safeParticipant,
        provenance: ["fsp_hackathon_registry:demo-2026-10"], checked_at: new Date().toISOString() });
      await client.query(`INSERT INTO candidate_verification_ledger(id,candidate_id,evidence_key,input_hash,status,verified_value,provenance)
        VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb)`, [randomUUID(), accountId, evidenceKey, inputHash, "partial", JSON.stringify(safeParticipant), JSON.stringify(["fsp_hackathon_registry:demo-2026-10"])]);
      await client.query("DELETE FROM candidate_profile_evidence WHERE candidate_id=$1", [accountId]);
      for (const row of evidence.values()) await client.query(`INSERT INTO candidate_profile_evidence(candidate_id,evidence_key,input_hash,status,verified_value,provenance,checked_at)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7)`, [accountId,row.evidence_key,row.input_hash,row.status,row.verified_value===null?null:JSON.stringify(row.verified_value),JSON.stringify(row.provenance),row.checked_at]);
      await client.query(`UPDATE candidate_profile_outbox SET state='obsolete',lease_token=NULL,lease_until=NULL,updated_at=now()
        WHERE candidate_id=$1 AND state IN ('queued','processing','retrying','failed')`, [accountId]);
      await enqueue(client, account, profile, await evidenceFor(client, accountId));
      return viewInTransaction(client, accountId, account, profile);
    });
  }

  /** Encrypt a candidate document at rest; upload creates pending metadata only. */
  public async uploadVerificationDocument(accountId: string, upload: CandidateVerificationDocumentUpload, content: Buffer): Promise<CandidateVerificationDocument> {
    if (!upload.filename || upload.filename.length > 255 || upload.filename === "." || upload.filename === ".."
      || /[\\/\u0000-\u001f\u007f]/u.test(upload.filename) || !/^[\w.+-]+\/[\w.+-]+$/.test(upload.content_type)) throw invalid("invalid_document_metadata");
    if (content.length < 1 || content.length > 10 * 1024 * 1024) throw invalid("invalid_document_size");
    const secret = process.env.VERIFICATION_DOCUMENT_KEY;
    if (!secret) throw new ProfileError(503, "document_encryption_unavailable");
    const key = createHash("sha256").update(secret, "utf8").digest();
    const nonce = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", key, nonce);
    const ciphertext = Buffer.concat([cipher.update(content), cipher.final(), cipher.getAuthTag()]);
    return transaction(this.pool, async client => {
      await accountFor(client, accountId);
      const profile = await profileRow(client, accountId);
      if (!profile) throw new ProfileError(404, "profile_not_found");
      const claims = profile.fields[upload.claim_kind] ?? [];
      const claim = claims.find(item => item.id === upload.claim_id);
      if (!claim) throw conflict("document_claim_not_current");
      const id = randomUUID();
      const row = await client.query<DocumentRow>(
        `INSERT INTO candidate_verification_documents(id,candidate_id,filename,content_type,size_bytes,nonce,ciphertext,claim_kind,claim_id)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id,filename,content_type,size_bytes,status,uploaded_at,claim_kind,claim_id`,
        [id, accountId, upload.filename, upload.content_type, content.length, nonce, ciphertext, upload.claim_kind, upload.claim_id],
      );
      const saved = row.rows[0];
      if (!saved) throw new Error("document insert returned no row");
      return { id: saved.id, filename: saved.filename, content_type: saved.content_type, size_bytes: saved.size_bytes,
        status: saved.status, claim_kind: saved.claim_kind, claim_id: saved.claim_id,
        uploaded_at: projectionTimestamp(saved.uploaded_at) ?? new Date(0).toISOString() };
    });
  }

  /** List owned uploads; no ciphertext or automatic evidence is exposed. */
  public async verificationDocuments(accountId: string): Promise<CandidateVerificationDocument[]> {
    return transaction(this.pool, async client => {
      await accountFor(client, accountId, false);
      const rows = await client.query<DocumentRow>(
        `SELECT id,filename,content_type,size_bytes,status,uploaded_at,claim_kind,claim_id FROM candidate_verification_documents
         WHERE candidate_id=$1 ORDER BY uploaded_at DESC`, [accountId]);
      return rows.rows.map(row => ({ id: row.id, filename: row.filename, content_type: row.content_type,
        size_bytes: row.size_bytes, status: row.status, claim_kind: row.claim_kind, claim_id: row.claim_id,
        uploaded_at: projectionTimestamp(row.uploaded_at) ?? new Date(0).toISOString() }));
    });
  }

  /** Download one owned encrypted document; pending status is preserved. */
  public async downloadVerificationDocument(accountId: string, documentId: string): Promise<{ filename: string; content_type: string; content: Buffer }> {
    if (!uuid(documentId)) throw invalid("invalid_document_id");
    const secret = process.env.VERIFICATION_DOCUMENT_KEY;
    if (!secret) throw new ProfileError(503, "document_encryption_unavailable");
    const key = createHash("sha256").update(secret, "utf8").digest();
    return transaction(this.pool, async client => {
      await accountFor(client, accountId, false);
      const row = (await client.query<DocumentRow>(
        `SELECT filename,content_type,nonce,ciphertext FROM candidate_verification_documents WHERE id=$1 AND candidate_id=$2`,
        [documentId, accountId],
      )).rows[0];
      if (!row || !row.nonce || !row.ciphertext) throw new ProfileError(404, "document_not_found");
      if (row.nonce.length !== 12 || row.ciphertext.length < 16) throw new ProfileError(503, "document_ciphertext_invalid");
      try {
        const decipher = createDecipheriv("aes-256-gcm", key, row.nonce);
        const tag = row.ciphertext.subarray(row.ciphertext.length - 16);
        decipher.setAuthTag(tag);
        const content = Buffer.concat([decipher.update(row.ciphertext.subarray(0, -16)), decipher.final()]);
        return { filename: row.filename, content_type: row.content_type, content };
      } catch { throw new ProfileError(503, "document_decryption_failed"); }
    });
  }

  /** Requeue the newest source revision after a terminal delivery failure. */
  public async retry(accountId: string): Promise<CandidateProfileView> {
    return transaction(this.pool, async client => {
      const account = await accountFor(client, accountId);
      const profile = await profileRow(client, accountId, true);
      if (!profile) throw new ProfileError(404, "profile_not_found");
      const current = await client.query<{ state: string }>(
        "SELECT state FROM candidate_profile_outbox WHERE candidate_id=$1 AND source_revision=$2",
        [accountId, profile.revision],
      );
      if (current.rows[0] && current.rows[0].state !== "failed") {
        return viewInTransaction(client, accountId, account, profile);
      }
      const evidence = await evidenceFor(client, accountId);
      await client.query(
        `UPDATE candidate_profile_outbox SET state='obsolete', lease_token=NULL, lease_until=NULL, updated_at=now()
          WHERE candidate_id=$1 AND source_revision<>$2 AND state IN ('queued','processing','retrying','failed')`, [accountId, profile.revision],
      );
      const reset = await client.query(
        `UPDATE candidate_profile_outbox SET state='queued', attempts=0, available_at=now(),
            projected_revision=NULL, error_code=NULL, lease_token=NULL, lease_until=NULL, delivered_at=NULL, updated_at=now()
          WHERE candidate_id=$1 AND source_revision=$2 RETURNING id`, [accountId, profile.revision],
      );
      if (reset.rowCount !== 1) await enqueue(client, account, profile, evidence);
      return viewInTransaction(client, accountId, account, profile);
    });
  }

  /** Fail closed for private pipeline/PDF reads until this profile is acknowledged. */
  public async assertProjectionCurrent(accountId: string): Promise<void> {
    const view = await this.get(accountId);
    if (!view) throw new ProfileError(404, "profile_not_found");
    if (!view.consents.processing || view.projection.state !== "delivered" || view.projection.source_revision !== view.revision) {
      throw conflict("profile_projection_stale");
    }
  }

  /**
   * Check whether an engine card is safe for public selection. Unknown legacy
   * profiles remain trusted; known source profiles need current consent and ACK.
   */
  public async isSelectionCurrent(candidateId: string, pipelineRevision: number): Promise<boolean> {
    if (!Number.isSafeInteger(pipelineRevision) || pipelineRevision < 1) return false;
    return transaction(this.pool, async client => {
      const profile = await profileRow(client, candidateId);
      if (!profile) return true;
      const row = await client.query<{ projected_revision: number | string | null; source_revision: number | string; state: string }>(
        `SELECT projected_revision, source_revision, state FROM candidate_profile_outbox
          WHERE candidate_id=$1 ORDER BY source_revision DESC, created_at DESC LIMIT 1`, [candidateId],
      );
      const latest = row.rows[0];
      return profile.processing_consent && profile.pool_consent && latest?.state === "delivered"
        && Number(latest.source_revision) === Number(profile.revision)
        && Number(latest.projected_revision) === pipelineRevision;
    });
  }

  /** Claim one durable projection job with a fenced sixty-second lease. */
  public async claimProjection(leaseSeconds = 60, maxAttempts = 5): Promise<ProfileProjectionJob | null> {
    if (!Number.isInteger(leaseSeconds) || leaseSeconds < 1) throw new RangeError("invalid projection lease");
    return transaction(this.pool, async client => {
      await client.query(
        `UPDATE candidate_profile_outbox SET state='failed', lease_token=NULL, lease_until=NULL,
             error_code='lease_exhausted', updated_at=now()
          WHERE state='processing' AND lease_until < now() AND attempts >= $1`, [maxAttempts],
      );
      const token = randomUUID();
      const result = await client.query<OutboxJobRow>(
        `WITH next AS (
           SELECT id FROM candidate_profile_outbox
            WHERE attempts < $1 AND ((state IN ('queued','retrying') AND available_at <= now())
              OR (state='processing' AND lease_until < now()))
            ORDER BY available_at, created_at, id FOR UPDATE SKIP LOCKED LIMIT 1
         )
         UPDATE candidate_profile_outbox o SET state='processing', attempts=attempts+1,
           lease_token=$2, lease_until=now() + make_interval(secs => $3), updated_at=now()
          FROM next WHERE o.id=next.id
          RETURNING o.id, o.candidate_id, o.source_revision, o.payload, o.attempts, o.lease_token, o.lease_until`,
        [maxAttempts, token, leaseSeconds],
      );
      const row = result.rows[0];
      if (!row) return null;
      return {
        job_id: row.id, candidate_id: row.candidate_id, source_revision: Number(row.source_revision),
        payload: row.payload, attempts: row.attempts, lease_token: row.lease_token,
        lease_until: timestamp(row.lease_until) ?? new Date().toISOString(),
      };
    });
  }

  /** Confirm the engine acknowledgement only while the job lease and source revision are current. */
  public async acknowledgeProjection(jobId: string, leaseToken: string, candidateId: string, sourceRevision: number, projectedRevision: number): Promise<boolean> {
    if (!uuid(candidateId) || !uuid(jobId) || !uuid(leaseToken) || !integer(sourceRevision) || sourceRevision < 1
      || !integer(projectedRevision) || projectedRevision < 1) return false;
    return transaction(this.pool, async client => {
      const result = await client.query(
        `UPDATE candidate_profile_outbox o SET state='delivered', projected_revision=$5, error_code=NULL,
             lease_token=NULL, lease_until=NULL, delivered_at=now(), updated_at=now()
          WHERE o.id=$1 AND o.lease_token=$2 AND o.candidate_id=$3 AND o.source_revision=$4
            AND o.state='processing' AND o.lease_until>now()
            AND EXISTS (SELECT 1 FROM candidate_profiles p WHERE p.candidate_id=o.candidate_id AND p.revision=o.source_revision)
          RETURNING o.id`, [jobId, leaseToken, candidateId, sourceRevision, projectedRevision],
      );
      return result.rowCount === 1;
    });
  }

  /** Persist a safe retry/terminal code while retaining lease fencing. */
  public async failProjection(jobId: string, leaseToken: string, code: string, retryable: boolean, maxAttempts = 5): Promise<boolean> {
    const safeCode = /^[a-z][a-z0-9_]{1,63}$/.test(code) ? code : "projection_failed";
    return transaction(this.pool, async client => {
      const result = await client.query(
        `UPDATE candidate_profile_outbox SET state=CASE WHEN attempts >= $4 OR NOT $5 THEN 'failed' ELSE 'retrying' END,
           available_at=CASE WHEN attempts >= $4 OR NOT $5 THEN now() ELSE now()+make_interval(secs => LEAST(30 * power(2, LEAST(attempts-1, 3)), 240)) END,
           error_code=$3, lease_token=NULL, lease_until=NULL, updated_at=now()
         WHERE id=$1 AND lease_token=$2 AND state='processing' AND lease_until>now() RETURNING id`,
        [jobId, leaseToken, safeCode, maxAttempts, retryable],
      );
      return result.rowCount === 1;
    });
  }

  /** Verify a current lease immediately before provider I/O. */
  public async assertProjectionLease(jobId: string, leaseToken: string): Promise<void> {
    const result = await this.pool.query(
      `SELECT 1 FROM candidate_profile_outbox WHERE id=$1 AND lease_token=$2 AND state='processing' AND lease_until>now()`,
      [jobId, leaseToken],
    );
    if (result.rowCount !== 1) throw new Error("projection lease is no longer valid");
  }
}

/** Short alias retained for route consumers that use the domain name. */
export { CandidateProfileStore as ProfileStore };
export { PROFILE_CONSENT_VERSION };

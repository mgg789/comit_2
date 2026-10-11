/** Fetch the authoritative append-only matching registry, with a bounded in-process cache. */

import { skillLabel, skillTestSubject, type CandidateSkillCatalog, type CandidateSkillGroup } from "@comit/shared";
interface Registry { version: number; schema_hash: string; coordinates: Array<{ group: "domain" | "stack" | "soft_skill"; key: string; index: number }> }
function validRegistry(value: unknown): value is Registry {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<Registry>;
  return Number.isSafeInteger(item.version) && Number(item.version) > 0 && typeof item.schema_hash === "string"
    && Array.isArray(item.coordinates) && item.coordinates.every(c => c && ["domain", "stack", "soft_skill"].includes(c.group)
      && typeof c.key === "string" && /^[a-z][a-z0-9_-]*$/.test(c.key) && Number.isSafeInteger(c.index));
}
let cached: { expires: number; value: CandidateSkillCatalog } | undefined;
/** Unavailable registry fails closed for new skills; unchanged legacy source values need no fetch. */
export async function activeSkillCatalog(): Promise<CandidateSkillCatalog> {
  if (cached && cached.expires > Date.now()) return cached.value;
  const token = process.env.CANDIDATE_PIPELINE_TOKEN ?? "";
  if (!token) throw new Error("skill_catalog_unavailable");
  const response = await fetch(new URL("/internal/strict-schema", process.env.MATCHING_ENGINE_URL ?? "http://matching-engine:8101"), {
    headers: { "x-internal-token": token }, signal: AbortSignal.timeout(10000), redirect: "error",
  });
  if (!response.ok) throw new Error("skill_catalog_unavailable");
  const registry: unknown = await response.json();
  if (!validRegistry(registry)) throw new Error("skill_catalog_invalid");
  const groups: Record<string, CandidateSkillGroup> = { domain: "domains", stack: "stacks", soft_skill: "soft_skills" };
  const value: CandidateSkillCatalog = { version: registry.version, schema_hash: registry.schema_hash,
    skills: registry.coordinates.map(coordinate => { const group = groups[coordinate.group]!; return {
      group, key: coordinate.key, label: skillLabel(group, coordinate.key), test_subject: skillTestSubject(group, coordinate.key),
      confirmed_level: null, next_attempt_at: null, upgrade_at: null,
    }; }) };
  cached = { expires: Date.now() + 30000, value }; return value;
}

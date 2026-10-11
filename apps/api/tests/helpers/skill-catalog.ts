/** Explicit offline registry fixture; production always reads the matching service. */
import { domainChoices, stackChoices, softChoices, skillTestSubject, type CandidateSkillCatalog, type CandidateSkillGroup } from "@comit/shared";
export async function fixtureSkillCatalog(): Promise<CandidateSkillCatalog> {
  const groups: Array<[CandidateSkillGroup, ReadonlyArray<readonly [string, string]>]> = [["domains", domainChoices], ["stacks", stackChoices], ["soft_skills", softChoices]];
  return { version: 1, schema_hash: "synthetic-active-registry", skills: groups.flatMap(([group, choices]) => choices.map(([key, label]) => ({
    group, key, label, test_subject: skillTestSubject(group, key), confirmed_level: null, next_attempt_at: null, upgrade_at: null,
  }))) };
}

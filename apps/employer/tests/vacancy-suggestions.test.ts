import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiError } from "@comit/api-client";
import type { AgentsDraftView, AgentsJobView } from "@comit/shared";
import { changedRequirements, descriptionLength, suggestVacancyRequirements, type VacancySuggestionApi } from "../src/vacancy-suggestions";

const saved: AgentsDraftView = { draft_id: "draft", vacancy_id: "vacancy", owner_id: "owner", revision: 1,
  state: "editing", fields: { title: "Frontend", stacks: { react: 5 }, domens: {}, soft_skills: {} },
  field_sources: { stacks: { source: "manual" } } };
const job = (state: AgentsJobView["state"]): AgentsJobView => ({ job_id: "job", kind: "enrichment", owner_id: "owner", state,
  payload: {}, attempts: 1, max_attempts: 3 });
const wait = async () => {};
function fixture(state: AgentsJobView["state"], latest = saved) {
  const calls: string[] = [];
  const api: VacancySuggestionApi = {
    enrichVacancy: async (_id, revision) => { calls.push(`enrich:${revision}`); return job(state); },
    getAgentJob: async () => { calls.push("poll"); return job(state); },
    getVacancy: async () => latest,
    patchVacancy: async (_id, body) => { calls.push(`fence:${body.expected_revision}`); assert.deepEqual(body.fields, {});
      return { ...latest, revision: latest.revision + 1 }; },
  };
  return { api, calls };
}
test("length indicator measures trimmed code points at explicit thresholds", () => {
  assert.equal(descriptionLength("  ").tone, "short");
  assert.equal(descriptionLength("я".repeat(160)).tone, "medium");
  assert.equal(descriptionLength("я".repeat(400)).tone, "detailed");
  assert.equal(descriptionLength("🙂").length, 1);
});
test("untouched empty/inferred groups remain omitted, edits and removals are manual", () => {
  assert.deepEqual(changedRequirements(undefined, { stacks: {}, domens: {}, soft_skills: {} }), {});
  assert.deepEqual(changedRequirements(undefined, { stacks: {}, domens: {}, soft_skills: {} }, new Set(["stacks"])), { stacks: {} });
  assert.deepEqual(changedRequirements(saved, { stacks: { react: 5 }, domens: {}, soft_skills: {} }), {});
  assert.deepEqual(changedRequirements(saved, { stacks: {}, domens: { backend: 4 }, soft_skills: {} }), { stacks: {}, domens: { backend: 4 } });
});
test("cached success loads suggestions and fences the review while retaining manual stacks", async () => {
  const latest: AgentsDraftView = { ...saved, revision: 2, fields: { ...saved.fields, domens: { frontend: 4 } },
    field_sources: { ...saved.field_sources, domens: { source: "laya" } } };
  const { api, calls } = fixture("succeeded", latest);
  const result = await suggestVacancyRequirements(api, saved, { wait });
  assert.equal(result.suggested, true);
  assert.deepEqual(result.draft.fields.stacks, { react: 5 });
  assert.deepEqual(result.draft.fields.domens, { frontend: 4 });
  assert.deepEqual(calls, ["enrich:1", "fence:2"]);
});
test("timeout and ambiguous enqueue fence late writes before manual fallback", async () => {
  const first = fixture("processing");
  assert.equal((await suggestVacancyRequirements(first.api, saved, { wait, polls: 2 })).suggested, false);
  assert.deepEqual(first.calls, ["enrich:1", "poll", "poll", "fence:1"]);
  first.api.enrichVacancy = async () => { throw new Error("lost reply"); };
  assert.equal((await suggestVacancyRequirements(first.api, saved, { wait })).draft.revision, 2);
});
test("fence retries a worker CAS race but never overwrites another manual edit", async () => {
  const { api } = fixture("failed");
  const patch = api.patchVacancy; let attempts = 0;
  api.patchVacancy = async (...args) => { if (attempts++ === 0) throw new ApiError(409, "test", {}); return patch(...args); };
  await suggestVacancyRequirements(api, saved, { wait });
  assert.equal(attempts, 2);
  api.getVacancy = async () => ({ ...saved, revision: 2, fields: { ...saved.fields, stacks: { react: 7 } } });
  await assert.rejects(suggestVacancyRequirements(api, saved, { wait }), /другом окне/);
});

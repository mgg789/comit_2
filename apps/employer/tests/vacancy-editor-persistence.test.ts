import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import { ApiError } from "@comit/api-client";
import type { AgentsDraftView } from "@comit/shared";

// Execute the production persistence handler without mounting the editor or replacing its logic.
const source = readFileSync(new URL("../src/vacancies.tsx", import.meta.url), "utf8");
const handler = source.slice(source.indexOf("async function persistFields()"), source.indexOf("  function showSaveError"));
const executable = ts.transpileModule(`${handler}\npersistFields`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const saved: AgentsDraftView = { draft_id: "known", vacancy_id: "vacancy", owner_id: "owner", revision: 7,
  state: "editing", fields: { title: "Frontend" } };
function fixture(createdId = "known") {
  const calls: string[] = [];
  const api = {
    getVacancy: async (id: string) => { calls.push(`get:${id}`); return saved; },
    patchVacancy: async (id: string, body: { expected_revision: number }) => { calls.push(`patch:${id}:${body.expected_revision}`); return saved; },
    createVacancy: async () => { calls.push("create"); return saved; },
  };
  let uncertain = false;
  let remembered = "";
  const persist: () => Promise<AgentsDraftView> = runInNewContext(executable, { api, ApiError, createdId,
    current: null, from: "100", to: "150", title: "Frontend", specialization: "frontend", city: "Москва", daily: "fulltime", work: "remote", bio: "Описание",
    skills: {}, domains: {}, softSkills: {}, manuallyEditedRequirements: { current: new Set() }, changedRequirements: () => ({}),
    setCurrent: () => {}, setCreatedId: (id: string) => { remembered = id; }, setCreationUncertain: (value: boolean) => { uncertain = value; } });
  return { api, calls, persist, uncertain: () => uncertain, remembered: () => remembered };
}
test("reopened new vacancy reuses the server draft and its fresh CAS revision", async () => {
  const f = fixture();
  assert.equal(await f.persist(), saved);
  assert.deepEqual(f.calls, ["get:known", "patch:known:7"]);
});
test("failed reads of a known draft never create a duplicate", async () => {
  for (const error of [new Error("network"), new ApiError(503, "unavailable", {}), new ApiError(404, "missing", {})]) {
    const f = fixture(); f.api.getVacancy = async () => { throw error; };
    await assert.rejects(f.persist(), error);
    assert.equal(f.calls.includes("create"), false);
  }
});
test("published or archived persisted draft requires opening its existing editor", async () => {
  for (const result of [{ ...saved, business_status: "open" as const }, { ...saved, archived: true }]) {
    const f = fixture(); f.api.getVacancy = async () => result;
    await assert.rejects(f.persist(), /уже изменён/);
    assert.deepEqual(f.calls, []);
  }
});
test("successful first creation remembers its identity; ambiguous creation still blocks retry", async () => {
  const f = fixture(""); await f.persist(); assert.equal(f.remembered(), "known");
  const failing = fixture(""); failing.api.createVacancy = async () => { throw new Error("lost response"); };
  await assert.rejects(failing.persist(), /Черновик мог сохраниться/);
  assert.equal(failing.uncertain(), true);
});

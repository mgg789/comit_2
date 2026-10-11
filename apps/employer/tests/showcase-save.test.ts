import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import { ApiError } from "@comit/api-client";

// Execute the actual save handler without mounting the SPA or replacing its transitions.
const source = readFileSync(new URL("../src/discovery.tsx", import.meta.url), "utf8");
const handler = source.slice(source.indexOf("async function save(candidate:"), source.indexOf("  const visible = orderCandidates("));
const executable = ts.transpileModule(`${handler}\nsave`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;

function fixture(detail?: string) {
  const paths: string[] = [], notices: string[] = [], saved: unknown[] = [];
  let closed = false;
  const saveLock = { current: false };
  const save = runInNewContext(executable, {
    ApiError, crypto: { randomUUID: () => "key" }, saveLock,
    open: [{ draft_id: "draft" }], likeKeys: { current: new Map() },
    api: { likeShowcaseCandidate: async () => {
      if (detail) throw new ApiError(409, "/employer/x", { detail });
    } },
    mismatchFrom: () => null, errorText: () => "visible error",
    navigate: (path: string) => paths.push(path),
    setChoosing: (value: unknown) => { closed = value === null; },
    setBusy: () => {}, setNotice: (value: string) => notices.push(value),
    setSaved: (update: (value: unknown[]) => unknown[]) => saved.push(...update([])),
  }) as (candidate: { candidate_id: string; profile_revision: number }, target: string) => Promise<void>;
  return { save, paths, notices, saved, saveLock, closed: () => closed };
}

test("already saved candidate opens the vacancy-owned resume for invitation without another save", async () => {
  const f = fixture("showcase_candidate_already_saved");
  await f.save({ candidate_id: "candidate", profile_revision: 1 }, "draft");
  assert.deepEqual(f.paths, ["/resume/candidate?draft=draft"]);
  assert.equal(f.saved.length, 0);
  assert.equal(f.closed(), true);
  assert.equal(f.saveLock.current, false);
});

test("new manual addition saves normally without automatic navigation", async () => {
  const f = fixture();
  await f.save({ candidate_id: "candidate", profile_revision: 1 }, "draft");
  assert.deepEqual(f.paths, []);
  assert.deepEqual(f.saved, ["draft:candidate"]);
  assert.equal(f.closed(), true);
});

for (const code of ["showcase_candidate_invitation_pending", "showcase_candidate_already_visited", "showcase_source_obsolete"]) {
  test(`blocked addition keeps the modal and reports ${code}`, async () => {
    const f = fixture(code);
    await f.save({ candidate_id: "candidate", profile_revision: 1 }, "draft");
    assert.deepEqual(f.paths, []);
    assert.equal(f.closed(), false);
    assert.equal(f.notices.at(-1), "visible error");
    assert.equal(f.saveLock.current, false);
  });
}

import assert from "node:assert/strict";
import { test } from "node:test";
import { confirmedTestGrade, skillTestSubject } from "../src/testing-launch";

test("only available subjects get a test action", () => {
  assert.equal(skillTestSubject("React.js"), "react");
  assert.equal(skillTestSubject("REST API"), "rest_api");
  assert.equal(skillTestSubject("Java"), null);
});
test("only a confirmed known grade is shown, never raw private evidence", () => {
  const evidence = { input_hash: "hash", status: "confirmed" as const, verified_value: { grade: "middle", session_id: "private" }, provenance: [], checked_at: null };
  assert.equal(confirmedTestGrade(evidence), "Middle");
  assert.equal(confirmedTestGrade({ ...evidence, verified_value: { grade: "private" } }), null);
  assert.equal(confirmedTestGrade(undefined), null);
});

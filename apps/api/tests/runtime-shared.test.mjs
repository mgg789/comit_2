/** Verify the shared runtime contract works in plain Node without TS loaders. */
import assert from "node:assert/strict";
import test from "node:test";
import { GRADES, addWorkingDays, rejectionReasonCategories } from "@comit/shared";
test("compiled shared exports are available to production Node", () => {
  assert.ok(GRADES.includes("senior"));
  assert.ok(rejectionReasonCategories.length > 0);
  assert.equal(typeof addWorkingDays, "function");
});

import assert from "node:assert/strict";
import test from "node:test";
import { hasCurrentWeeklyActivity } from "../src/weekly-activity";

test("weekly flame requires an earned current reward and expires at the UTC boundary", () => {
  const expires = "2026-10-12T00:00:00Z";
  assert.equal(hasCurrentWeeklyActivity(expires, Date.parse("2026-10-11T23:59:59Z")), true);
  assert.equal(hasCurrentWeeklyActivity(expires, Date.parse("2026-10-12T00:00:00Z")), false);
  assert.equal(hasCurrentWeeklyActivity(expires, Date.parse("2026-10-04T00:00:00Z")), false);
  assert.equal(hasCurrentWeeklyActivity(undefined, Date.now()), false);
  assert.equal(hasCurrentWeeklyActivity("invalid", Date.now()), false);
  assert.equal(hasCurrentWeeklyActivity(null, Date.now()), false);
});

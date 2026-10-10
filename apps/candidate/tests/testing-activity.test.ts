import assert from "node:assert/strict";
import { test } from "node:test";
import { testingActivityCalendar, testingActivityIntensity } from "../src/testing-activity.js";

test("calendar keeps unavailable days separate from an actual zero and aligns Monday-first", () => {
  const days = [{ date: "2026-09-11", count: null }, { date: "2026-09-12", count: 0 }, { date: "2026-09-13", count: 3 }];
  const calendar = testingActivityCalendar(days);
  assert.equal(calendar.length, 7);
  assert.deepEqual(calendar.slice(0, 4), [null, null, null, null]);
  assert.deepEqual(calendar.slice(4), days);
  assert.equal(calendar[4]?.count, null);
  assert.equal(calendar[5]?.count, 0);
});

test("test counts map to bounded intensity and empty calendars remain empty", () => {
  assert.deepEqual([null, 0, 1, 2, 4, 8].map(testingActivityIntensity), [0, 0, 1, 2, 4, 4]);
  assert.deepEqual(testingActivityCalendar([]), []);
});

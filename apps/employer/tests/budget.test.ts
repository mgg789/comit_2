/** AI budget share shown in the agent tools; amounts are decimal micro-USD strings. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { budgetRemainingPercent } from "../src/budget";

test("shows the available share of the rolling limit", () => {
  assert.equal(budgetRemainingPercent({ limit_micro_usd: "1000000", available_micro_usd: "250000" }), 25);
  assert.equal(budgetRemainingPercent({ limit_micro_usd: "3000000", available_micro_usd: "1000000" }), 33);
});

test("clamps overspend and survives a missing or broken limit", () => {
  assert.equal(budgetRemainingPercent({ limit_micro_usd: "1000000", available_micro_usd: "-5" }), 0);
  assert.equal(budgetRemainingPercent({ limit_micro_usd: "1000000", available_micro_usd: "2000000" }), 100);
  assert.equal(budgetRemainingPercent({ limit_micro_usd: "0", available_micro_usd: "0" }), null);
  assert.equal(budgetRemainingPercent({ limit_micro_usd: "n/a", available_micro_usd: "1" }), null);
  assert.equal(budgetRemainingPercent({ limit_micro_usd: "100", available_micro_usd: "n/a" }), null);
});

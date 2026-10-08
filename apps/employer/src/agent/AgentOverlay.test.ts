import { describe, expect, it } from "vitest";
import { budgetUsage } from "./AgentOverlay";

const budget = { limit_micro_usd: "1000000", spent_micro_usd: "150000", reserved_micro_usd: "50000", available_micro_usd: "800000", pricing_configured: true };

describe("budgetUsage", () => {
  it("counts spent and reserved micro-USD against the rolling limit", () => {
    expect(budgetUsage(budget)).toBeCloseTo(0.2);
  });

  it("clamps overspend and survives a missing limit", () => {
    expect(budgetUsage({ ...budget, spent_micro_usd: "5000000" })).toBe(1);
    expect(budgetUsage({ ...budget, limit_micro_usd: "0" })).toBe(0);
  });
});

import { describe, expect, it } from "vitest";
import { changePercent, periodRange } from "./StatisticsPage";

describe("periodRange", () => {
  it("builds an inclusive UTC range ending today", () => {
    expect(periodRange(7, new Date("2026-10-08T23:30:00Z"))).toEqual({ from: "2026-10-02", to: "2026-10-08" });
    expect(periodRange(1, new Date("2026-10-08T00:00:00Z"))).toEqual({ from: "2026-10-08", to: "2026-10-08" });
  });
});

describe("changePercent", () => {
  it("compares with the previous period and has no value without a base", () => {
    expect(changePercent(29, 25)).toBe(16);
    expect(changePercent(5, 10)).toBe(-50);
    expect(changePercent(3, 0)).toBeNull();
  });
});

import { describe, expect, it } from "vitest";
import { changePercent, periodRange, vacancyStage } from "./StatisticsPage";

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

describe("vacancyStage", () => {
  const zero = { delivered: 0, viewed: 0, liked: 0, disliked: 0, invited: 0, accepted: 0, interviewed: 0, offered: 0, hired: 0 };
  it("names the furthest milestone of a vacancy", () => {
    expect(vacancyStage(zero).label).toBe("Старт");
    expect(vacancyStage({ ...zero, delivered: 12, liked: 3 }).label).toBe("Лайки");
    expect(vacancyStage({ ...zero, liked: 3, interviewed: 1 }).label).toBe("Собеседование");
    expect(vacancyStage({ ...zero, offered: 1 }).label).toBe("Оффер");
  });
});

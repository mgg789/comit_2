import type { ScoreBreakdown } from "@comit/shared";
import { describe, expect, it } from "vitest";
import { describeFeature, filterFactorLabels, matchPercent, requirementWeightLabel } from "./explain";

const breakdown: ScoreBreakdown = {
  rating_score: 0.8,
  strict_score: 0.7,
  semantic_cosine: null,
  semantic_unavailable_reason: "vacancy_bio_missing",
  rating_contribution: 0.45,
  strict_contribution: 0.42,
  semantic_contribution: 0,
  score: 0.874,
  top_matched_features: [],
};

describe("matchPercent", () => {
  it("shows the server score as a bounded whole percent", () => {
    expect(matchPercent(breakdown)).toBe(87);
    expect(matchPercent({ ...breakdown, score: 1.2 })).toBe(100);
    expect(matchPercent({ ...breakdown, score: -0.1 })).toBe(0);
  });
});

describe("filterFactorLabels", () => {
  it("translates strict-filter codes, hides technical ones and keeps unknown codes visible", () => {
    expect(filterFactorLabels(["class_match", "strict_schema_current", "same_city", "new_factor"])).toEqual([
      "Направление совпадает",
      "Тот же город",
      "new_factor",
    ]);
  });
});

describe("requirementWeightLabel", () => {
  it("names the 0..7 requirement scale", () => {
    expect(requirementWeightLabel(7)).toBe("Обязателен");
    expect(requirementWeightLabel(4)).toBe("Важен");
    expect(requirementWeightLabel(2)).toBe("Средний вес");
    expect(requirementWeightLabel(1)).toBe("Желателен");
  });
});

describe("describeFeature", () => {
  it("states candidate level against the target with evidence status", () => {
    expect(
      describeFeature({ index: 0, group: "stack", key: "go", target_level: 6, candidate_level: 5, status: "confirmed", agreement: 0.86, confidence: 1, contribution: 0.1 }),
    ).toBe("go: 5/7 при цели 6/7 · подтверждено");
  });
});

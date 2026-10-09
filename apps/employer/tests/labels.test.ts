/** Matching keys are shown as readable labels; unknown keys stay visible instead of disappearing. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { featureLabel, filterReasonLabel, specializationLabel } from "../src/labels";

test("labels stack, domain and soft-skill keys", () => {
  assert.equal(featureLabel("react"), "React");
  assert.equal(featureLabel("rest_api"), "REST API");
  assert.equal(featureLabel("teamwork"), "Командная работа");
  assert.equal(featureLabel("brand_new_key"), "brand_new_key");
});

test("labels specializations and strict-filter reasons", () => {
  assert.equal(specializationLabel("frontend"), "Frontend");
  assert.equal(specializationLabel("unknown"), "unknown");
  assert.equal(filterReasonLabel("class_mismatch"), "Другая специализация");
  assert.equal(filterReasonLabel("salary_disjoint"), "Зарплатные ожидания не пересекаются с вилкой");
  assert.equal(filterReasonLabel("future_reason"), "future_reason");
});

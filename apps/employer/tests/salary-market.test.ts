import assert from "node:assert/strict";
import { test } from "node:test";
import { estimatePlatformSalaryMarket, estimateSalaryMarket } from "../src/salary-market";
import type { SalaryMarketAggregate } from "@comit/shared";

const platform: SalaryMarketAggregate = { specialization: "backend", grade: "middle", stacks: ["python"],
  sample_count: 8, minimum_sample_count: 3, average_salary_rub: 200000 };

test("platform vacancy aggregate is primary and compares the entered range midpoint", () => {
  const result = estimatePlatformSalaryMarket(platform, "160", "240");
  assert.equal(result?.referenceRub, 200000);
  assert.equal(result?.deviationPercent, 0);
  assert.equal(result?.label, "Средняя по рынку");
  assert.equal(result?.source, "platform");
  assert.equal(estimatePlatformSalaryMarket(platform, "100", "140")?.label, "Ниже рынка");
  assert.equal(estimatePlatformSalaryMarket(platform, "260", "300")?.label, "Выше рынка");
  assert.equal(estimatePlatformSalaryMarket({ ...platform, average_salary_rub: null }, "160", "240"), null);
});

test("Habr external fallback remains available and identifies its all-grades limitation", () => {
  const result = estimateSalaryMarket("backend", { python: 4, docker: 3 }, "200", "283");
  assert.equal(result?.referenceRub, 241500);
  assert.equal(result?.label, "Средняя по рынку");
  assert.equal(result?.basis, "direction_and_language");
  assert.equal(result?.source, "habr_fallback");
  assert.equal(estimateSalaryMarket("qa", { python: 5 }, "163", "163")?.referenceRub, 163000);
  assert.equal(estimateSalaryMarket("unsupported", {}, "100", "200"), null);
});

test("invalid and reversed salary inputs do not receive a label", () => {
  for (const [from, to] of [["", "200"], ["300", "200"], ["NaN", "200"], ["1e20", "1e20"]]) {
    assert.equal(estimatePlatformSalaryMarket(platform, from, to), null);
    assert.equal(estimateSalaryMarket("backend", {}, from, to), null);
  }
});

import assert from "node:assert/strict";
import { test } from "node:test";
import { estimateSalaryMarket } from "../src/salary-market";
test("salary interval midpoint uses whole RUB and the selected direction/language", () => {
  const result = estimateSalaryMarket("backend", { python: 4, docker: 3 }, "200", "283");
  assert.equal(result?.referenceRub, 241500);
  assert.equal(result?.deviationPercent, 0);
  assert.equal(result?.label, "Средняя по рынку");
  assert.equal(result?.basis, "direction_and_language");
  assert.equal(estimateSalaryMarket("frontend", { react: 4 }, "100", "150")?.label, "Ниже рынка");
  assert.equal(estimateSalaryMarket("mobile", {}, "400", "500")?.label, "Выше рынка");
});
test("neutral boundaries are inclusive and auxiliary languages do not skew QA", () => {
  assert.equal(estimateSalaryMarket("fullstack", {}, "160", "160")?.label, "Средняя по рынку");
  assert.equal(estimateSalaryMarket("fullstack", {}, "240", "240")?.label, "Средняя по рынку");
  assert.equal(estimateSalaryMarket("qa", { python: 5 }, "163", "163")?.referenceRub, 163000);
});
test("empty, reversed, invalid, unsupported and unsafe intervals have no estimate", () => {
  for (const [direction, from, to] of [["backend", "", "200"], ["backend", "300", "200"], ["backend", "NaN", "200"], ["ml", "100", "200"], ["backend", "0", "200"], ["backend", "1e20", "1e20"]]) {
    assert.equal(estimateSalaryMarket(direction, {}, from, to), null);
  }
});

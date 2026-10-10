import assert from "node:assert/strict";
import test from "node:test";
import { candidateCode, isShowcaseSort, orderCandidates } from "../src/showcase-order";

const card = (id: string, from: number, to: number, grade: "intern" | "junior" | "middle" | "senior") => ({
  candidate_id: id,
  salary: { from_rub: from, to_rub: to },
  category: { grade },
});
const pool = [
  card("a", 180_000, 260_000, "middle"),
  card("b", 150_000, 230_000, "senior"),
  card("c", 150_000, 200_000, "junior"),
  card("d", 200_000, 300_000, "middle"),
];
const ids = (items: { candidate_id: string }[]) => items.map((item) => item.candidate_id).join("");

test("relevance keeps the server order and never mutates the page", () => {
  assert.equal(ids(orderCandidates(pool, "relevance")), "abcd");
  orderCandidates(pool, "salary_asc");
  assert.equal(ids(pool), "abcd");
});

test("salary sorts by the lower bound upwards and the upper bound downwards", () => {
  assert.equal(ids(orderCandidates(pool, "salary_asc")), "cbad");
  assert.equal(ids(orderCandidates(pool, "salary_desc")), "dabc");
});

test("grade puts senior first and keeps relevance order on ties", () => {
  assert.equal(ids(orderCandidates(pool, "grade_desc")), "badc");
});

test("anonymous candidate codes are recognised, ordinary queries are not", () => {
  assert.equal(candidateCode("0A40"), "0a40");
  assert.equal(candidateCode("  Кандидат 0a40 "), "0a40");
  assert.equal(candidateCode("кандидат2838fd"), "2838fd");
  assert.equal(candidateCode("react"), null);
  assert.equal(candidateCode("Нижний Новгород"), null);
  assert.equal(candidateCode("abc"), null);
  assert.equal(candidateCode("cafe"), null);
  assert.equal(candidateCode("Кандидат CAFE"), "cafe");
});

test("stored sort values are validated", () => {
  assert.equal(isShowcaseSort("salary_desc"), true);
  assert.equal(isShowcaseSort("price"), false);
  assert.equal(isShowcaseSort(undefined), false);
});

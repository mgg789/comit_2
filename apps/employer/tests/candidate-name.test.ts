/** Anonymous candidate handles (TZ §2.2): readable, never a raw identifier. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { candidateName } from "../src/candidate-name";

test("turns the anonymous matching label into a short handle", () => {
  assert.equal(candidateName("Candidate 28382fdd-d094-4444-b779-712c3e7859c1"), "Кандидат 2838");
  assert.equal(candidateName("Candidate C00D6001-6DE6-4D17-A9FE-7AD73CF1978E"), "Кандидат C00D");
});

test("keeps real names and falls back for empty values", () => {
  assert.equal(candidateName("Александра Ковалева"), "Александра Ковалева");
  assert.equal(candidateName("Candidate Петров"), "Candidate Петров");
  assert.equal(candidateName(undefined), "Кандидат");
  assert.equal(candidateName("  ", "Анонимный профиль"), "Анонимный профиль");
});

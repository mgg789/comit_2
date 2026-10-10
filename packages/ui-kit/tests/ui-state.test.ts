import assert from "node:assert/strict";
import test from "node:test";
import { validUiState } from "../src/ui-state";

test("non-secret UI drafts accept bounded JSON and reject corrupted or prototype-bearing storage", () => {
  assert.equal(validUiState({ query: "React", filters: ["remote"], level: 4 }), true);
  assert.equal(validUiState(JSON.parse('{"constructor":{"prototype":{"polluted":true}}}')), false);
  assert.equal(validUiState({ level: Number.POSITIVE_INFINITY }), false);
  assert.equal(validUiState({ file: new Date() }), false);
  assert.equal(validUiState({ text: "x".repeat(128001) }), false);
  let deep: unknown = "leaf";
  for (let level = 0; level < 18; level++) deep = { child: deep };
  assert.equal(validUiState(deep), false);
});

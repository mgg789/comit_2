import assert from "node:assert/strict";
import test from "node:test";
import { countLabel } from "../src/plural";

const offers = ["предложение", "предложения", "предложений"] as const;

test("offer counter agrees with the number", () => {
  assert.equal(countLabel(1, offers), "1 предложение");
  assert.equal(countLabel(3, offers), "3 предложения");
  assert.equal(countLabel(5, offers), "5 предложений");
  assert.equal(countLabel(11, offers), "11 предложений");
  assert.equal(countLabel(21, offers), "21 предложение");
  assert.equal(countLabel(0, offers), "0 предложений");
});

test("word form alone follows the same rules", async () => {
  const { pluralWord } = await import("../src/plural");
  assert.equal(pluralWord(1, ["принятое приглашение", "принятых приглашения", "принятых приглашений"]), "принятое приглашение");
  assert.equal(pluralWord(2, ["приглашение", "приглашения", "приглашений"]), "приглашения");
});

import assert from "node:assert/strict";
import { test } from "node:test";
import type { RecruitmentView } from "@comit/shared";
import { mayReadCandidateName, recruitmentChatTitle } from "../src/recruitment-title";
const process = (state: RecruitmentView["state"]) => ({ state, terms: { vacancy_title: "Python Backend" } as RecruitmentView["terms"] });
test("only accepted processes display the authorized candidate name", () => {
  assert.equal(recruitmentChatTitle(process("accepted"), " Алексей Соколов "), "Python Backend — Алексей Соколов");
  assert.equal(recruitmentChatTitle(process("sent"), "Private name"), "Python Backend");
  assert.equal(recruitmentChatTitle(process("withdrawn"), "Private name"), "Python Backend");
  assert.equal(recruitmentChatTitle(process("accepted")), "Python Backend");
  assert.equal(mayReadCandidateName("viewed"), false);
  assert.equal(mayReadCandidateName("hired"), true);
});

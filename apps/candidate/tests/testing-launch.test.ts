import assert from "node:assert/strict";
import { test } from "node:test";
import { skillTestSubject, skillLevelLabel, TEST_GRADE_LEVELS, type CandidateSkillCatalogItem } from "@comit/shared";
import { skillTestAction } from "../src/testing-launch";

const skill: CandidateSkillCatalogItem = { group: "stacks", key: "react", label: "React", test_subject: "react", confirmed_level: null, next_attempt_at: null, upgrade_at: null };
test("test actions bind exact canonical coordinates, never arbitrary aliases or the wrong group", () => {
  assert.equal(skillTestSubject("stacks", "react"), "react");
  assert.equal(skillTestSubject("domains", "react"), null);
  assert.equal(skillTestSubject("stacks", "React.js"), null);
  assert.equal(skillTestSubject("domains", "rest_api"), "rest_api");
  assert.equal(skillTestSubject("soft_skills", "teamwork"), "teamwork");
  assert.deepEqual(Object.values(TEST_GRADE_LEVELS), [1, 2, 4, 6]);
  assert.equal(skillLevelLabel(3), "Junior+"); assert.equal(skillLevelLabel(7), "Senior+");
});
test("skill actions reflect server availability and distinguish confirmation from upgrade", () => {
  const now = Date.parse("2026-10-11T00:00:00Z");
  assert.deepEqual(skillTestAction(skill, now), { label: "Подтвердить", mode: "retake", disabled: false });
  assert.equal(skillTestAction({ ...skill, test_subject: null }, now).label, "Пока нет теста");
  assert.equal(skillTestAction({ ...skill, confirmed_level: 4, upgrade_at: "2026-10-12T00:00:01Z" }, now).label, "Тест через 2 дн.");
  assert.deepEqual(skillTestAction({ ...skill, confirmed_level: 4, upgrade_at: "2026-10-10T00:00:00Z" }, now), { label: "Улучшить", mode: "upgrade", disabled: false });
});

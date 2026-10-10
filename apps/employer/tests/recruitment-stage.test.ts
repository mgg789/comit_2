/** Progression labels never bypass the server's action availability. */
import assert from "node:assert/strict";
import { test } from "node:test";
import type { RecruitmentView } from "@comit/shared";
import { recruitmentStage, recruitmentTileAction } from "../src/recruitment-stage";
const accepted = { state: "accepted" as const, offers: [], interviews: [], allowed_actions: ["interview_propose", "offer_send"] satisfies RecruitmentView["allowed_actions"], chat_available: true };
test("a like invites; an accepted invitation opens the interview form", () => {
  assert.equal(recruitmentStage(), 0);
  assert.deepEqual(recruitmentTileAction(), { label: "Пригласить", command: "invite", disabled: false });
  assert.equal(recruitmentTileAction(accepted).command, "interview_propose");
  assert.equal(recruitmentTileAction({ ...accepted, state: "sent", allowed_actions: [] }).disabled, true);
});
test("proposed interviews advance the badge and expose the offer form", () => {
  const process = { ...accepted, interviews: [{ state: "proposed" as const }] };
  assert.equal(recruitmentStage(process), 2);
  assert.deepEqual(recruitmentTileAction(process), { label: "Сделать оффер", command: "offer_send", disabled: false });
  assert.equal(recruitmentTileAction({ ...process, allowed_actions: [] }).disabled, true);
});
test("offers, hires and terminal decisions only open permitted chat", () => {
  assert.equal(recruitmentTileAction({ ...accepted, state: "offer_sent", offers: [{}] }).command, "chat");
  assert.equal(recruitmentStage({ ...accepted, state: "hired" }), 4);
  assert.equal(recruitmentTileAction({ ...accepted, state: "rejected" }).command, "chat");
  assert.equal(recruitmentTileAction({ ...accepted, state: "rejected", chat_available: false }).disabled, true);
});

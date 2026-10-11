import assert from "node:assert/strict";
import { test } from "node:test";
import { uploadAchievementFile, profileFieldsForSave } from "../src/achievement-documents";

test("achievement upload rejects unsupported/empty/oversize files before network and sends private claim-bound metadata", async () => {
  const previous = globalThis.fetch;
  const sent: Array<{ path: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (input, init) => {
    sent.push({ path: String(input), body: JSON.parse(String(init?.body)) as Record<string, unknown> });
    return Response.json({ id: "fixture-document" });
  };
  try {
    for (const file of [new File([], "empty.pdf", { type: "application/pdf" }), new File(["text"], "unsupported.txt", { type: "text/plain" }), new File([new Uint8Array(10 * 1024 * 1024 + 1)], "oversize.pdf", { type: "application/pdf" })]) {
      await assert.rejects(uploadAchievementFile(file, "fixture-claim"));
    }
    assert.equal(sent.length, 0);
    await uploadAchievementFile(new File(["%PDF-fixture"], "evidence.pdf", { type: "application/pdf" }), "fixture-claim");
    assert.equal(sent.length, 1);
    assert.equal(sent[0]?.path, "/api/candidate/profile/verification-documents");
    assert.deepEqual(sent[0]?.body, { filename: "evidence.pdf", content_type: "application/pdf", content_base64: btoa("%PDF-fixture"), claim_kind: "achievements", claim_id: "fixture-claim" });
    assert.equal("status" in (sent[0]?.body ?? {}), false);
    assert.equal("candidate_id" in (sent[0]?.body ?? {}), false);
  } finally { globalThis.fetch = previous; }
});

test("new narratives use server-assigned IDs while existing owner claims retain identity", () => {
  const existing = { id: "server-owner-id", title: "Existing" };
  const pending = { id: "temporary-editor-id", title: "New award" };
  const result = profileFieldsForSave({ achievements: [pending, existing] }, { achievements: [existing] });
  assert.deepEqual(result.achievements, [{ title: "New award" }, existing]);
  assert.equal(pending.id, "temporary-editor-id");
  assert.deepEqual(profileFieldsForSave({ projects: [pending] }, undefined).projects, [{ title: "New award" }]);
});

/** Full candidate auth -> real Python bank -> media -> trusted profile result. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { startTestingBrowserFixture } from "./helpers/testing-browser-fixture.js";

const databaseUrl = process.env.TESTING_BROWSER_TEST_DATABASE_URL;
test("real HTTP grade workflow stores scoped evidence and leaves the global grade intact", { skip: !databaseUrl }, async () => {
  const fixture = await startTestingBrowserFixture(databaseUrl!);
  try {
    const post = async (path: string, payload: unknown, token?: string) => fetch(fixture.apiUrl + path, {
      method: "POST", headers: { "content-type": "application/json", origin: "http://localhost", ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: JSON.stringify(payload) });
    const receipt = await (await post("/auth/candidate/code", { email: "selftest@example.invalid" })).json();
    assert.match(receipt.dev_code, /^[0-9]{6}$/);
    const login = await (await post("/auth/candidate/verify", { email: "selftest@example.invalid", challenge_id: receipt.challenge_id,
      code: receipt.dev_code, consent: { processing: true, version: "account-v1" } })).json();
    const token: string = login.access_token;
    assert.ok(token);
    const get = async (path: string) => fetch(fixture.apiUrl + path, { headers: { authorization: `Bearer ${token}` } });
    const preflight = await (await get("/candidate/testing/preflight?specialization=react")).json();
    assert.equal(preflight.eligible, true); assert.equal(preflight.recording_enabled, true);
    const created = await (await post("/candidate/testing/sessions", { specialization: "react", consents: {
      version: "testing-media-v1", processing: true, screen_recording: true, video_analysis: false } }, token)).json();
    const sid: string = created.session_id; assert.ok(sid);
    assert.equal((await fetch(fixture.engineUrl + `/testing/sessions/${sid}/result`)).status, 401);
    const bank = JSON.parse(await readFile(join(fixture.mediaDir, "items.json"), "utf8"));
    let typed = false;
    for (let step = 0; step < 30; step++) {
      const response = await get(`/candidate/testing/sessions/${sid}/next`);
      if (response.status === 409) break;
      assert.equal(response.status, 200);
      const question = (await response.json()).task;
      assert.equal(question.tests, undefined); assert.equal(question.expected_answers, undefined); assert.equal(question.answer_index, undefined);
      const row = bank.find((item: { item_id: string }) => item.item_id === question.item_id);
      const answer = question.type === "mcq" ? row.payload.answer_index : row.payload.expected_answers[0];
      typed ||= question.type === "short_answer";
      const answered = await post(`/candidate/testing/sessions/${sid}/answer`, { item_id: question.item_id, answer }, token);
      assert.equal(answered.status, 200);
      const result = await answered.json(); assert.equal(result.correct, true); assert.equal(result.judge_status, "auto");
      if (result.status === "finished") break;
    }
    assert.equal(typed, true);
    const chunk = { kind: "screen", sequence: 0, data_base64: Buffer.from("synthetic-recording-fixture").toString("base64") };
    assert.equal((await post(`/candidate/testing/sessions/${sid}/media`, chunk, token)).status, 200);
    assert.equal((await post(`/candidate/testing/sessions/${sid}/supervision`, { total_seconds: 4, in_frame_seconds: 0,
      focus_lost_seconds: 0, multi_face_events: 0, camera: "denied" }, token)).status, 200);
    const resumedResponse = await get("/candidate/testing/preflight?specialization=react");
    assert.equal(resumedResponse.status, 200);
    const resumed = await resumedResponse.json();
    assert.equal(resumed.next_sequence, 1);
    assert.equal(resumed.active_session_consents.video_analysis, false);
    assert.deepEqual(resumed.active_supervision, { total_seconds: 4, in_frame_seconds: 0,
      focus_lost_seconds: 0, multi_face_events: 0, camera: "denied" });
    assert.equal(resumed.active_supervision.badge, undefined);
    assert.equal(resumed.active_supervision.uploaded_at, undefined);
    const finishedResponse = await post(`/candidate/testing/sessions/${sid}/finish`, {}, token);
    assert.equal(finishedResponse.status, 200);
    const finished = await finishedResponse.json(); assert.equal(finished.grade_publication.published, true); assert.equal(finished.capture_finalized, true);
    assert.equal((await post(`/candidate/testing/sessions/${sid}/finish`, {}, token)).status, 200);
    const profile = await fixture.profiles.get(fixture.candidateId);
    assert.equal(profile?.fields.declared_grade, "middle");
    const evidence = (await fixture.pool.query("SELECT evidence_key,verified_value FROM candidate_profile_evidence WHERE candidate_id=$1 AND evidence_key='grade:react'", [fixture.candidateId])).rows[0];
    assert.equal(evidence.verified_value.grade, finished.decided_grade);
    assert.equal(evidence.verified_value.camera_status, "denied");
    const employerVisible = await fixture.profiles.publicTestingResults(fixture.candidateId);
    assert.deepEqual(employerVisible, [{ subject: "react", grade: finished.decided_grade,
      camera_status: "denied", supervision_badge: "не определён" }]);
    assert.equal(JSON.stringify(employerVisible).includes(sid), false);
    assert.equal((await fixture.pool.query("SELECT count(*)::integer AS count FROM candidate_grade_results WHERE candidate_id=$1", [fixture.candidateId])).rows[0].count, 1);
    assert.equal((await (await get("/candidate/testing/preflight?specialization=react")).json()).eligible, false);
  } finally { await fixture.close(); }
});

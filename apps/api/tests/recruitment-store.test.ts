/** Real PostgreSQL lifecycle tests; the explicit database is a disposable isolated test database. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { Pool } from "pg";
import type { RecruitmentAction, RecruitmentView } from "@comit/shared";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { RecruitmentError, RecruitmentStore, type InvitationGrant } from "../src/recruitment/store.js";

const databaseUrl = process.env.RECRUITMENT_TEST_DATABASE_URL;
type ActionFields<T = RecruitmentAction> = T extends RecruitmentAction ? Omit<T, "expected_revision" | "idempotency_key"> : never;
const rejection = (status: number, code?: string) => (error: unknown) =>
  error instanceof RecruitmentError && error.status === status && (code === undefined || error.code === code);
const later = () => new Date(Date.now() + 86400000).toISOString();

test("transactional recruitment ownership, contacts, interviews, offers and durable events", { skip: !databaseUrl }, async t => {
  const pool = new Pool({ connectionString: databaseUrl });
  const store = new RecruitmentStore(pool);
  const profiles = new ProfileStore(pool);
  try {
    await migrate(pool);
    await pool.query("TRUNCATE accounts CASCADE");
    const fixture = async () => {
      const owner = randomUUID(), foreign = randomUUID(), candidate = randomUUID(), otherCandidate = randomUUID();
      for (const [id, role] of [[owner, "employer"], [foreign, "employer"], [candidate, "candidate"], [otherCandidate, "candidate"]]) {
        await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES($1,$2,$3,true)", [id, `${id}@example.test`, role]);
      }
      await profiles.save(candidate, { expected_revision: 0, fields: { display_name: "Synthetic candidate", phone: "+70000000000" },
        consent: { processing: true, pool: true, version: "profile-v1" } });
      await pool.query("UPDATE candidate_profile_outbox SET state='delivered',projected_revision=11,delivered_at=now() WHERE candidate_id=$1", [candidate]);
      const grant: InvitationGrant = { draft_id: randomUUID(), vacancy_id: randomUUID(), candidate_id: candidate, profile_revision: 11,
        terms: { company_name: "Synthetic company", description: "Backend position", salary: { from_rub: 100000, to_rub: 200000 },
          contact_method: "platform", vacancy_title: "Backend engineer", city: "Москва", daily: "Backend development", work_type: "remote" } };
      const request = { candidate_id: candidate, idempotency_key: randomUUID(), company_name: grant.terms.company_name, description: grant.terms.description };
      const invite = () => store.invite(owner, request, grant);
      const act = (role: "candidate" | "employer", view: RecruitmentView, action: ActionFields) => store.act(role,
        role === "candidate" ? candidate : owner, view.id, { ...action, expected_revision: view.revision, idempotency_key: randomUUID() });
      return { owner, foreign, candidate, otherCandidate, grant, request, invite, act };
    };

    await t.test("invitation replay, pair uniqueness, foreign ownership and consent-scoped contacts", async () => {
      const f = await fixture();
      let view = await f.invite();
      assert.equal(view.state, "sent");
      assert.deepEqual(view.terms, f.grant.terms);
      assert.deepEqual(await f.invite(), view);
      await assert.rejects(() => store.invite(f.owner, { ...f.request, description: "Changed payload" }, f.grant), rejection(409, "idempotency_conflict"));
      await assert.rejects(() => store.invite(f.owner, { ...f.request, idempotency_key: randomUUID() }, f.grant), rejection(409, "recruitment_already_exists"));
      await assert.rejects(() => store.contacts(f.owner, view.id), rejection(403, "invitation_acceptance_required"));
      await assert.rejects(() => store.get("employer", f.foreign, view.id), rejection(404));
      await assert.rejects(() => store.get("candidate", f.otherCandidate, view.id), rejection(404));
      await assert.rejects(() => store.contacts(f.foreign, view.id), rejection(404));
      await assert.rejects(() => f.act("employer", view, { action: "accept" }), rejection(409, "recruitment_action_unavailable"));
      view = await f.act("candidate", view, { action: "accept" });
      const contact = await store.contacts(f.owner, view.id);
      assert.equal(contact.email, `${f.candidate}@example.test`);
      assert.equal(contact.phone, "+70000000000");
      await profiles.save(f.candidate, { expected_revision: 1, fields: {}, consent: { processing: true, pool: false, version: "profile-v1" } });
      assert.equal((await store.contacts(f.owner, view.id)).profile_revision, 2);
      await profiles.save(f.candidate, { expected_revision: 2, fields: {}, consent: { processing: false, pool: false, version: "profile-v1" } });
      await assert.rejects(() => store.contacts(f.owner, view.id), rejection(409, "candidate_processing_consent_required"));
      assert.equal((await store.get("candidate", f.candidate, view.id)).allowed_actions.includes("withdraw"), true);
      const events = await store.events("candidate", f.candidate);
      assert.equal(events.items.length, 2);
      for (const event of events.items) {
        assert.deepEqual(Object.keys(event).sort(), ["created_at", "event_id", "process_id", "revision", "sequence", "state", "type"]);
        assert.ok(!Number.isNaN(Date.parse(event.created_at)));
      }
      assert.deepEqual((await store.events("employer", f.foreign)).items, []);
    });

    await t.test("CAS, exact replay and two simultaneous tabs mutate only once", async () => {
      const f = await fixture();
      const initial = await f.invite();
      const command: RecruitmentAction = { action: "view", expected_revision: initial.revision, idempotency_key: randomUUID() };
      const viewed = await store.act("candidate", f.candidate, initial.id, command);
      assert.deepEqual(await store.act("candidate", f.candidate, initial.id, command), viewed);
      await assert.rejects(() => store.act("candidate", f.candidate, initial.id, { ...command, action: "accept" }), rejection(409, "idempotency_conflict"));
      await assert.rejects(() => f.act("candidate", initial, { action: "accept" }), rejection(409, "recruitment_revision_conflict"));
      const results = await Promise.allSettled([f.act("candidate", viewed, { action: "accept" }), f.act("candidate", viewed, { action: "decline", reason: "Another offer" })]);
      assert.equal(results.filter(item => item.status === "fulfilled").length, 1);
      const failure = results.find(item => item.status === "rejected");
      assert.ok(failure && failure.status === "rejected" && rejection(409, "recruitment_revision_conflict")(failure.reason));
      assert.equal((await store.get("candidate", f.candidate, initial.id)).revision, viewed.revision + 1);
      assert.equal((await store.events("candidate", f.candidate)).items.length, 3);
    });

    await t.test("interview slots, cancellation, elapsed proposals and completion timing", async () => {
      const f = await fixture();
      let view = await f.act("candidate", await f.invite(), { action: "accept" });
      const propose = (kind: "technical" | "general"): ActionFields => ({ action: "interview_propose", kind,
        starts_at: later(), time_zone: "Europe/Moscow", duration_minutes: 60, format: "remote", location: "https://example.test/meeting" });
      view = await f.act("employer", view, propose("technical"));
      const first = view.interviews[0]!;
      await assert.rejects(() => f.act("candidate", view, { action: "interview_complete", interview_id: first.id, outcome: "passed", feedback: "Passed" }), rejection(409));
      await assert.rejects(() => f.act("employer", view, propose("technical")), rejection(409, "interview_type_limit"));
      view = await f.act("employer", view, { action: "interview_cancel", interview_id: first.id, reason: "Rescheduled" });
      view = await f.act("employer", view, propose("technical"));
      const technical = view.interviews.find(item => item.state === "proposed")!;
      view = await f.act("candidate", view, { action: "interview_accept", interview_id: technical.id });
      await assert.rejects(() => f.act("employer", view, { action: "interview_complete", interview_id: technical.id, outcome: "passed", feedback: "Passed" }), rejection(409, "interview_not_started"));
      await pool.query("UPDATE recruitment_interviews SET starts_at=now()-interval '1 minute' WHERE id=$1", [technical.id]);
      view = await f.act("employer", view, { action: "interview_complete", interview_id: technical.id, outcome: "passed", feedback: "Passed tasks" });
      assert.equal(view.interviews.find(item => item.id === technical.id)?.state, "completed");
      assert.ok(view.interviews.find(item => item.id === technical.id)?.completed_at);
      await assert.rejects(() => f.act("employer", view, propose("technical")), rejection(409, "interview_type_limit"));
      view = await f.act("employer", view, propose("general"));
      const elapsed = view.interviews.find(item => item.kind === "general")!;
      await pool.query("UPDATE recruitment_interviews SET starts_at=now()-interval '1 minute' WHERE id=$1", [elapsed.id]);
      await assert.rejects(() => f.act("candidate", view, { action: "interview_accept", interview_id: elapsed.id }), rejection(409, "interview_proposal_elapsed"));
      view = await f.act("employer", view, { action: "interview_cancel", interview_id: elapsed.id, reason: "Past proposal" });
      view = await f.act("employer", view, propose("general"));
      const general = view.interviews.find(item => item.kind === "general" && item.state === "proposed")!;
      view = await f.act("candidate", view, { action: "interview_accept", interview_id: general.id });
      await pool.query("UPDATE recruitment_interviews SET starts_at=now()-interval '1 minute' WHERE id=$1", [general.id]);
      view = await f.act("employer", view, { action: "interview_complete", interview_id: general.id, outcome: "neutral", feedback: "General discussion complete" });
      assert.equal(view.interviews.filter(item => item.state === "completed").length, 2);
      assert.equal(view.allowed_actions.includes("interview_propose"), false);
      await assert.rejects(() => f.act("employer", view, propose("general")), rejection(409, "recruitment_action_unavailable"));
    });

    await t.test("an active offer keeps proposed and scheduled interviews actionable", async () => {
      const f = await fixture();
      let view = await f.act("candidate", await f.invite(), { action: "accept" });
      view = await f.act("employer", view, { action: "interview_propose", kind: "technical", starts_at: later(),
        time_zone: "Europe/Moscow", duration_minutes: 30, format: "remote", location: "https://example.test/meeting" });
      const meeting = view.interviews[0]!;
      view = await f.act("employer", view, { action: "offer_send", salary_rub: 200000, description: "Early offer", expires_at: later() });
      assert.equal(view.state, "offer_sent");
      assert.ok(view.allowed_actions.includes("interview_cancel"));
      const candidateView = await store.get("candidate", f.candidate, view.id);
      assert.ok(candidateView.allowed_actions.includes("interview_accept"));
      view = await f.act("candidate", candidateView, { action: "interview_accept", interview_id: meeting.id });
      const employerView = await store.get("employer", f.owner, view.id);
      assert.ok(employerView.allowed_actions.includes("interview_complete"));
      view = await f.act("employer", employerView, { action: "interview_cancel", interview_id: meeting.id, reason: "Offer discussion instead" });
      assert.equal(view.state, "offer_sent");
      assert.equal(view.interviews[0]?.state, "cancelled");
      assert.equal(view.offers[0]?.state, "sent");
    });

    await t.test("offers need no meetings, hiring is terminal, decline closes and expiry permits a new offer", async () => {
      const offer = (): ActionFields => ({ action: "offer_send", salary_rub: 200000, description: "Backend position", expires_at: later() });
      const f = await fixture();
      let view = await f.act("candidate", await f.invite(), { action: "accept" });
      view = await f.act("employer", view, offer());
      assert.equal(view.interviews.length, 0);
      const firstOffer = view.offers[0]!;
      view = await f.act("candidate", view, { action: "offer_accept", offer_id: firstOffer.id });
      assert.equal(view.state, "hired");
      assert.deepEqual(view.allowed_actions, []);
      assert.equal(view.offers[0]?.state, "accepted");
      await assert.rejects(() => f.act("employer", view, offer()), rejection(409, "recruitment_action_unavailable"));

      const declined = await fixture();
      let closed = await declined.act("candidate", await declined.invite(), { action: "accept" });
      closed = await declined.act("employer", closed, offer());
      closed = await declined.act("candidate", closed, { action: "offer_decline", offer_id: closed.offers[0]!.id, reason: "Different company" });
      assert.equal(closed.state, "offer_declined");
      assert.equal(closed.closure?.actor, "candidate");
      await assert.rejects(() => declined.act("employer", closed, offer()), rejection(409));

      const expiring = await fixture();
      let expired = await expiring.act("candidate", await expiring.invite(), { action: "accept" });
      expired = await expiring.act("employer", expired, offer());
      const sentRevision = expired.revision;
      await pool.query("UPDATE recruitment_offers SET sent_at=now()-interval '1 day',expires_at=clock_timestamp() WHERE id=$1", [expired.offers[0]!.id]);
      expired = await store.get("candidate", expiring.candidate, expired.id);
      assert.equal(expired.state, "accepted");
      assert.equal(expired.revision, sentRevision + 1);
      assert.equal(expired.offers[0]?.state, "expired");
      assert.equal((await store.events("candidate", expiring.candidate)).items.at(-1)?.type, "offer.expired");
      await assert.rejects(() => expiring.act("candidate", expired, { action: "offer_accept", offer_id: expired.offers[0]!.id }), rejection(409));
      expired = await expiring.act("employer", expired, offer());
      assert.equal(expired.state, "offer_sent");
      assert.equal(expired.offers.length, 2);
    });

    await t.test("silent shortlist rejection is employer-only and cannot become an invitation", async () => {
      const f = await fixture();
      const request = { candidate_id: f.candidate, idempotency_key: randomUUID(), reason: "The candidate needs more production experience with distributed services and stronger technical skills in API design before this role would be suitable.", categories: ["technical_skills" as const] };
      const view = await store.rejectSaved(f.owner, f.grant.draft_id, f.grant.vacancy_id, 11, request);
      assert.equal(view.state, "rejected");
      assert.equal(view.terms, null);
      assert.equal(view.invited_at, null);
      assert.equal(view.closure?.reason, request.reason);
      assert.equal(view.closure?.actor, "employer");
      assert.deepEqual(await store.rejectSaved(f.owner, f.grant.draft_id, f.grant.vacancy_id, 11, request), view);
      await assert.rejects(() => store.get("candidate", f.candidate, view.id), rejection(404));
      assert.deepEqual((await store.list("candidate", f.candidate)).items, []);
      assert.deepEqual((await store.events("candidate", f.candidate)).items, []);
      assert.equal((await store.list("employer", f.owner, 50, 0, f.grant.draft_id)).items[0]?.id, view.id);
      assert.equal((await store.events("employer", f.owner)).items[0]?.type, "shortlist.rejected");
      await assert.rejects(() => f.invite(), rejection(409, "recruitment_already_exists"));
    });
  } finally { await pool.end(); }
});

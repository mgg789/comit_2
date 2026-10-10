/** Real PostgreSQL/session gateway tests; only private upstream HTTP services are isolated test doubles. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import swagger from "@fastify/swagger";
import { Pool } from "pg";
import type { AccountRole, AgentsDraftView, ContactsView, RecruitmentView, RecruitmentPage, RecruitmentEventPage } from "@comit/shared";
import { AuthStore } from "../src/auth/store.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { signSession } from "../src/auth/session.js";
import { migrate } from "../src/auth/migrate.js";
import { ProfileStore } from "../src/profiles/store.js";
import { RecruitmentStore } from "../src/recruitment/store.js";
import { EmployerProfileStore } from "../src/employers/store.js";
import { recruitmentRoutes } from "../src/routes/recruitment.js";

const databaseUrl = process.env.RECRUITMENT_ROUTE_TEST_DATABASE_URL;

test("recruitment HTTP contracts enforce auth, ownership, source freshness, privacy and replay", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  try {
    await migrate(pool);
    await pool.query("TRUNCATE accounts,auth_challenges,auth_issue_events,auth_verify_events RESTART IDENTITY CASCADE");
    const secret = "synthetic-only-recruitment-route-secret-32-bytes";
    const config = loadAuthConfig({ AUTH_SECRET: secret, AUTH_ALLOWED_ORIGINS: "http://localhost:39580", AUTH_COOKIE_SECURE: "false" });
    const store = new AuthStore(pool, secret), profiles = new ProfileStore(pool), recruitment = new RecruitmentStore(pool);
    const login = async (role: AccountRole, email: string, ip: string) => {
      const challenge = await store.issue(role, email, ip);
      const mail = await store.claimMail();
      assert.ok(mail);
      await store.finishMail(mail.job_id, mail.lease_token);
      const result = await store.verify(role, email, challenge.challenge_id, mail.code, "account-v1");
      const token = await signSession(config, role, result.account.id, result.session_id, result.expires_at);
      return { id: result.account.id, headers: { authorization: `Bearer ${token}` }, token };
    };
    const employer = await login("employer", "employer-recruitment@example.test", "192.0.2.1");
    const foreign = await login("employer", "foreign-recruitment@example.test", "192.0.2.2");
    const candidate = await login("candidate", "candidate-recruitment@example.test", "192.0.2.3");
    const pending = await login("candidate", "pending-recruitment@example.test", "192.0.2.4");
    const revoked = await login("candidate", "revoked-recruitment@example.test", "192.0.2.5");
    for (const person of [candidate, pending, revoked]) {
      await profiles.save(person.id, { expected_revision: 0, fields: { display_name: "Synthetic candidate", phone: "+70000000000" },
        consent: { processing: true, pool: true, version: "profile-v1" } });
    }
    await pool.query("UPDATE candidate_profile_outbox SET state='delivered',projected_revision=11,delivered_at=now() WHERE candidate_id=ANY($1::uuid[])", [[candidate.id, revoked.id]]);
    await profiles.save(revoked.id, { expected_revision: 1, fields: {}, consent: { processing: true, pool: false, version: "profile-v1" } });
    const employerProfiles = new EmployerProfileStore(pool);
    await employerProfiles.update(employer.id, { expected_revision: 0, company_name: "Synthetic company" });
    await employerProfiles.update(foreign.id, { expected_revision: 0, company_name: "Synthetic company" });
    const draftId = randomUUID(), rejectDraftId = randomUUID(), vacancyId = randomUUID(), rejectVacancyId = randomUUID();
    const exposures = new Map([candidate, pending, revoked].map(person => [person.id, randomUUID()]));
    const forwarded: Array<{ path: string; owner: string | null }> = [];
    let offline = false;
    const transport: typeof fetch = async (input, init) => {
      const url = new URL(String(input));
      const owner = new Headers(init?.headers).get("x-owner-id");
      forwarded.push({ path: url.pathname, owner });
      if (offline) throw new Error("synthetic offline upstream");
      if (owner !== employer.id) return Response.json({ detail: "not_found" }, { status: 404 });
      if (url.pathname.startsWith("/internal/vacancy-drafts/")) {
        const id = url.pathname.split("/").at(-1);
        if (id !== draftId && id !== rejectDraftId) return Response.json({ detail: "not_found" }, { status: 404 });
        const draft: AgentsDraftView = { draft_id: id, vacancy_id: id === draftId ? vacancyId : rejectVacancyId,
          owner_id: employer.id, revision: 1, state: "created", business_status: "open", publication: { status: "accepted", source_revision: 1 },
          fields: { title: "Backend engineer", zp_min: 100000, zp_max: 200000, city: "Москва", daily: "Backend development", type: "remote" } };
        return Response.json(draft);
      }
      if (url.pathname.includes("/saved/")) {
        const personId = url.pathname.split("/").at(-1)!;
        const exposure = exposures.get(personId);
        if (!exposure) return Response.json({ detail: "not_found" }, { status: 404 });
        return Response.json({ vacancy_id: url.pathname.includes(rejectVacancyId) ? rejectVacancyId : vacancyId,
          candidate_id: personId, profile_revision: 11, exposure_id: exposure, saved_at: new Date().toISOString(),
          card: { candidate_id: personId, profile_revision: 11, exposure_id: exposure, decision: "like" } });
      }
      if (url.pathname.includes("/feed/exposures/")) {
        const exposureId = url.pathname.split("/").at(-1);
        const entry = [...exposures].find(([, id]) => id === exposureId);
        if (entry) return Response.json({ candidate_id: entry[0], profile_revision: 11, exposure_id: exposureId, decision: "like" });
      }
      return Response.json({ detail: "not_found" }, { status: 404 });
    };
    await app.register(cookie);
    await app.register(swagger, { openapi: { info: { title: "Recruitment test", version: "1" }, components: { securitySchemes: {
      bearerAuth: { type: "http", scheme: "bearer" }, employerSession: { type: "apiKey", in: "cookie", name: "comit_employer_session" },
      candidateSession: { type: "apiKey", in: "cookie", name: "comit_candidate_session" },
    } } } });
    await app.register(recruitmentRoutes, { store, config, recruitment, profiles, employerProfiles, mailConfigured: false,
      agentsToken: "synthetic-agents", matchingToken: "synthetic-matching", transport });
    await app.ready();
    const inviteUrl = `/employer/vacancy-drafts/${draftId}/invitations`;
    const payload = { candidate_id: candidate.id, idempotency_key: randomUUID(), company_name: "Synthetic company", description: "Backend position" };
    assert.equal((await app.inject({ method: "POST", url: inviteUrl, payload: { unexpected: true } })).statusCode, 401);
    assert.equal((await app.inject({ method: "POST", url: inviteUrl, headers: candidate.headers, payload })).statusCode, 401);
    assert.equal(forwarded.length, 0);
    for (const invalid of [{ ...payload, owner_id: foreign.id }, { ...payload, candidate_id: "invalid" },
      { ...payload, salary: { from_rub: 1, to_rub: 2, currency: "USD" } }, { ...payload, description: " " },
    ]) assert.equal((await app.inject({ method: "POST", url: inviteUrl, headers: employer.headers, payload: invalid })).statusCode, 400);
    assert.equal(forwarded.length, 0, "invalid payloads must not reach private services");
    assert.equal((await app.inject({ method: "POST", url: inviteUrl, headers: foreign.headers, payload })).statusCode, 404);
    const created = await app.inject({ method: "POST", url: inviteUrl,
      headers: { ...employer.headers, "x-owner-id": foreign.id }, payload });
    assert.equal(created.statusCode, 200, created.body);
    let view = created.json<RecruitmentView>();
    assert.equal(view.owner_id, employer.id);
    assert.equal(forwarded.slice(1).every(call => call.owner === employer.id), true);
    assert.deepEqual(view.terms?.salary, { from_rub: 100000, to_rub: 200000 });
    assert.equal(created.headers["cache-control"], "no-store");
    const processUrl = `/candidate/recruitment/${view.id}`;
    const read = await app.inject({ url: processUrl, headers: candidate.headers });
    assert.equal(read.statusCode, 200);
    assert.equal("email" in read.json<Record<string, unknown>>(), false);
    assert.equal("phone" in read.json<Record<string, unknown>>(), false);
    assert.equal(read.body.includes("candidate-recruitment@example.test"), false);
    assert.equal((await app.inject({ url: processUrl, headers: pending.headers })).statusCode, 404);
    const contactsUrl = `/employer/recruitment/${view.id}/contacts`;
    assert.equal((await app.inject({ url: contactsUrl, headers: employer.headers })).statusCode, 403);
    assert.equal((await app.inject({ url: contactsUrl, headers: foreign.headers })).statusCode, 404);
    for (const action of ["view", "accept"]) {
      const result = await app.inject({ method: "POST", url: `${processUrl}/actions`, headers: candidate.headers,
        payload: { action, expected_revision: view.revision, idempotency_key: randomUUID() } });
      assert.equal(result.statusCode, 200, result.body);
      view = result.json<RecruitmentView>();
    }
    await profiles.save(candidate.id, { expected_revision: 1, fields: { phone: "+70000000001" } });
    const contacts = await app.inject({ url: contactsUrl, headers: employer.headers });
    assert.equal(contacts.statusCode, 200, contacts.body);
    assert.equal(contacts.json<ContactsView>().phone, "+70000000001");
    assert.equal(contacts.json<ContactsView>().profile_revision, 2);
    assert.equal(contacts.headers["cache-control"], "no-store");

    for (const person of [pending, revoked]) {
      const unavailable = await app.inject({ method: "POST", url: inviteUrl, headers: employer.headers,
        payload: { ...payload, candidate_id: person.id, idempotency_key: randomUUID() } });
      assert.equal(unavailable.statusCode, 409, unavailable.body);
      assert.deepEqual((await recruitment.list("candidate", person.id)).items, []);
    }
    const callCount = forwarded.length;
    offline = true;
    const replay = await app.inject({ method: "POST", url: inviteUrl, headers: employer.headers, payload });
    assert.equal(replay.statusCode, 200, replay.body);
    assert.deepEqual(replay.json<RecruitmentView>(), created.json<RecruitmentView>());
    assert.equal(forwarded.length, callCount, "committed retry bypasses offline upstream");
    offline = false;

    const rejected = await app.inject({ method: "POST", url: `/employer/vacancy-drafts/${rejectDraftId}/recruitment/reject`,
      headers: employer.headers, payload: { candidate_id: pending.id, idempotency_key: randomUUID(), reason: "The candidate needs more production experience with distributed services and stronger technical skills in API design before this role would be suitable.", categories: ["technical_skills"] } });
    assert.equal(rejected.statusCode, 200, rejected.body);
    const rejection = rejected.json<RecruitmentView>();
    assert.equal(rejection.invited_at, null);
    assert.equal(rejection.terms, null);
    assert.equal((await app.inject({ url: `/candidate/recruitment/${rejection.id}`, headers: pending.headers })).statusCode, 404);
    const rejectedList = await app.inject({ url: "/candidate/recruitment", headers: pending.headers });
    assert.deepEqual(rejectedList.json<RecruitmentPage>().items, []);
    const rejectedEvents = await app.inject({ url: "/candidate/recruitment-events", headers: pending.headers });
    assert.deepEqual(rejectedEvents.json<RecruitmentEventPage>().items, []);

    const paths = app.swagger().paths ?? {};
    for (const path of ["/employer/vacancy-drafts/{draft_id}/invitations", "/employer/vacancy-drafts/{draft_id}/recruitment/reject",
      "/candidate/recruitment/{id}/actions", "/employer/recruitment/{id}/contacts", "/candidate/recruitment-events",
    ]) assert.ok(paths[path], `missing public OpenAPI path ${path}`);
  } finally { await app.close(); await pool.end(); }
});

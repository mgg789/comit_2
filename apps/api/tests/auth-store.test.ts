import assert from "node:assert/strict";
import { test } from "node:test";
import { Pool } from "pg";
import { randomUUID } from "node:crypto";
import { decryptOtpPayload, encryptOtpPayload, hashOtp, requireEmail, safeEqual } from "../src/auth/crypto.js";
import { AuthStore } from "../src/auth/store.js";
import { migrate } from "../src/auth/migrate.js";

const secret = "test-auth-secret-with-at-least-32-bytes-123";

test("OTP payload encryption is purpose-bound and does not expose the code", () => {
  const encrypted = encryptOtpPayload(secret, { email: "person@example.com", code: "012345", challengeId: "challenge-1" });
  assert.equal(encrypted.ciphertext.includes(Buffer.from("012345", "utf8")), false);
  assert.deepEqual(decryptOtpPayload(secret, encrypted), {
    email: "person@example.com", code: "012345", challengeId: "challenge-1",
  });
  assert.equal(safeEqual(hashOtp(secret, "challenge-1", "012345"), hashOtp(secret, "challenge-1", "012345")), true);
  assert.equal(safeEqual(hashOtp(secret, "challenge-1", "012345"), hashOtp(secret, "challenge-1", "012346")), false);
});

test("email normalization accepts IDN domains and rejects unsafe local parts", () => {
  assert.equal(requireEmail(" User@BÜCHER.example "), "user@xn--bcher-kva.example");
  assert.throws(() => requireEmail("first..last@example.com"));
  assert.throws(() => requireEmail("user@example"));
});

const databaseUrl = process.env.AUTH_TEST_DATABASE_URL;
test("PostgreSQL auth flow is atomic, single-use and revocable", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  try {
    await migrate(pool);
    await pool.query("TRUNCATE auth_mail_outbox, auth_verify_events, auth_issue_events, sessions, auth_challenges, accounts RESTART IDENTITY CASCADE");
    const store = new AuthStore(pool, secret, { accountConsentVersion: "account-v1" });
    const issued = await store.issue("candidate", "Candidate@Example.com", "192.0.2.10");
    const job = await store.claimMail();
    assert.ok(job);
    assert.equal(job.email, "candidate@example.com");
    assert.match(job.code, /^\d{6}$/);
    await store.assertMailLease(job.job_id, job.lease_token);
    await store.finishMail(job.job_id, job.lease_token);
    await assert.rejects(
      () => store.verify("candidate", "candidate@example.com", issued.challenge_id, job.code),
      (error: unknown) => error instanceof Error && error.message === "consent_required",
    );
    const verified = await store.verify("candidate", "candidate@example.com", issued.challenge_id, job.code, "account-v1", "192.0.2.10");
    assert.equal(verified.account.email_verified, true);
    assert.equal(verified.account.role, "candidate");
    assert.equal(verified.account.consent.version, "account-v1");
    assert.ok(await store.session("candidate", verified.account.id, verified.session_id));
    await store.revoke("candidate", verified.account.id, verified.session_id);
    assert.equal(await store.session("candidate", verified.account.id, verified.session_id), null);
    await assert.rejects(
      () => store.verify("candidate", "candidate@example.com", issued.challenge_id, job.code, "account-v1"),
      (error: unknown) => error instanceof Error && error.message === "challenge_consumed",
    );

    // A valid code must consume even when the SMTP acknowledgement is still pending.
    const pending = await store.issue("candidate", "pending@example.test", "192.0.2.11");
    const pendingJob = await store.claimMail();
    assert.ok(pendingJob);
    const pendingAccount = await store.verify("candidate", pendingJob.email, pending.challenge_id, pendingJob.code, "account-v1");
    assert.ok(await store.session("candidate", pendingAccount.account.id, pendingAccount.session_id));
    assert.equal(await store.deliveryStatus("candidate", pendingJob.email, pending.challenge_id), "expired");

    const exhausted = await store.issue("candidate", "exhausted@example.test", "192.0.2.12");
    const exhaustedJob = await store.claimMail();
    assert.ok(exhaustedJob);
    const wrong = exhaustedJob.code === "000000" ? "111111" : "000000";
    for (let attempt = 0; attempt < 5; attempt++) {
      await assert.rejects(() => store.verify("candidate", exhaustedJob.email, exhausted.challenge_id, wrong, "account-v1"));
    }
    assert.equal((await pool.query("SELECT status, attempts FROM auth_challenges WHERE id=$1", [exhausted.challenge_id])).rows[0].status, "exhausted");
    assert.equal(await store.deliveryStatus("candidate", exhaustedJob.email, exhausted.challenge_id), "expired");

    await pool.query("UPDATE auth_issue_events SET created_at=now()-interval '2 minutes'");
    const login = await store.issue("candidate", "candidate@example.com", "192.0.2.13");
    const loginJob = await store.claimMail();
    assert.ok(loginJob);
    await assert.rejects(() => store.issue("employer", "candidate@example.com", "192.0.2.14"), /role_mismatch/);
    assert.equal((await pool.query("SELECT status FROM auth_challenges WHERE id=$1", [login.challenge_id])).rows[0].status, "active");
    const loggedIn = await store.verify("candidate", loginJob.email, login.challenge_id, loginJob.code);
    assert.equal(loggedIn.account.id, verified.account.id);

    const rejected = await store.issue("employer", "rejected@example.test", "192.0.2.15");
    const rejectedJob = await store.claimMail();
    assert.ok(rejectedJob);
    await store.failMail(rejectedJob.job_id, rejectedJob.lease_token, "rejected", false);
    assert.equal(await store.deliveryStatus("employer", rejectedJob.email, rejected.challenge_id), "failed");
    const poisoned = await store.issue("employer", "poisoned@example.test", "192.0.2.16");
    const poisonedJob = await store.claimMail();
    assert.ok(poisonedJob);
    await pool.query("UPDATE auth_mail_outbox SET attempts=5, leased_until=now()-interval '1 second' WHERE id=$1", [poisonedJob.job_id]);
    await store.purgeAuthExpired();
    const erased = (await pool.query("SELECT status, encrypted_payload, nonce FROM auth_mail_outbox WHERE challenge_id=$1", [poisoned.challenge_id])).rows[0];
    assert.equal(erased.status, "failed");
    assert.equal(erased.encrypted_payload, null);
    assert.equal(erased.nonce, null);

    const raceIssues = await Promise.allSettled([
      store.issue("employer", "race@example.test", "192.0.2.17"),
      store.issue("employer", "race@example.test", "192.0.2.17"),
    ]);
    assert.equal(raceIssues.filter(result => result.status === "fulfilled").length, 1);
    const raceJob = await store.claimMail();
    assert.ok(raceJob);
    const raceVerifies = await Promise.allSettled([
      store.verify("employer", raceJob.email, raceJob.challenge_id, raceJob.code, "account-v1"),
      store.verify("employer", raceJob.email, raceJob.challenge_id, raceJob.code, "account-v1"),
    ]);
    assert.equal(raceVerifies.filter(result => result.status === "fulfilled").length, 1);
    assert.equal((await pool.query("SELECT count(*) FROM accounts WHERE email=$1", [raceJob.email])).rows[0].count, "1");

    const expired = await store.issue("candidate", "expired@example.test", "192.0.2.18");
    const expiredJob = await store.claimMail();
    assert.ok(expiredJob);
    await pool.query("UPDATE auth_challenges SET expires_at=now()-interval '1 second' WHERE id=$1", [expired.challenge_id]);
    await assert.rejects(() => store.verify("candidate", expiredJob.email, expired.challenge_id, expiredJob.code, "account-v1"), /challenge_expired/);
    assert.equal(await store.deliveryStatus("candidate", expiredJob.email, expired.challenge_id), "expired");

    // Unknown challenge IDs still spend the IP verification budget.
    for (let attempt = 0; attempt < 60; attempt++) {
      await assert.rejects(() => store.verify("candidate", "unknown@example.test", randomUUID(), "000000", undefined, "192.0.2.99"));
    }
    await assert.rejects(() => store.verify("candidate", "unknown@example.test", randomUUID(), "000000", undefined, "192.0.2.99"), /verification_rate_limited/);
  } finally {
    await pool.end();
  }
});

const candidateDeleteDatabaseUrl = process.env.CANDIDATE_DELETE_TEST_DATABASE_URL;
test("candidate account deletion removes source graph, recruitment references and sessions", { skip: !candidateDeleteDatabaseUrl }, async () => {
  const pool = new Pool({ connectionString: candidateDeleteDatabaseUrl });
  try {
    await migrate(pool);
    const store = new AuthStore(pool, secret, { accountConsentVersion: "account-v1" });
    const candidateId = randomUUID(), emptyCandidateId = randomUUID(), employerId = randomUUID(), processId = randomUUID(), vacancyId = randomUUID(), draftId = randomUUID();
    await pool.query(`INSERT INTO accounts(id,email,role,email_verified,processing_consent_version,processing_consent_at)
      VALUES ($1,$2,'candidate',true,'account-v1',now()),($3,$4,'candidate',true,'account-v1',now()),($5,$6,'employer',true,'account-v1',now())`,
    [candidateId, `delete-${candidateId}@example.test`, emptyCandidateId, `empty-${emptyCandidateId}@example.test`, employerId, `owner-${employerId}@example.test`]);
    await pool.query(`INSERT INTO candidate_profiles(candidate_id,revision,processing_consent,pool_consent,consent_version,consent_accepted_at)
      VALUES ($1,1,true,true,'account-v1',now())`, [candidateId]);
    await pool.query(`INSERT INTO sessions(id,account_id,role,session_hash,expires_at)
      VALUES ($1,$2,'candidate',$3,now()+interval '1 day'),($4,$2,'candidate',$5,now()+interval '1 day')`,
    [randomUUID(), candidateId, Buffer.from(`session-${candidateId}-1`), randomUUID(), Buffer.from(`session-${candidateId}-2`)]);
    await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state)
      VALUES ($1,$2,$3,$4,$5,1,'rejected')`, [processId, employerId, candidateId, vacancyId, draftId]);
    await pool.query(`INSERT INTO recruitment_receipts(actor_id,idempotency_key,fingerprint,result)
      VALUES ($1,$2,repeat('a',64)::text,'{}')`, [candidateId, randomUUID()]);
    await pool.query(`INSERT INTO auth_challenges(id,email,role,ip_hmac,expires_at)
      VALUES ($1,$2,'candidate',$3,now()+interval '10 minutes')`, [randomUUID(), `delete-${candidateId}@example.test`, Buffer.from("ip")]);
    await pool.query(`INSERT INTO auth_issue_events(email,ip_hmac) VALUES ($1,$2)`, [`delete-${candidateId}@example.test`, Buffer.from("ip")]);

    assert.equal(await store.deleteCandidateAccount(candidateId), true);
    assert.equal((await pool.query("SELECT 1 FROM accounts WHERE id=$1", [candidateId])).rowCount, 0);
    assert.equal((await pool.query("SELECT 1 FROM sessions WHERE account_id=$1", [candidateId])).rowCount, 0);
    assert.equal((await pool.query("SELECT 1 FROM candidate_profiles WHERE candidate_id=$1", [candidateId])).rowCount, 0);
    assert.equal((await pool.query("SELECT 1 FROM recruitment_processes WHERE id=$1", [processId])).rowCount, 0);
    assert.equal((await pool.query("SELECT 1 FROM recruitment_receipts WHERE actor_id=$1", [candidateId])).rowCount, 0);
    assert.equal((await pool.query("SELECT 1 FROM accounts WHERE id=$1", [employerId])).rowCount, 1);
    assert.equal(await store.deleteCandidateAccount(candidateId), false);
    assert.equal(await store.deleteCandidateAccount(emptyCandidateId), true);
    assert.equal((await pool.query("SELECT 1 FROM accounts WHERE id=$1", [emptyCandidateId])).rowCount, 0);
  } finally {
    await pool.end();
  }
});

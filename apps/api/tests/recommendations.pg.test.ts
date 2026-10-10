/** Isolated PostgreSQL acceptance for migration, queue recovery, privacy and revocation. */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import { Pool } from "pg";
import { migrate } from "../src/auth/migrate.js";
import { CandidateProfileStore } from "../src/profiles/store.js";
import { CandidateRecommendationsStore } from "../src/recommendations/store.js";

const databaseUrl = process.env.RECOMMENDATIONS_TEST_DATABASE_URL;
test("weekly queue migration, unique claim, lease fencing, thresholds, atomic notification and withdrawal", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  try {
    await migrate(pool);
    assert.deepEqual(await migrate(pool), []);
    const candidateId = randomUUID();
    const account = async (id: string, role: "candidate" | "employer") => {
      await pool.query("INSERT INTO accounts(id,email,role,email_verified) VALUES($1,$2,$3,true)", [id, `${id}@example.test`, role]);
    };
    await account(candidateId, "candidate");
    await pool.query(`INSERT INTO candidate_profiles(candidate_id,revision,processing_consent,pool_consent,training_consent,training_consent_version,training_consent_accepted_at,consent_version,consent_accepted_at)
      VALUES($1,1,true,true,true,'training-v2',now(),'profile-v1',now())`, [candidateId]);
    await pool.query("CREATE TABLE test_recommendation_notifications(key text PRIMARY KEY,candidate_id uuid NOT NULL)");
    const store = new CandidateRecommendationsStore(pool, { notifyInTransaction: async (client, candidate, item) => {
      await client.query("INSERT INTO test_recommendation_notifications(key,candidate_id) VALUES($1,$2) ON CONFLICT DO NOTHING", [`recommendation:${item.recommendation_id}`, candidate]);
    } });
    const week = new Date("2026-10-05T12:00:00Z");
    await pool.query("UPDATE candidate_profiles SET training_consent_version='training-v1' WHERE candidate_id=$1", [candidateId]);
    assert.equal(await store.enqueueCurrentWeek(week), 0, "legacy consent cannot enable recommendations");
    assert.equal((await store.latest(candidateId)).item, null);
    const profiles = new CandidateProfileStore(pool);
    const optedIn = await profiles.save(candidateId, { expected_revision: 1, fields: {}, consent: { processing: true, pool: true, version: "profile-v1", training: true, training_version: "training-v2" } });
    assert.equal(optedIn.consents.training_version, "training-v2");
    const edited = await profiles.save(candidateId, { expected_revision: 2, fields: { city: "Москва" } });
    assert.equal(edited.consents.training_version, "training-v2", "field edits preserve the chosen purpose version");
    assert.equal(await store.enqueueCurrentWeek(week), 1);
    assert.equal(await store.enqueueCurrentWeek(week), 0);
    const claims = await Promise.all([store.claim(), store.claim()]);
    const job = claims.find(value => value !== null); assert.ok(job); assert.equal(claims.filter(Boolean).length, 1);
    assert.equal(await store.valid(job), true);
    const owners = [randomUUID(), randomUUID(), randomUUID()];
    for (const ownerId of owners) {
      await account(ownerId, "employer");
      await pool.query(`INSERT INTO recruitment_processes(id,owner_id,candidate_id,vacancy_id,draft_id,profile_revision,state,closure)
        VALUES($1,$2,$3,$4,$5,1,'rejected',jsonb_build_object('reason','Недостаточно опыта','actor','employer','at','2026-09-29T12:00:00Z','categories',jsonb_build_array('experience')))`,
      [randomUUID(), ownerId, candidateId, randomUUID(), randomUUID()]);
    }
    assert.deepEqual(await store.feedback(job), [{ source: "employer_feedback", category: "experience", count: 3 }]);
    await pool.query("UPDATE candidate_recommendations SET lease_until=now()-interval '1 second' WHERE id=$1", [job.id]);
    const recovered = await store.claim(); assert.ok(recovered); assert.equal(recovered.attempts, 2);
    assert.equal(await store.publish(job, "insufficient_data", "Недостаточно независимых наблюдений.", []), false);
    const evidence = await store.feedback(recovered);
    assert.equal(await store.publish(recovered, "ready", "По агрегатам отзывов стоит уточнить вклад в проектах.", evidence), true);
    assert.equal(await store.publish(recovered, "ready", "Повторная публикация недопустима.", evidence), false);
    assert.equal((await pool.query("SELECT count(*)::int AS count FROM test_recommendation_notifications")).rows[0].count, 1);
    assert.equal((await store.latest(candidateId)).item?.state, "ready");
    assert.equal((await store.latest(candidateId)).item?.week_start, "2026-10-05");
    assert.equal((await store.latest(randomUUID())).item, null);
    // Immediate source withdrawal purges results without waiting for projection delivery.
    await pool.query("UPDATE candidate_profiles SET revision=revision+1,training_consent=false,training_consent_version=NULL,training_consent_accepted_at=NULL WHERE candidate_id=$1", [candidateId]);
    assert.equal((await store.latest(candidateId)).item, null);
    assert.equal((await pool.query("SELECT count(*)::int AS count FROM candidate_recommendations")).rows[0].count, 0);
    await pool.query("UPDATE candidate_profiles SET revision=revision+1,training_consent=true,training_consent_version='training-v2',training_consent_accepted_at=now() WHERE candidate_id=$1", [candidateId]);
    assert.equal(await store.enqueueCurrentWeek(week), 1);
    const afterRestore = await store.claim(); assert.ok(afterRestore);
    await pool.query("UPDATE candidate_recommendations SET attempts=3,lease_until=now()-interval '1 second' WHERE id=$1", [afterRestore.id]);
    const finalRecovery = await store.claim(); assert.ok(finalRecovery); assert.equal(finalRecovery.terminal_recovery, true); assert.equal(finalRecovery.attempts, 3);
    assert.equal(await store.publish(finalRecovery, "provider_unavailable", "Провайдер временно недоступен. Попытки этой недели исчерпаны.", []), true);
    assert.equal(await store.claim(), null);
    // Notification failure rolls the result back and preserves the owned lease for retry.
    const nextWeek = new Date("2026-10-12T12:00:00Z"); await store.enqueueCurrentWeek(nextWeek);
    const nextJob = await store.claim(); assert.ok(nextJob);
    const failing = new CandidateRecommendationsStore(pool, { notifyInTransaction: async () => { throw new Error("synthetic_notification_failure"); } });
    await assert.rejects(failing.publish(nextJob, "insufficient_data", "Недостаточно независимых наблюдений.", []));
    assert.equal(await store.valid(nextJob), true);
    assert.equal((await pool.query("SELECT state FROM candidate_recommendations WHERE id=$1", [nextJob.id])).rows[0].state, "processing");
    // A concurrent profile edit prevents publication even if consent stays enabled.
    await pool.query("UPDATE candidate_profiles SET revision=revision+1 WHERE candidate_id=$1", [candidateId]);
    assert.equal(await store.publish(nextJob, "ready", "Ответ для устаревшего снимка не должен сохраниться.", []), false);
    await store.discard(nextJob);
    // Exercise the production matching publisher and its authenticated aggregate route on the same isolated DB.
    if (process.env.MATCHING_TEST_PYTHON) {
    const matching = spawnSync(process.env.MATCHING_TEST_PYTHON, ["-m", "pytest", "-q", "-p", "no:cacheprovider", "services/matching-engine/tests/test_recommendations_integration.py"], {
      cwd: new URL("../../../", import.meta.url), encoding: "utf8", env: { ...process.env, PIPELINE_TEST_DATABASE_URL: databaseUrl,
        PYTHONPYCACHEPREFIX: "/private/tmp/comit-recommendation-pycache", PYTHONPATH: "services/matching-engine/src:services/matching-engine/tests" },
    });
    // Only fixed test output is emitted; subprocess itself never prints DSNs.
    assert.equal(matching.status, 0, matching.stdout + matching.stderr);
    }
  } finally { await pool.end(); }
});

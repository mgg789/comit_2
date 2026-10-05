/** Provider and lease-fencing checks for the profile projection worker. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { ProfileWorker, type ProfileWorkerStore } from "../src/profiles/worker.js";
import type { ProfileProjectionJob } from "../src/profiles/store.js";

const candidateId = "00000000-0000-4000-8000-000000000001";
const job: ProfileProjectionJob = {
  job_id: "00000000-0000-4000-8000-000000000002", candidate_id: candidateId, source_revision: 3,
  payload: { source_revision: 3, city: "Москва" }, attempts: 1, lease_token: "00000000-0000-4000-8000-000000000003",
  lease_until: new Date(Date.now() + 60_000).toISOString(),
};

function fakeStore(overrides: Partial<ProfileWorkerStore> = {}): ProfileWorkerStore {
  return {
    claimProjection: async () => job,
    assertProjectionLease: async () => undefined,
    acknowledgeProjection: async () => true,
    failProjection: async () => true,
    ...overrides,
  };
}

test("worker sends the exact persisted snapshot and validates the fenced ACK", async () => {
  let requestBody = "";
  let acknowledged: unknown[] = [];
  const store = fakeStore({
    acknowledgeProjection: async (...values: unknown[]) => { acknowledged = values; return true; },
  });
  const worker = new ProfileWorker(store, {
    token: "profile-token", engineUrl: "http://matching.test",
    transport: async (url, options) => {
      assert.equal(String(url), `http://matching.test/internal/candidates/${candidateId}/profile`);
      requestBody = String(options?.body);
      return Response.json({ candidate_id: candidateId, profile_revision: 7 });
    },
  });
  assert.equal(await worker.processOnce(), true);
  assert.deepEqual(JSON.parse(requestBody), job.payload);
  assert.deepEqual(acknowledged.slice(2), [candidateId, 3, 7]);
});

test("worker quarantines client rejection and retries temporary provider failures without leaking bodies", async () => {
  for (const [status, retryable] of [[422, false], [503, true]] as const) {
    let failure: unknown[] = [];
    const worker = new ProfileWorker(fakeStore({
      failProjection: async (...values: unknown[]) => { failure = values; return true; },
    }), {
      token: "profile-token", engineUrl: "http://matching.test",
      transport: async () => Response.json({ private: "must-not-be-persisted" }, { status }),
    });
    assert.equal(await worker.processOnce(), true);
    assert.deepEqual(failure.slice(2), [retryable ? "provider_unavailable" : "provider_rejected", retryable, 5]);
  }
});

test("a superseded lease never sends its old source snapshot", async () => {
  let sends = 0;
  const worker = new ProfileWorker(fakeStore({
    assertProjectionLease: async () => { throw new Error("projection lease is no longer valid"); },
  }), {
    token: "profile-token", engineUrl: "http://matching.test",
    transport: async () => { sends++; return Response.json({ candidate_id: candidateId, profile_revision: 7 }); },
  });
  assert.equal(await worker.processOnce(), true);
  assert.equal(sends, 0);
});

test("worker turns malformed ACK into a terminal safe error", async () => {
  let failure: unknown[] = [];
  const worker = new ProfileWorker(fakeStore({
    failProjection: async (...values: unknown[]) => { failure = values; return true; },
  }), {
    token: "profile-token", engineUrl: "http://matching.test",
    transport: async () => Response.json({ candidate_id: candidateId }),
  });
  assert.equal(await worker.processOnce(), true);
  assert.deepEqual(failure.slice(2), ["provider_invalid_ack", false, 5]);
});

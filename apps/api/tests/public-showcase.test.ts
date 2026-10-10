/** Anonymous catalogue regression: private IDs, contacts and PDF access never escape. */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import Fastify from "fastify";
import { Pool } from "pg";
import { AuthStore } from "../src/auth/store.js";
import { ProfileStore } from "../src/profiles/store.js";
import { loadAuthConfig } from "../src/auth/config.js";
import { accountRoutes } from "../src/routes/accounts.js";
import { authDomain } from "../src/auth/domain.js";

const config = loadAuthConfig({ AUTH_SECRET: "synthetic-public-showcase-only-32bytes", AUTH_ALLOWED_ORIGINS: "http://localhost:5177", AUTH_COOKIE_SECURE: "false" });

test("anonymous catalogue uses consent watermark, opaque IDs/cursors and exact upstream allowlists", async () => {
  const pool = new Pool({ connectionString: "postgres://unused:unused@127.0.0.1:1/unused" });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  const profiles = new ProfileStore(pool); let consent = true, malformed = false, down = false;
  profiles.isSelectionCurrent = async () => consent;
  const id = randomUUID(), sourceCursor = Buffer.from(JSON.stringify({ key: id })).toString("base64url");
  let lastOwner: string | null = "unset", lastCursor: string | null = null, lastQuery = "";
  const transport: typeof fetch = async (input, init) => {
    const url = new URL(String(input)); lastOwner = new Headers(init?.headers).get("x-owner-id");
    lastCursor = url.searchParams.get("cursor"); lastQuery = url.search;
    assert.equal(new Headers(init?.headers).get("x-internal-token"), "synthetic-matching");
    if (down) return Response.json({}, { status: 503 });
    return Response.json({ items: [{ candidate_id: id, profile_revision: 2, display_name: `Candidate ${id}`, city: "Москва",
      salary: { from_rub: 100000, to_rub: 150000 }, category: { specialization: "backend", grade: "middle", grade_status: "self_reported" },
      stacks: ["python"], published_at: "2026-10-10T00:00:00Z", ...(malformed ? { contacts: "private" } : {}) }],
      limit: 1, has_more: true, next_cursor: sourceCursor });
  };
  try {
    await app.register(accountRoutes, { config, store: new AuthStore(pool, config.secret), profiles, mailConfigured: false,
      matchingToken: "synthetic-matching", agentsToken: "synthetic-agents", transport });
    const first = await app.inject({ url: "/public/showcase/candidates?limit=1&specialization=backend&grade=middle&stack=python&sort=oldest&q=Python" });
    assert.equal(first.statusCode, 200, first.body); assert.equal(lastOwner, null);
    assert.ok(lastQuery.includes("stack=python") && lastQuery.includes("sort=oldest"));
    const page = first.json(); assert.equal(page.items[0].display_name, "Специалист");
    assert.match(page.items[0].candidate_id, /^[a-f0-9]{64}$/); assert.ok(!first.body.includes(id));
    assert.ok(!Buffer.from(page.next_cursor, "base64url").toString("utf8").includes(id));
    assert.ok(!first.body.includes("profile_revision") && !first.body.includes("resume_url"));
    const second = await app.inject({ url: `/public/showcase/candidates?limit=1&cursor=${page.next_cursor}` });
    assert.equal(second.statusCode, 200, second.body); assert.equal(lastCursor, sourceCursor);
    assert.equal(second.json().items[0].candidate_id, page.items[0].candidate_id);
    assert.equal((await app.inject({ url: `/public/showcase/candidates?cursor=${sourceCursor}` })).statusCode, 409);
    assert.equal((await app.inject({ url: `/public/showcase/vacancies?cursor=${page.next_cursor}` })).statusCode, 409);
    for (const query of ["limit=0", "limit=51", "limit=true", "grade=expert", "stack=private.email", "owner_id=anything", "q=%00"]) {
      assert.equal((await app.inject({ url: `/public/showcase/candidates?${query}` })).statusCode, 400, query);
    }
    consent = false; assert.deepEqual((await app.inject({ url: "/public/showcase/candidates" })).json().items, []);
    consent = true; malformed = true; assert.equal((await app.inject({ url: "/public/showcase/candidates" })).statusCode, 503);
    malformed = false; down = true; assert.equal((await app.inject({ url: "/public/showcase/candidates" })).statusCode, 503);
    assert.equal((await app.inject({ url: `/public/showcase/candidates/${id}` })).statusCode, 404);
    assert.equal((await app.inject({ url: `/public/showcase/candidates/${id}/resume.pdf` })).statusCode, 404);
  } finally { await app.close(); await pool.end(); }
});

test("public vacancies require exact accepted current editorial revision and omit private data", async () => {
  const pool = new Pool({ connectionString: "postgres://unused:unused@127.0.0.1:1/unused" });
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  const id = randomUUID(), owner = randomUUID(); let closed = false, archived = false, sourceRevision = 4, accepted = true;
  const transport: typeof fetch = async input => {
    const url = new URL(String(input));
    if (url.pathname.endsWith("/vacancies")) return Response.json({ items: [{ vacancy_id: id, owner_id: owner, source_revision: 4, revision: 1,
      status: "open", zp_min: 100000, zp_max: 150000, daily: "5/2", city: "Москва", type: "remote", bio: "private bio",
      domens: {}, stacks: { python: 3 }, soft_skills: {}, class: "backend", metadata: { private: "private metadata" },
      build_mode: "mixed", strict_vector: [], requirement_mask: [], strict_schema_version: 1, strict_schema_hash: "synthetic",
      semantic_vector: null, semantic_model_version: null, semantic_space_id: null, semantic_available: false, published_at: "2026-10-10T00:00:00Z" }],
      limit: 24, offset: 0, has_more: false, next_cursor: null });
    return Response.json({ draft_id: id, vacancy_id: id, owner_id: owner, revision: sourceRevision, state: "created", business_status: closed ? "closed" : "open", archived,
      fields: { title: "Backend engineer", metadata: { company_name: "Synthetic organization", grade: "middle", private: "private metadata" } },
      publication: { status: accepted ? "accepted" : "pending", source_revision: sourceRevision } });
  };
  try {
    await app.register(accountRoutes, { config, store: new AuthStore(pool, config.secret), profiles: new ProfileStore(pool), mailConfigured: false,
      matchingToken: "synthetic-matching", agentsToken: "synthetic-agents", transport });
    const first = await app.inject({ url: "/public/showcase/vacancies" }); assert.equal(first.statusCode, 200, first.body);
    assert.equal(first.json().items[0].title, "Backend engineer"); assert.ok(!first.body.includes(owner) && !first.body.includes("private"));
    assert.ok(!first.body.includes("vector") && !first.body.includes("bio"));
    closed = true; assert.deepEqual((await app.inject({ url: "/public/showcase/vacancies" })).json().items, []);
    closed = false; archived = true; assert.deepEqual((await app.inject({ url: "/public/showcase/vacancies" })).json().items, []);
    archived = false; accepted = false; assert.deepEqual((await app.inject({ url: "/public/showcase/vacancies" })).json().items, []);
    accepted = true; sourceRevision = 5; assert.deepEqual((await app.inject({ url: "/public/showcase/vacancies" })).json().items, []);
  } finally { await app.close(); await pool.end(); }
});


test("catalogue fails explicitly when source/auth configuration is unavailable", async () => {
  const app = Fastify();
  const previous = Object.fromEntries(["AUTH_SECRET", "JWT_SECRET", "DATABASE_URL"].map(key => [key, process.env[key]]));
  try {
    for (const key of Object.keys(previous)) delete process.env[key];
    await authDomain(app);
    for (const kind of ["vacancies", "candidates"]) {
      const result = await app.inject({ url: `/public/showcase/${kind}` });
      assert.equal(result.statusCode, 503); assert.deepEqual(result.json(), { detail: "showcase_unavailable" });
      assert.equal(result.headers["cache-control"], "no-store");
    }
  } finally {
    for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
    await app.close();
  }
});

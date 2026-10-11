import assert from "node:assert/strict";
import { test } from "node:test";
import Fastify from "fastify";
import { Pool } from "pg";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { salaryMarketRoutes } from "../src/routes/salary-market.js";
import { SalaryMarketStore } from "../src/salary-market/store.js";

test("public platform salary endpoint validates dimensions and returns aggregate only", async () => {
  const app = Fastify({ ajv: { customOptions: { removeAdditional: false, coerceTypes: false, useDefaults: false } } });
  let requested: { specialization: string; grade: string; stacks: string[] } | null = null;
  await app.register(salaryMarketRoutes, { market: { get: async query => {
    requested = query;
    return { ...query, sample_count: 3, minimum_sample_count: 3, average_salary_rub: 205000 };
  } } });
  try {
    const result = await app.inject({ url: "/public/salary-market?specialization=backend&grade=middle&stacks=python,postgresql" });
    assert.equal(result.statusCode, 200, result.body);
    assert.deepEqual(requested, { specialization: "backend", grade: "middle", stacks: ["postgresql", "python"] });
    assert.deepEqual(result.json(), { specialization: "backend", grade: "middle", stacks: ["postgresql", "python"],
      sample_count: 3, minimum_sample_count: 3, average_salary_rub: 205000 });
    assert.equal(result.headers["cache-control"], "no-store");
    for (const query of ["grade=expert", "specialization=unknown", "stacks=python,,go", "stacks=private.email"]) {
      assert.equal((await app.inject({ url: `/public/salary-market?specialization=backend&grade=middle&${query}` })).statusCode, 400, query);
    }
  } finally { await app.close(); }
});

test("salary endpoint returns unavailable on source failure instead of a fabricated zero", async () => {
  const app = Fastify();
  await app.register(salaryMarketRoutes, { market: { get: async () => { throw new Error("database unavailable"); } } });
  try {
    const result = await app.inject({ url: "/public/salary-market?specialization=backend&grade=middle" });
    assert.equal(result.statusCode, 503);
    assert.deepEqual(result.json(), { detail: "salary_market_unavailable" });
  } finally { await app.close(); }
});

test("store suppresses sparse samples and constrains the query to active published matching vacancies", async () => {
  let result: { sample_count: number; average_salary_rub: string | null } = { sample_count: 2, average_salary_rub: "199000" };
  let statement = "";
  let parameters: unknown[] = [];
  const pool = { query: async (sql: string, values: unknown[]) => {
    statement = sql; parameters = values;
    return { rows: [result] };
  } } as unknown as Pool;
  const store = new SalaryMarketStore(pool);
  const query = { specialization: "backend" as const, grade: "middle" as const, stacks: ["python", "postgresql"] };
  const sparse = await store.get(query);
  assert.equal(sparse.sample_count, 2);
  assert.equal(sparse.average_salary_rub, null);
  assert.equal(sparse.minimum_sample_count, 3);
  assert.deepEqual(parameters, ["backend", "middle", ["postgresql", "python"]]);
  assert.match(statement, /v\.state='ready' AND v\.business_status='open'/);
  assert.match(statement, /s\.active_schema_version=r\.schema_version/);
  assert.match(statement, /v\.profile->'metadata'->>'grade'=\$2/);
  assert.match(statement, /NOT EXISTS \(SELECT 1 FROM unnest\(\$3::text\[\]\)/);

  result = { sample_count: 3, average_salary_rub: "199001" };
  assert.equal((await store.get(query)).average_salary_rub, 199001);
});


const databaseUrl = process.env.SALARY_MARKET_TEST_DATABASE_URL;
test("PostgreSQL salary market excludes drafts, other grades, stale revisions and missing stack", { skip: !databaseUrl }, async () => {
  const pool = new Pool({ connectionString: databaseUrl });
  try {
    for (const name of ["001_candidate_pipeline.sql", "002_vacancy_pipeline.sql"]) {
      await pool.query(await readFile(new URL(`../../../services/matching-engine/src/matching_engine/migrations/${name}`, import.meta.url), "utf8"));
    }
    await pool.query("INSERT INTO candidate_pipeline.strict_schemas(version,schema_hash,coordinates) VALUES(1,'salary-test','[]'),(2,'old-salary-test','[]')");
    await pool.query("INSERT INTO candidate_pipeline.settings(singleton,active_schema_version) VALUES(true,1)");
    async function vacancy(from: number, to: number, options: { grade?: string; status?: string; schema?: number; revision?: number; stacks?: Record<string, number> } = {}) {
      const id = randomUUID();
      const profile = { class: "backend", metadata: { grade: options.grade ?? "middle" }, zp_min: from, zp_max: to, stacks: options.stacks ?? { python: 4, postgresql: 3 } };
      await pool.query(`INSERT INTO vacancy_pipeline.vacancies(vacancy_id,revision,source_revision,profile_hash,profile,raw_input,business_status,state,missing_requirements)
        VALUES($1,1,1,'salary-fixture',$2,'{}',$3,'ready','[]')`, [id, JSON.stringify(profile), options.status ?? "open"]);
      await pool.query(`INSERT INTO vacancy_pipeline.ready(vacancy_id,revision,schema_version,schema_hash,build_mode,strict_vector,requirement_mask)
        VALUES($1,$2,$3,'salary-test','mixed','{}','{}')`, [id, options.revision ?? 1, options.schema ?? 1]);
    }
    await vacancy(100000, 200000);
    await vacancy(200000, 300000);
    const store = new SalaryMarketStore(pool);
    const query = { specialization: "backend" as const, grade: "middle" as const, stacks: ["python", "postgresql"] };
    assert.deepEqual((await store.get(query)).average_salary_rub, null);
    await vacancy(150000, 250000);
    await vacancy(900000, 1000000, { grade: "senior" });
    await vacancy(900000, 1000000, { status: "draft" });
    await vacancy(900000, 1000000, { schema: 2 });
    await vacancy(900000, 1000000, { revision: 2 });
    await vacancy(900000, 1000000, { stacks: { python: 4 } });
    const result = await store.get(query);
    assert.equal(result.sample_count, 3);
    assert.equal(result.average_salary_rub, 200000);
    assert.equal((await store.get({ ...query, specialization: "frontend" })).sample_count, 0);
  } finally { await pool.end(); }
});

/** Isolated real HTTP/PG fixture; synthetic credentials and no production services. */
import { randomUUID } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createServer } from "node:net";
import Fastify from "fastify";
import cookie from "@fastify/cookie";
import { Pool } from "pg";
import { loadAuthConfig } from "../../src/auth/config.js";
import { AuthStore } from "../../src/auth/store.js";
import { fixtureSkillCatalog } from "./skill-catalog.js";
import { ProfileStore } from "../../src/profiles/store.js";
import { authRoutes } from "../../src/routes/auth.js";
import { candidateProfileRoutes } from "../../src/routes/candidate-profile.js";
import { candidateWeeklyChallengeRoutes } from "../../src/routes/candidate-weekly-challenge.js";
import { candidateTestingRoutes } from "../../src/routes/candidate-testing.js";
import { testingIntegrationRoutes } from "../../src/routes/testing-integration.js";

async function freePort(): Promise<number> {
  const server = createServer();
  await new Promise<void>(done => server.listen(0, "127.0.0.1", done));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("fixture_port_unavailable");
  await new Promise<void>((done, reject) => server.close(error => error ? reject(error) : done()));
  return address.port;
}

/** Start synthetic candidate profile, real auth, API facade and private Python service. */
export async function startTestingBrowserFixture(databaseUrl: string, ports?: { api: number; engine: number }) {
  const schema = `testing_browser_${randomUUID().replaceAll("-", "")}`;
  const admin = new Pool({ connectionString: databaseUrl });
  const pool = new Pool({ connectionString: databaseUrl, options: `-c search_path=${schema}` });
  const app = Fastify();
  let engine: ChildProcess | undefined;
  let mediaDir: string | null = null;
  try {
  await admin.query(`CREATE SCHEMA ${schema}`);
  for (const name of ["001_accounts", "002_candidate_profiles", "005_training_consent", "007_candidate_verification", "019_testing_grades", "021_weekly_challenges"]) {
    await pool.query(await readFile(new URL(`../../migrations/${name}.sql`, import.meta.url), "utf8"));
  }
  const candidateId = randomUUID();
  await pool.query(`INSERT INTO accounts(id,email,role,email_verified,processing_consent_version,processing_consent_at)
    VALUES($1,'selftest@example.invalid','candidate',true,'account-v1',now())`, [candidateId]);
  const profiles = new ProfileStore(pool, fixtureSkillCatalog);
  await profiles.save(candidateId, { expected_revision: 0, fields: { display_name: "Демо Кандидат", primary_direction: "frontend", declared_grade: "middle", stacks: [{ key: "react", level: 4 }] }, consent: {processing:true,pool:false,version:"profile-v1"} });
  const root = resolve(new URL("../../../../", import.meta.url).pathname);
  mediaDir = await mkdtemp(join(tmpdir(), "comit-testing-fixture-"));
  const enginePort = ports?.engine ?? await freePort();
  const engineUrl = `http://127.0.0.1:${enginePort}`;
  const serviceToken = "synthetic-browser-testing-engine-token";
  process.env.NODE_ENV = "development";
  process.env.TESTING_ENGINE_URL = engineUrl;
  process.env.TESTING_ENGINE_TOKEN = serviceToken;
  const config = loadAuthConfig({ AUTH_SECRET: "synthetic-browser-fixture-auth-secret-32chars", AUTH_ALLOWED_ORIGINS:
    "http://127.0.0.1:5185,http://localhost:5185,http://127.0.0.1:5201,http://localhost:5201,http://localhost",
    NODE_ENV: "development", DEV_AUTH_EXPOSE_CODE: "true", AUTH_COOKIE_SECURE: "false" });
  await app.register(cookie);
  const store = new AuthStore(pool, config.secret);
  const options = { config, store, profiles, mailConfigured: false };
  await app.register(authRoutes, options);
  await app.register(candidateProfileRoutes, options);
  await app.register(candidateWeeklyChallengeRoutes, options);
  await app.register(candidateTestingRoutes, { ...options, pool });
  await app.register(testingIntegrationRoutes, { profiles, token: serviceToken });
  await app.listen({ port: ports?.api ?? 0, host: "127.0.0.1" });
  const address = app.server.address();
  if (!address || typeof address === "string") throw new Error("fixture_api_unavailable");
  const apiUrl = `http://127.0.0.1:${address.port}`;
  engine = spawn(process.env.TESTING_ENGINE_TEST_PYTHON ?? "python3", ["-m", "testing_engine"], {
    cwd: join(root, "services/testing-engine"), stdio: "ignore", env: {
      PATH: process.env.PATH, PYTHONPATH: join(root, "services/testing-engine/src"), PYTHONDONTWRITEBYTECODE: "1",
      PORT: String(enginePort), TESTING_DATA_DIR: mediaDir, TESTING_ENGINE_TOKEN: serviceToken,
      TESTING_API_URL: apiUrl, TESTING_RECORDING_MODE: "full" }
  });
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt++) {
    try { ready = (await fetch(engineUrl + "/healthz")).ok; } catch { /* Wait only for this fixture process. */ }
    if (ready) break;
    if (engine.exitCode !== null) throw new Error("fixture_engine_failed");
    await new Promise(done => setTimeout(done, 100));
  }
  if (!ready) throw new Error("fixture_engine_not_ready");
  return { apiUrl, engineUrl, mediaDir, pool, candidateId, profiles,
    /** Remove only fixture-owned processes, schema and temporary recordings. */
    async close() {
      engine?.kill("SIGTERM");
      await app.close(); await pool.end();
      await admin.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`); await admin.end();
      if (mediaDir) await rm(mediaDir, { recursive: true, force: true });
    } };
  } catch (error) {
    engine?.kill("SIGTERM");
    await app.close().catch(() => undefined); await pool.end();
    await admin.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`).catch(() => undefined); await admin.end();
    if (mediaDir) await rm(mediaDir, { recursive: true, force: true });
    throw error;
  }
}

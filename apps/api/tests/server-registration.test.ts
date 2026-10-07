/** Register the complete production server graph; individual gateway tests cannot catch namespace collisions. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { createApiServer } from "../src/server.js";

test("full server registers matching, editorial CRUD and authenticated public routes without collisions", async () => {
  const changes = {
    AUTH_SECRET: "synthetic-only-registration-test-secret-32-bytes",
    AUTH_ALLOWED_ORIGINS: "http://localhost:39880", AUTH_COOKIE_SECURE: "false", NODE_ENV: "test",
    // Registration must not connect to a database or start a listener.
    DATABASE_URL: "postgres://synthetic:synthetic@127.0.0.1:1/registration_only",
  };
  const previous = Object.fromEntries(Object.keys(changes).map(key => [key, process.env[key]]));
  Object.assign(process.env, changes);
  let app;
  try {
    app = await createApiServer({ logger: false });
    await app.ready();
    for (const [method, url] of [
      ["GET", "/internal/vacancies/:vacancy_id"], ["PUT", "/internal/vacancies/:vacancy_id"],
      ["GET", "/internal/vacancies/ready"], ["GET", "/internal/agents/vacancies"],
      ["POST", "/internal/agents/vacancies"], ["GET", "/internal/agents/vacancies/:draft_id"],
      ["PATCH", "/internal/agents/vacancies/:draft_id"], ["DELETE", "/internal/agents/vacancies/:draft_id"],
      ["POST", "/internal/agents/vacancies/:draft_id/approve"], ["POST", "/internal/agents/vacancies/:draft_id/publish"],
      ["POST", "/internal/agents/vacancies/:draft_id/close"], ["GET", "/employer/vacancies"],
      ["POST", "/employer/vacancies"], ["GET", "/employer/vacancies/:draft_id"],
      ["PATCH", "/employer/vacancies/:draft_id"], ["DELETE", "/employer/vacancies/:draft_id"],
      ["GET", "/candidate/recruitment/:id/chat/events"], ["GET", "/employer/recruitment-events/stream"],
    ] as const) assert.equal(app.hasRoute({ method, url }), true, `${method} ${url}`);
    assert.equal((await app.inject({ url: "/healthz" })).statusCode, 200);
    const paths = app.swagger().paths ?? {};
    assert.ok(paths["/internal/vacancies/{vacancy_id}"]?.get);
    assert.ok(paths["/internal/agents/vacancies/{draft_id}"]?.delete);
    assert.ok(paths["/employer/vacancies/{draft_id}"]?.patch);
  } finally {
    if (app) await app.close();
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});

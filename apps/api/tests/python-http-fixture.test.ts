/** Exercise startup deadlines and owned-child cleanup without external services. */
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { startPythonHttpFixture } from "./helpers/python-http-fixture.js";

/** A disposable executable models delayed or failed process startup, not PDF parsing. */
async function executable(body: string) {
  const directory = await mkdtemp(join(tmpdir(), "comit-http-fixture-"));
  const path = join(directory, "child");
  const pidFile = join(directory, "pid");
  await writeFile(path, `#!${process.execPath}\nrequire('node:fs').writeFileSync(${JSON.stringify(pidFile)}, String(process.pid));\n${body}\n`, { mode: 0o700 });
  const previous = process.env.TESTING_ENGINE_TEST_PYTHON;
  process.env.TESTING_ENGINE_TEST_PYTHON = path;
  return {
    async assertStopped() {
      const pid = Number(await readFile(pidFile, "utf8"));
      assert.throws(() => process.kill(pid, 0), { code: "ESRCH" });
    },
    async dispose() {
      if (previous === undefined) delete process.env.TESTING_ENGINE_TEST_PYTHON;
      else process.env.TESTING_ENGINE_TEST_PYTHON = previous;
      await rm(directory, { recursive: true, force: true });
    },
  };
}

test("HTTP fixture permits bounded startup beyond the former 15-second limit", async () => {
  const child = await executable(`setTimeout(() => {
    require('node:http').createServer((_request, response) => response.end('ok'))
      .listen(Number(process.argv[process.argv.indexOf('--port') + 1]), '127.0.0.1');
  }, 16000);`);
  try {
    const fixture = await startPythonHttpFixture("agents-service", {});
    try { assert.equal((await fetch(fixture.url + "/healthz")).status, 200); }
    finally { await fixture.close(); }
    await child.assertStopped();
  } finally { await child.dispose(); }
});

test("HTTP fixture reports early exit without printing child output", async () => {
  const child = await executable("console.error('synthetic-private-output'); process.exit(23);");
  try {
    await assert.rejects(startPythonHttpFixture("agents-service", {}), error => {
      assert.match(String(error), /startup_failed/);
      assert.doesNotMatch(String(error), /synthetic-private-output/);
      return true;
    });
    await child.assertStopped();
  } finally { await child.dispose(); }
});

test("HTTP fixture detects signal termination and failed spawn promptly", async () => {
  const child = await executable("process.kill(process.pid, 'SIGTERM');");
  try {
    await assert.rejects(startPythonHttpFixture("agents-service", {}), /startup_failed: exit=none signal=SIGTERM/);
    await child.assertStopped();
    process.env.TESTING_ENGINE_TEST_PYTHON += ".missing";
    await assert.rejects(startPythonHttpFixture("agents-service", {}), /startup_failed/);
  } finally { await child.dispose(); }
});

test("HTTP fixture times out and kills only its TERM-resistant child", async () => {
  const child = await executable("process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);");
  try {
    await assert.rejects(startPythonHttpFixture("agents-service", {}, { startupTimeoutMs: 1000 }), /health_timeout/);
    await child.assertStopped();
  } finally { await child.dispose(); }
});

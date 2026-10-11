/** Real private Python HTTP fixtures; missing runtimes fail acceptance instead of skipping it. */
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

/** Start a real service within 45s (including CI contention); close only its owned child.
 * The shorter deadline override supports deterministic timeout/cleanup regression tests.
 */
export async function startPythonHttpFixture(service: "integration-engine" | "agents-service", env: Record<string, string>, options: { startupTimeoutMs?: number } = {}) {
  const startupTimeoutMs = options.startupTimeoutMs ?? 45000;
  if (!Number.isFinite(startupTimeoutMs) || startupTimeoutMs <= 0 || startupTimeoutMs > 45000) throw new Error("fixture_invalid_startup_timeout");
  const server = createServer();
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("fixture_port_unavailable");
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  const root = fileURLToPath(new URL("../../../../", import.meta.url));
  const child = spawn(process.env.TESTING_ENGINE_TEST_PYTHON ?? "python3", ["-m", "uvicorn", `${service.replaceAll("-", "_")}.main:create_app`, "--factory", "--host", "127.0.0.1", "--port", String(address.port)], {
    cwd: root, stdio: "ignore", env: { PATH: process.env.PATH, PYTHONPATH: join(root, "services", service, "src"), PYTHONDONTWRITEBYTECODE: "1", ...env },
  });
  let spawnError = false;
  child.on("error", () => { spawnError = true; });
  const url = `http://127.0.0.1:${address.port}`;
  const exited = () => child.exitCode !== null || child.signalCode !== null;
  function waitForExit(timeoutMs: number): Promise<boolean> {
    if (exited()) return Promise.resolve(true);
    return new Promise(resolve => {
      const finished = () => { clearTimeout(timer); child.off("exit", finished); resolve(true); };
      const timer = setTimeout(() => { child.off("exit", finished); resolve(exited()); }, timeoutMs);
      child.once("exit", finished);
    });
  }
  async function close() {
    if (exited() || !child.pid) return;
    child.kill("SIGTERM");
    if (await waitForExit(2000)) return;
    child.kill("SIGKILL");
    if (!await waitForExit(2000)) throw new Error(`fixture_${service}_cleanup_timeout`);
  }
  try {
    const deadline = performance.now() + startupTimeoutMs;
    while (performance.now() < deadline) {
      if (spawnError || exited()) throw new Error(`fixture_${service}_startup_failed: exit=${child.exitCode ?? "none"} signal=${child.signalCode ?? "none"}; install service Python dependencies and set TESTING_ENGINE_TEST_PYTHON`);
      try {
        const remaining = Math.max(1, Math.ceil(deadline - performance.now()));
        const response = await fetch(url + "/healthz", { signal: AbortSignal.timeout(Math.min(500, remaining)) });
        await response.body?.cancel();
        if (response.ok && !exited() && performance.now() < deadline) return { url, close };
      } catch { /* Wait only for this isolated child, with a bounded health deadline. */ }
      await waitForExit(Math.max(1, Math.min(100, deadline - performance.now())));
    }
    throw new Error(`fixture_${service}_health_timeout`);
  } catch (error) { await close(); throw error; }
}

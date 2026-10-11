/** Real private Python HTTP fixtures; missing runtimes fail acceptance instead of skipping it. */
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

/** Start the repository service with a minimal environment; close only its owned child process. */
export async function startPythonHttpFixture(service: "integration-engine" | "agents-service", env: Record<string, string>) {
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
  async function close() {
    if (child.exitCode !== null || child.signalCode !== null || !child.pid) return;
    const stopped = new Promise<void>(resolve => child.once("exit", () => resolve()));
    child.kill("SIGTERM");
    const timer = setTimeout(() => child.kill("SIGKILL"), 2000);
    try { await stopped; } finally { clearTimeout(timer); }
  }
  try {
    for (let attempt = 0; attempt < 150; attempt++) {
      if (spawnError || child.exitCode !== null) throw new Error(`fixture_${service}_startup_failed: install service Python dependencies and set TESTING_ENGINE_TEST_PYTHON`);
      try {
        if ((await fetch(url + "/healthz", { signal: AbortSignal.timeout(300) })).ok) return { url, close };
      } catch { /* Wait only for this isolated child, with a bounded health deadline. */ }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error(`fixture_${service}_health_timeout`);
  } catch (error) { await close(); throw error; }
}

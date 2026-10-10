/** Global bounded request controls and low-cardinality operational telemetry. */
import { AsyncLocalStorage } from "node:async_hooks";
import { createHmac, randomUUID } from "node:crypto";
import type { FastifyInstance } from "fastify";
import type { Pool } from "pg";

const context = new AsyncLocalStorage<{ requestId: string }>();
const requestIdPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

/** Accept a bounded log-safe correlation ID or generate a new opaque UUID. */
export function validatedRequestId(value: unknown): string {
  return typeof value === "string" && requestIdPattern.test(value) ? value : randomUUID();
}

/** Propagate the validated current request ID without forwarding browser credentials. */
export function correlatedFetch(transport: typeof fetch = fetch): typeof fetch {
  return async (input, init) => {
    const headers = new Headers(init?.headers);
    const requestId = context.getStore()?.requestId;
    if (requestId) headers.set("x-request-id", requestId);
    return transport(input, { ...init, headers });
  };
}

/** Injectable clock/database permit deterministic operational tests without global state. */
export interface OperationsOptions {
  pool?: Pool; secret?: string; limit?: number; windowSeconds?: number; maxFallbackKeys?: number; now?: () => number;
}

/** Install root hooks before routes; database failure fails closed instead of resetting shared limits. */
export function installOperations(app: FastifyInstance, options: OperationsOptions = {}): void {
  const limit = options.limit ?? 240, windowSeconds = options.windowSeconds ?? 60;
  const maxKeys = options.maxFallbackKeys ?? 10000;
  if (!Number.isInteger(limit) || limit < 1 || !Number.isInteger(windowSeconds) || windowSeconds < 1 || !Number.isInteger(maxKeys) || maxKeys < 1) throw new Error("invalid_operations_configuration");
  const secret = options.secret ?? randomUUID();
  const now = options.now ?? Date.now;
  const fallback = new Map<string, { window: number; count: number }>();
  const counters = new Map<string, number>();
  const starts = new WeakMap<object, number>();
  const durations = [0.01, 0.05, 0.1, 0.5, 1, 5, 15, Infinity];
  const buckets = durations.map(() => 0);
  let sum = 0, count = 0, blocked = 0, unavailable = 0;
  app.addHook("onRequest", (request, reply, done) => {
    const supplied = request.headers["x-request-id"];
    request.id = typeof supplied === "string" && requestIdPattern.test(supplied) ? supplied : validatedRequestId(request.id);
    reply.header("x-request-id", request.id);
    starts.set(request, now());
    context.run({ requestId: request.id }, done);
  });
  app.addHook("onRequest", async (request, reply) => {
    // Probes and metrics must remain inspectable when ordinary traffic is exhausted.
    if (["/healthz", "/ready", "/metrics"].includes(request.url.split("?")[0]!)) return;
    const window = Math.floor(now() / 1000 / windowSeconds) * windowSeconds;
    const key = createHmac("sha256", secret).update(request.ip).digest("hex");
    let requests: number;
    if (options.pool) {
      try {
        const row = await options.pool.query<{ request_count: number }>(`INSERT INTO api_rate_windows(key_hash,window_start,request_count)
          VALUES($1,$2,1) ON CONFLICT(key_hash,window_start) DO UPDATE
          SET request_count=LEAST(api_rate_windows.request_count+1,$3) RETURNING request_count`, [key, window, limit + 1]);
        requests = row.rows[0]!.request_count;
        await options.pool.query("DELETE FROM api_rate_windows WHERE window_start < $1", [window - windowSeconds]);
      } catch { unavailable++; return reply.code(503).send({ detail: "rate_limit_unavailable" }); }
    } else {
      for (const [existing, value] of fallback) if (value.window !== window) fallback.delete(existing);
      let value = fallback.get(key);
      // A full fallback fails closed for new addresses; it never evicts another client's quota.
      if (!value && fallback.size >= maxKeys) { blocked++; return reply.code(429).header("retry-after", String(windowSeconds)).send({ detail: "rate_limited" }); }
      if (!value) { value = { window, count: 0 }; fallback.set(key, value); }
      requests = value.count = Math.min(value.count + 1, limit + 1);
    }
    if (requests > limit) {
      blocked++;
      const retry = Math.max(1, window + windowSeconds - Math.floor(now() / 1000));
      return reply.code(429).header("retry-after", String(retry)).send({ detail: "rate_limited" });
    }
  });
  app.addHook("onResponse", async (request, reply) => {
    const method = ["GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"].includes(request.method) ? request.method : "OTHER";
    const key = `${method}|${Math.floor(reply.statusCode / 100)}xx`;
    counters.set(key, (counters.get(key) ?? 0) + 1);
    const duration = Math.max(0, now() - (starts.get(request) ?? now())) / 1000;
    sum += duration; count++;
    durations.forEach((boundary, index) => { if (duration <= boundary) buckets[index] = buckets[index]! + 1; });
  });
  app.get("/metrics", { schema: { tags: ["operations"], response: { 200: { type: "string" } } } }, async (_request, reply) => {
    const lines = ["# TYPE comit_api_requests_total counter"];
    for (const [key, value] of counters) {
      const [method, status] = key.split("|");
      lines.push(`comit_api_requests_total{method="${method}",status_class="${status}"} ${value}`);
    }
    lines.push("# TYPE comit_api_request_duration_seconds histogram");
    durations.forEach((boundary, index) => lines.push(`comit_api_request_duration_seconds_bucket{le="${boundary === Infinity ? "+Inf" : boundary}"} ${buckets[index]}`));
    lines.push(`comit_api_request_duration_seconds_sum ${sum}`, `comit_api_request_duration_seconds_count ${count}`,
      "# TYPE comit_api_rate_limited_total counter", `comit_api_rate_limited_total ${blocked}`,
      "# TYPE comit_api_rate_unavailable_total counter", `comit_api_rate_unavailable_total ${unavailable}`);
    return reply.type("text/plain; version=0.0.4; charset=utf-8").send(`${lines.join("\n")}\n`);
  });
}

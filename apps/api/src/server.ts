/**
 * api-core entrypoint: Fastify + OpenAPI.
 * Owns auth, profiles, vacancies, invitations/statuses, rating ledger and the
 * visibility rules of TZ §2.2. Engines stay behind it (context/40-architecture.md).
 */
import Fastify, { type FastifyInstance } from "fastify";
import { isIP } from "node:net";
import { fileURLToPath } from "node:url";
import swagger from "@fastify/swagger";
import swaggerUi from "@fastify/swagger-ui";
import { healthRoutes } from "./routes/health.js";
import { candidatePipelineRoutes, candidateValidationOptions } from "./routes/candidate-pipeline.js";
import { agentsRoutes } from "./routes/agents.js";
import cookie from "@fastify/cookie";
import { authDomain } from "./auth/domain.js";
import { Pool } from "pg";
import { loadAuthConfig } from "./auth/config.js";
import { installOperations, validatedRequestId } from "./operations/index.js";

/** Trust only explicitly configured proxy addresses/CIDRs; hostnames and trust-all flags fail closed. */
export function proxyTrustConfig(value: string | undefined): false | string[] {
  if (!value?.trim()) return false;
  const addresses = value.split(",").map(address => address.trim());
  for (const address of addresses) {
    const [ip, prefix, extra] = address.split("/");
    const version = isIP(ip ?? "");
    if (!version || extra !== undefined || (prefix !== undefined &&
      (!/^\d+$/.test(prefix) || Number(prefix) < 1 || Number(prefix) > (version === 4 ? 32 : 128)))) {
      throw new Error("TRUST_PROXY requires explicit IP addresses or non-universal CIDRs");
    }
  }
  return addresses;
}

/** Build the complete production route graph without listening, enabling registration regressions. */
export async function createApiServer({ logger = true }: { logger?: boolean } = {}): Promise<FastifyInstance> {
  const app = Fastify({
    logger: logger ? { redact: ["req.headers.authorization", "req.headers.cookie", "req.headers.x-internal-token", "res.headers.set-cookie"] } : false,
    trustProxy: proxyTrustConfig(process.env.TRUST_PROXY),
    ajv: { customOptions: candidateValidationOptions },
    requestIdHeader: false,
    genReqId: request => validatedRequestId(request.headers["x-request-id"]),
  });

  let operationConfig: ReturnType<typeof loadAuthConfig> | undefined;
  try { operationConfig = loadAuthConfig(); } catch { /* Offline APIs still receive a bounded local quota. */ }
  const operationsPool = operationConfig && process.env.DATABASE_URL ? new Pool({ connectionString: process.env.DATABASE_URL,
    max: 4, connectionTimeoutMillis: 5000, statement_timeout: 5000 }) : undefined;
  if (operationsPool) {
    operationsPool.on("error", () => app.log.error({ code: "operations_database_unavailable" }, "Operations database unavailable"));
    app.addHook("onClose", () => operationsPool.end());
  }

  await app.register(swagger, {
    openapi: {
      info: {
        title: "Comit API",
        description: "api-core of the Comit hiring platform (LCT 2026, FSP track).",
        version: "0.1.0",
      },
      tags: [{ name: "health", description: "Liveness and readiness probes" }],
      components: { securitySchemes: {
        internalToken: { type: "apiKey", in: "header", name: "X-Internal-Token" },
        bearerAuth: { type: "http", scheme: "bearer", bearerFormat: "JWT" },
        candidateSession: { type: "apiKey", in: "cookie", name: "__Host-comit_candidate_session" },
        employerSession: { type: "apiKey", in: "cookie", name: "__Host-comit_employer_session" },
      } },
    },
  });
  installOperations(app, { ...(operationsPool ? { pool: operationsPool } : {}), ...(operationConfig ? { secret: operationConfig.secret } : {}),
    limit: Number(process.env.API_RATE_LIMIT ?? 240), windowSeconds: Number(process.env.API_RATE_WINDOW_SECONDS ?? 60),
    maxFallbackKeys: Number(process.env.API_RATE_FALLBACK_KEYS ?? 10000) });
  await app.register(swaggerUi, { routePrefix: "/docs" });
  await app.register(healthRoutes, { readiness: async () => {
    if (!operationConfig || !operationsPool) return false;
    const result = await operationsPool.query("SELECT 1 FROM auth_migrations WHERE name=$1", ["020_training_consent_not_null.sql"]);
    return result.rowCount === 1;
  } });
  await app.register(cookie);
  await app.register(candidatePipelineRoutes);
  await app.register(agentsRoutes);
  await app.register(authDomain);
  return app;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const app = await createApiServer();
  const port = Number(process.env.PORT ?? 8080);
  await app.listen({ port, host: "0.0.0.0" });
}

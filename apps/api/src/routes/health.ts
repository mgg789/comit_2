import type { FastifyInstance } from "fastify";

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
}

/** Health routes are the smoke-gate contract: every service answers /healthz (docs/ci.md). */
export async function healthRoutes(app: FastifyInstance): Promise<void> {
  app.get<{ Reply: HealthResponse }>(
    "/healthz",
    {
      schema: {
        tags: ["health"],
        response: {
          200: {
            type: "object",
            properties: {
              status: { type: "string", enum: ["ok"] },
              service: { type: "string" },
              version: { type: "string" },
            },
            required: ["status", "service", "version"],
          },
        },
      },
    },
    async () => ({ status: "ok", service: "api-core", version: "0.1.0" }),
  );

  // DB connectivity check joins here once migrations land (S1); /ready must fail
  // loudly when Postgres is unreachable so the LB stops routing to a broken pod.
  app.get<{ Reply: HealthResponse }>("/ready", { schema: { tags: ["health"] } }, async () => ({
    status: "ok",
    service: "api-core",
    version: "0.1.0",
  }));
}

import type { FastifyInstance } from "fastify";

export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
}

/** Health routes are the smoke-gate contract: every service answers /healthz (docs/ci.md). */
export async function healthRoutes(app: FastifyInstance, options: { readiness?: () => Promise<boolean> } = {}): Promise<void> {
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

  app.get("/ready", { schema: { tags: ["health"], response: {
    200: { type: "object", properties: { status: { const: "ok" }, service: { type: "string" }, version: { type: "string" } } },
    503: { type: "object", properties: { status: { const: "unavailable" }, service: { type: "string" } } }
  } } }, async (_request, reply) => {
    try {
      if (options.readiness && await options.readiness()) return { status: "ok", service: "api-core", version: "0.1.0" };
    } catch { /* Readiness never exposes connection strings or provider errors. */ }
    return reply.code(503).send({ status: "unavailable", service: "api-core" });
  });
}

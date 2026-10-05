/**
 * api-core entrypoint: Fastify + OpenAPI.
 * Owns auth, profiles, vacancies, invitations/statuses, rating ledger and the
 * visibility rules of TZ §2.2. Engines stay behind it (context/40-architecture.md).
 */
import Fastify from "fastify";
import swagger from "@fastify/swagger";
import swaggerUi from "@fastify/swagger-ui";
import { healthRoutes } from "./routes/health.js";
import { candidatePipelineRoutes, candidateValidationOptions } from "./routes/candidate-pipeline.js";
import { agentsRoutes } from "./routes/agents.js";

const app = Fastify({
  logger: true,
  trustProxy: true,
  ajv: { customOptions: candidateValidationOptions },
});

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
    } },
  },
});
await app.register(swaggerUi, { routePrefix: "/docs" });
await app.register(healthRoutes);
await app.register(candidatePipelineRoutes);
await app.register(agentsRoutes);

const port = Number(process.env.PORT ?? 8080);
await app.listen({ port, host: "0.0.0.0" });

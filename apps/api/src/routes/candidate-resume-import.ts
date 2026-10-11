/** Candidate-owned ephemeral PDF/photo import; it never writes profile or verification state. */
import type { FastifyInstance } from "fastify";
import type { ResumeImportRequest } from "@comit/shared";
import type { AuthOptions } from "./auth.js";
import { originAllowed, resolveSession } from "../auth/session.js";
import { correlatedFetch } from "../operations/index.js";

/** Injectable service transport for real HTTP integration and isolated authorization tests. */
export interface ResumeImportOptions extends AuthOptions { transport?: typeof fetch; serviceUrl?: string; serviceToken?: string }
const text = (maxLength: number) => ({ type: "string", maxLength });
const narrative = { type: "array", maxItems: 1, items: { type: "object", additionalProperties: false, required: ["title", "description"], properties: { title: text(300), description: text(10000) } } };
const resultSchema = { type: "object", additionalProperties: false, required: ["fields", "extracted_text", "method", "warnings"], properties: {
  fields: { type: "object", additionalProperties: false, properties: { display_name: text(160), city: text(160), phone: text(64), description: text(10000),
    primary_direction: { enum: ["backend", "frontend", "fullstack", "mobile", "devops", "data", "ml", "qa"] }, declared_grade: { enum: ["intern", "junior", "middle", "senior"] },
    work_types: { type: "array", items: { enum: ["onsite", "remote", "hybrid"] } }, experience: narrative, projects: narrative, achievements: narrative } },
  extracted_text: text(100000), method: { enum: ["pdf_text", "ocr", "mixed"] }, warnings: { type: "array", maxItems: 5, items: text(300) },
} };

/** Require explicit processing consent and derive owner exclusively from the candidate session. */
export async function candidateResumeImportRoutes(app: FastifyInstance, options: ResumeImportOptions): Promise<void> {
  const transport = correlatedFetch(options.transport ?? fetch);
  const owners = new WeakMap<object, string>();
  const active = new Set<string>();
  app.post<{ Body: ResumeImportRequest }>("/candidate/profile/import", {
    bodyLimit: 7 * 1024 * 1024,
    schema: { tags: ["candidate-profile"], security: [{ bearerAuth: [] }, { candidateSession: [] }], body: {
      type: "object", additionalProperties: false, required: ["filename", "content_base64", "processing_consent"], properties: {
        filename: { ...text(200), minLength: 1 }, content_base64: { ...text(6990508), minLength: 4 }, processing_consent: { const: true, type: "boolean" },
      },
    }, response: { 200: resultSchema } },
    onRequest: async (request, reply) => {
      reply.header("cache-control", "no-store");
      try {
        const identity = await resolveSession(request, "candidate", options.config, options.store);
        if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
        if (!originAllowed(request, options.config, identity.transport === "bearer")) return reply.code(403).send({ detail: "origin_forbidden" });
        owners.set(request, identity.accountId);
      } catch { return reply.code(503).send({ detail: "auth_unavailable" }); }
    },
  }, async (request, reply) => {
    const owner = owners.get(request);
    if (!owner) return reply.code(401).send({ detail: "unauthenticated" });
    const token = options.serviceToken ?? process.env.AGENTS_SERVICE_TOKEN;
    if (!token) return reply.code(503).send({ detail: "resume_parser_unavailable" });
    if (active.has(owner) || active.size >= 4) return reply.code(429).send({ detail: "resume_parser_busy" });
    active.add(owner);
    try {
      const result = await transport(new URL("/internal/candidate-resume/parse", options.serviceUrl ?? process.env.AGENTS_SERVICE_URL ?? "http://agents-service:8104"), {
        method: "POST", headers: { "content-type": "application/json", "x-internal-token": token, "x-owner-id": owner },
        body: JSON.stringify(request.body), signal: AbortSignal.timeout(50000), redirect: "error",
      });
      if (!result.ok) {
        const payload: unknown = await result.json();
        const detail = typeof payload === "object" && payload !== null && "detail" in payload && typeof payload.detail === "string" && /^resume_[a-z_]+$/.test(payload.detail) ? payload.detail : "resume_parse_failed";
        return reply.code(result.status === 422 ? 422 : 503).send({ detail });
      }
      const payload: unknown = await result.json();
      if (!request.compileValidationSchema(resultSchema)(payload)) return reply.code(503).send({ detail: "resume_parser_unavailable" });
      return reply.send(payload);
    } catch { return reply.code(503).send({ detail: "resume_parser_unavailable" }); }
    finally { active.delete(owner); }
  });
}

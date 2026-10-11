/** Anonymous catalogue cards; no detail, contact, private identity or PDF capability. */
import { createHash, createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import type { FastifyInstance, FastifyRequest } from "fastify";
import type { AgentsDraftView, PublicShowcaseCandidate, PublicShowcaseVacancy, PublicShowcasePage, VacancyReadyPage } from "@comit/shared";
import type { AccountGatewayOptions } from "./accounts.js";
import { correlatedFetch } from "../operations/index.js";

const text = { type: "string" };
const nullable = (schema: object) => ({ anyOf: [schema, { type: "null" }] });
const grades = ["intern", "junior", "middle", "senior"];
const specializations = ["backend", "frontend", "fullstack", "mobile", "devops", "data", "qa", "ml"];
const salary = { type: "object", additionalProperties: false, required: ["from_rub", "to_rub"], properties: {
  from_rub: { type: "integer", minimum: 0 }, to_rub: { type: "integer", minimum: 0 } } };
const common = { city: text, salary, stacks: { type: "array", items: text }, published_at: { type: "string", format: "date-time" } };
const candidateSchema = { type: "object", additionalProperties: false, required: ["candidate_id", "display_name", ...Object.keys(common), "category"], properties: {
  candidate_id: { type: "string", pattern: "^[a-f0-9]{64}$" }, display_name: { type: "string", const: "Специалист" }, ...common, salary: { ...salary, properties: { ...salary.properties, to_rub: nullable(salary.properties.to_rub) } },
  category: { type: "object", additionalProperties: false, required: ["specialization", "grade", "grade_status"], properties: {
    specialization: { type: "string", enum: specializations }, grade: { type: "string", enum: grades }, grade_status: { type: "string", enum: ["approved", "self_reported"] } } } } };
const vacancySchema = { type: "object", additionalProperties: false, required: ["vacancy_id", "title", "company_name", ...Object.keys(common), "schedule", "category", "work_types"], properties: {
  vacancy_id: { type: "string", format: "uuid" }, title: text, company_name: nullable(text), ...common, schedule: text,
  work_types: { type: "array", items: { type: "string", enum: ["onsite", "remote", "hybrid"] } },
  category: { type: "object", additionalProperties: false, required: ["specialization", "grade"], properties: {
    specialization: nullable({ type: "string", enum: specializations }), grade: nullable({ type: "string", enum: grades }) } } } };
const pageSchema = (item: object) => ({ type: "object", additionalProperties: false, required: ["items", "limit", "has_more", "next_cursor"], properties: {
  items: { type: "array", maxItems: 50, items: item }, limit: { type: "integer", minimum: 1, maximum: 50 },
  has_more: { type: "boolean" }, next_cursor: nullable({ type: "string", maxLength: 2048 }) } });
const querySchema = { type: "object", additionalProperties: false, properties: { q: { type: "string", maxLength: 500, pattern: "^[^\\u0000]*$" },
  limit: { type: "string", pattern: "^(?:[1-9]|[1-4][0-9]|50)$" }, cursor: { type: "string", maxLength: 2048 },
  specialization: { type: "string", enum: specializations }, grade: { type: "string", enum: grades }, stack: { type: "string", pattern: "^[a-z][a-z0-9_-]{0,99}$" },
  sort: { type: "string", enum: ["newest", "oldest"] } } };
type Query = { q?: string; limit?: string; cursor?: string; specialization?: string; grade?: string; stack?: string; sort?: string };
type CandidateSource = PublicShowcaseCandidate & { profile_revision: number };
const ref = (namespace: string, name: string) => ({ $ref: `urn:comit:public:${namespace}:${name}` });

/** Register read-only global lists with per-request source consent/publication rechecks. */
export async function publicShowcaseRoutes(app: FastifyInstance, options: AccountGatewayOptions): Promise<void> {
  const transport = correlatedFetch(options.transport ?? fetch);
  const matchingUrl = options.matchingUrl ?? process.env.MATCHING_ENGINE_URL ?? "http://matching-engine:8101";
  const matchingToken = options.matchingToken ?? process.env.CANDIDATE_PIPELINE_TOKEN;
  const agentsUrl = options.agentsUrl ?? process.env.AGENTS_SERVICE_URL ?? "http://agents-service:8104";
  const agentsToken = options.agentsToken ?? process.env.AGENTS_SERVICE_TOKEN;
  // Anonymous continuations contain no visible source UUID, including base64-decoded payloads.
  const cursorKey = createHash("sha256").update(`comit-public-cursor-v1:${options.config.secret}`).digest();
  const sealCursor = (value: string | null | undefined, kind: string): string | null => {
    if (!value) return null;
    const iv = randomBytes(12), cipher = createCipheriv("aes-256-gcm", cursorKey, iv);
    cipher.setAAD(Buffer.from(kind));
    return Buffer.concat([iv, cipher.update(value, "utf8"), cipher.final(), cipher.getAuthTag()]).toString("base64url");
  };
  const openCursor = (value: string, kind: string): string => {
    try { const bytes = Buffer.from(value, "base64url"); if (bytes.length < 29) throw new Error("invalid_cursor");
      const cipher = createDecipheriv("aes-256-gcm", cursorKey, bytes.subarray(0, 12));
      cipher.setAAD(Buffer.from(kind)); cipher.setAuthTag(bytes.subarray(-16));
      return Buffer.concat([cipher.update(bytes.subarray(12, -16)), cipher.final()]).toString("utf8");
    } catch { throw { status: 409 }; }
  };
  const read = async (request: FastifyRequest, url: URL, token: string, schema: object, owner?: string, deadline?: AbortSignal): Promise<unknown> => {
    const response = await transport(url, { headers: { "x-internal-token": token, ...(owner ? { "x-owner-id": owner } : {}) },
      signal: deadline ?? AbortSignal.timeout(10000), redirect: "error" });
    if (!response.ok) throw { status: response.status === 409 ? 409 : 503 };
    const reader = response.body?.getReader(); if (!reader) throw { status: 503 };
    const chunks: Uint8Array[] = []; let size = 0;
    while (true) { const part = await reader.read(); if (part.done) break; size += part.value.byteLength;
      if (size > 4 * 1024 * 1024) { await reader.cancel(); throw { status: 503 }; } chunks.push(part.value); }
    const value: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!request.compileValidationSchema(schema)(value)) throw { status: 503 };
    return value;
  };
  for (const kind of ["candidates", "vacancies"] as const) {
    app.get<{ Querystring: Query }>(`/public/showcase/${kind}`, { schema: { tags: ["public-showcase"], security: [], querystring: querySchema,
      response: { 200: pageSchema(kind === "candidates" ? candidateSchema : vacancySchema),
        409: { type: "object", required: ["detail"], properties: { detail: text } },
        503: { type: "object", required: ["detail"], properties: { detail: text } } } } }, async (request, reply) => {
      reply.header("cache-control", "no-store").header("x-content-type-options", "nosniff");
      if (!matchingToken || !options.profiles || (kind === "vacancies" && !agentsToken)) return reply.code(503).send({ detail: "showcase_unavailable" });
      try {
        const deadline = AbortSignal.timeout(15000);
        const url = new URL(`/internal/public/showcase/${kind}`, matchingUrl);
        for (const [key, value] of Object.entries(request.query)) if (value !== undefined) url.searchParams.set(key, key === "cursor" ? openCursor(value, kind) : value);
        if (kind === "candidates") {
          const source = await read(request, url, matchingToken, ref("matching", "CatalogueCandidatePage"), undefined, deadline) as PublicShowcasePage<CandidateSource>;
          const items: PublicShowcaseCandidate[] = [];
          for (const card of source.items) {
            if (!await options.profiles.isSelectionCurrent(card.candidate_id, card.profile_revision)) continue;
            items.push({ candidate_id: createHash("sha256").update(`public-candidate:${card.candidate_id}`).digest("hex"), display_name: "Специалист",
              city: card.city, salary: card.salary, category: card.category, stacks: card.stacks, published_at: card.published_at });
          }
          return reply.send({ items, limit: source.limit, has_more: source.has_more, next_cursor: sealCursor(source.next_cursor, kind) });
        }
        const source = await read(request, url, matchingToken, ref("matching", "VacancyReadyPage"), undefined, deadline) as VacancyReadyPage;
        const items: PublicShowcaseVacancy[] = [];
        for (const input of source.items) {
          if (options.vacancyLifecycle && await options.vacancyLifecycle.pending(input.vacancy_id)) continue;
          const draft = await read(request, new URL(`/internal/vacancy-drafts/${input.vacancy_id}`, agentsUrl), agentsToken!, ref("agents", "DraftView"), input.owner_id, deadline) as AgentsDraftView;
          if (draft.owner_id !== input.owner_id || draft.vacancy_id !== input.vacancy_id || draft.revision !== input.source_revision
            || draft.publication?.source_revision !== draft.revision || draft.state !== "created" || draft.archived || draft.business_status !== "open"
            || draft.publication.status !== "accepted" || !draft.fields.title) continue;
          const metadata = draft.fields.metadata ?? {};
          const grade = typeof metadata.grade === "string" && grades.includes(metadata.grade) ? metadata.grade as PublicShowcaseVacancy["category"]["grade"] : null;
          items.push({ vacancy_id: input.vacancy_id, title: draft.fields.title,
            company_name: typeof metadata.company_name === "string" && metadata.company_name.trim() ? metadata.company_name : null,
            city: input.city, salary: { from_rub: input.zp_min, to_rub: input.zp_max }, work_types: [input.type], schedule: input.daily,
            category: { specialization: input.class ?? null, grade }, stacks: Object.keys(input.stacks), published_at: input.published_at });
        }
        return reply.send({ items, limit: source.limit, has_more: source.has_more, next_cursor: sealCursor(source.next_cursor, kind) });
      } catch (error) {
        const status = typeof error === "object" && error !== null && "status" in error && error.status === 409 ? 409 : 503;
        return reply.code(status).send({ detail: status === 409 ? "showcase_source_obsolete" : "showcase_unavailable" });
      }
    });
  }
}

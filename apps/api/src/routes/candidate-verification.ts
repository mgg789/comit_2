/** Candidate-owned provider checks. Provider facts are bound to the authenticated account by api-core. */
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { CandidateVerificationDocumentUpload, CandidateVerificationRequest } from "@comit/shared";
import type { AuthOptions } from "./auth.js";
import { originAllowed, resolveSession, type SessionIdentity } from "../auth/session.js";
import { ProfileError, type ProfileStore } from "../profiles/store.js";
import { candidateProfileViewSchema } from "../profiles/schemas.js";
import { correlatedFetch } from "../operations/index.js";

export interface CandidateVerificationOptions extends AuthOptions {
  profiles: ProfileStore;
  verificationUrl?: string;
  verificationTransport?: typeof fetch;
  verificationToken?: string;
  integrationUrl?: string;
  integrationPublicUrl?: string;
  integrationToken?: string;
}

const resultSchema = { type: "object", additionalProperties: true } as const;
type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
type ProviderResult = {
  kind: "fsp" | "repository" | "hh";
  status: "confirmed" | "partial" | "pending" | "rejected";
  verified_value: JsonValue | null;
  provenance: string[];
  error_code: string | null;
};

function boundedJson(value: unknown, depth = 0): value is JsonValue {
  if (depth > 8) return false;
  if (value === null || typeof value === "boolean" || typeof value === "string") return typeof value !== "string" || value.length <= 100_000;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.length <= 1000 && value.every(item => boundedJson(item, depth + 1));
  if (typeof value !== "object") return false;
  const entries = Object.entries(value);
  return entries.length <= 200 && entries.every(([key, item]) => key.length <= 200 && boundedJson(item, depth + 1));
}

function providerResult(payload: unknown, kind: ProviderResult["kind"], subject: string): ProviderResult | null {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const value = payload as Record<string, unknown>;
  const keys = Object.keys(value).sort().join(",");
  if (keys !== "error_code,kind,provenance,status,verified_value") return null;
  if (value.kind !== kind || !["confirmed", "partial", "pending", "rejected"].includes(String(value.status))) return null;
  if (!Array.isArray(value.provenance) || value.provenance.length > 10 || value.provenance.some(item => typeof item !== "string" || item.length > 1000)) return null;
  if (!(value.verified_value === null || boundedJson(value.verified_value))) return null;
  if (!(value.error_code === null || (typeof value.error_code === "string" && /^[a-z][a-z0-9_]{1,63}$/.test(value.error_code)))) return null;
  if (value.status !== "rejected" && (!value.verified_value || typeof value.verified_value !== "object" || Array.isArray(value.verified_value))) return null;
  const verified = value.verified_value as Record<string, unknown> | null;
  if (verified && kind === "fsp" && verified.fsp_id !== subject) return null;
  if (verified && kind === "hh" && verified.resume_id !== subject) return null;
  if (verified && kind === "repository" && verified.url !== subject) return null;
  return { kind, status: value.status as ProviderResult["status"], verified_value: value.verified_value as JsonValue | null,
    provenance: value.provenance as string[], error_code: value.error_code as string | null };
}

function isDemoParticipant(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  const keys = Object.keys(item).sort().join(",");
  if (keys !== "account_ref,achievements,certificates,demo,display_name,events,fsp_id,provider,provider_version,rank,rating") return false;
  return typeof item.fsp_id === "string" && typeof item.rank === "string" && Number.isSafeInteger(item.rating)
    && item.provider === "fsp_hackathon_registry" && item.demo === true && Array.isArray(item.achievements)
    && Array.isArray(item.events) && Array.isArray(item.certificates);
}

/** Decode only canonical base64 within the encrypted-document byte limit. */
export function decodeVerificationDocument(value: string): Buffer {
  if (value.length < 1 || value.length > 14_000_000 || value.length % 4 !== 0) throw new ProfileError(400, "invalid_document_encoding");
  const padding = value.indexOf("=");
  const dataEnd = padding < 0 ? value.length : padding;
  if ((padding >= 0 && (padding < value.length - 2 || value.slice(padding).length > 2))
    || value.slice(dataEnd).replace(/=/g, "").length > 0) throw new ProfileError(400, "invalid_document_encoding");
  for (let index = 0; index < dataEnd; index += 1) {
    const code = value.charCodeAt(index);
    if (!((code >= 65 && code <= 90) || (code >= 97 && code <= 122) || (code >= 48 && code <= 57) || code === 43 || code === 47)) throw new ProfileError(400, "invalid_document_encoding");
  }
  const content = Buffer.from(value, "base64");
  if (content.toString("base64") !== value || content.length > 10 * 1024 * 1024) throw new ProfileError(400, "invalid_document_encoding");
  return content;
}
export const candidateVerificationRequestSchema = {
  type: "object", additionalProperties: false, required: ["expected_revision"],
  properties: {
    expected_revision: { type: "integer", minimum: 1, maximum: Number.MAX_SAFE_INTEGER },
    fsp_id: { type: "string", minLength: 1, maxLength: 120, pattern: "^[A-Za-z0-9._:-]+$" },
    fsp_claim_id: { type: "string", minLength: 1, maxLength: 120, pattern: "^[A-Za-z0-9._:-]+$" },
    claimed_rating: { anyOf: [{ type: "integer", minimum: 0, maximum: 10000000 }, { type: "null" }] },
    origin: { type: "string", enum: ["mock", "live_unbound"] },
    repository_url: { type: "string", format: "uri", maxLength: 2000 },
    contribution_narrative: { type: "string", minLength: 1, maxLength: 10000 },
    hh_resume_id: { type: "string", minLength: 1, maxLength: 100, pattern: "^[A-Za-z0-9_-]+$" },
    claim_kind: { type: "string", enum: ["projects", "experience", "achievements"] },
    claim_id: { type: "string", format: "uuid" },
    demo_fixture_id: { type: "string", enum: ["fsp-001", "fsp-002"] },
  },
} as const;

/** Register FSP/repository verification and encrypted document metadata routes. */
export async function candidateVerificationRoutes(app: FastifyInstance, options: CandidateVerificationOptions): Promise<void> {
  const identities = new WeakMap<FastifyRequest, SessionIdentity>();
  const guard = async (request: FastifyRequest, reply: FastifyReply) => {
    try {
      const identity = await resolveSession(request, "candidate", options.config, options.store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      if (!originAllowed(request, options.config, identity.transport === "bearer")) return reply.code(403).send({ detail: "origin_forbidden" });
      identities.set(request, identity);
    } catch { return reply.code(503).send({ detail: "auth_unavailable" }); }
  };
  const transport = correlatedFetch(options.verificationTransport ?? fetch);
  const baseUrl = options.verificationUrl ?? process.env.VERIFICATION_ENGINE_URL ?? "http://verification-engine:8105";
  const verificationToken = options.verificationToken ?? process.env.VERIFICATION_ENGINE_TOKEN;
  const integrationUrl = options.integrationUrl ?? process.env.INTEGRATION_ENGINE_URL ?? "http://integration-engine:8103";
  const integrationPublicUrl = options.integrationPublicUrl ?? process.env.FSP_DEMO_PUBLIC_URL ?? "http://localhost:8103";
  const integrationToken = options.integrationToken ?? process.env.FSP_DEMO_INTERNAL_TOKEN;
  const invoke = async (path: string, body: unknown): Promise<{ status: number; payload: unknown }> => {
    if (!verificationToken || verificationToken.length > 4096) throw new ProfileError(503, "verification_auth_unavailable");
    const response = await transport(new URL(path, baseUrl), {
      method: "POST", headers: { "content-type": "application/json", "x-internal-token": verificationToken }, body: JSON.stringify(body),
      signal: AbortSignal.timeout(10000), redirect: "error",
    });
    let payload: unknown = null;
    try { payload = await response.json(); } catch { /* provider error is mapped below */ }
    return { status: response.status, payload };
  };
  app.post<{ Body: CandidateVerificationRequest; Reply: unknown }>("/candidate/profile/fsp/demo/authorize", {
    schema: { tags: ["candidate-verification"], security: [{ bearerAuth: [] }, { candidateSession: [] }], body: candidateVerificationRequestSchema, response: { 200: resultSchema } }, onRequest: guard,
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (process.env.NODE_ENV !== "development" || process.env.FSP_DEMO_ENABLED !== "true") return reply.code(503).send({ detail: "fsp_demo_disabled" });
    const fixture = request.body.demo_fixture_id;
    if (!identity || (fixture !== "fsp-001" && fixture !== "fsp-002")) return reply.code(400).send({ detail: "demo_fixture_required" });
    try {
      const state = await options.profiles.startFspDemoAuthorization(identity.accountId, request.body.expected_revision, fixture);
      const callbackUrl = process.env.FSP_DEMO_CALLBACK_URL ?? "http://localhost:8080/candidate/profile/fsp/demo/callback";
      const authorize = new URL("/fsp/demo/authorize", integrationPublicUrl);
      authorize.search = new URLSearchParams({ state: state.state, redirect_uri: callbackUrl, fixture_id: fixture }).toString();
      return reply.header("cache-control", "no-store").send({ state: state.state, expires_at: state.expires_at, authorize_url: authorize.toString(), callback_url: callbackUrl, demo: true });
    } catch (error) { return profileFailure(reply, error); }
  });
  app.post<{ Body: { expected_revision: number; state: string; code: string }; Reply: unknown }>("/candidate/profile/fsp/demo/callback", {
    schema: { tags: ["candidate-verification"], security: [{ bearerAuth: [] }, { candidateSession: [] }], body: { type: "object", additionalProperties: false, required: ["expected_revision", "state", "code"], properties: { expected_revision: { type: "integer", minimum: 1 }, state: { type: "string", minLength: 20, maxLength: 256 }, code: { type: "string", minLength: 20, maxLength: 256 } } }, response: { 200: candidateProfileViewSchema } }, onRequest: guard,
  }, async (request, reply) => {
    if (process.env.NODE_ENV !== "development" || process.env.FSP_DEMO_ENABLED !== "true") return reply.code(503).send({ detail: "fsp_demo_disabled" });
    const identity = identities.get(request);
    if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
    try {
      const fixture = await options.profiles.assertFspDemoState(identity.accountId, request.body.expected_revision, request.body.state);
      if (!integrationToken || integrationToken.length > 4096) throw new ProfileError(503, "verification_auth_unavailable");
      const response = await transport(new URL("/fsp/demo/token", integrationUrl), {
        method: "POST", headers: { "content-type": "application/json", "x-internal-token": integrationToken }, body: JSON.stringify({ code: request.body.code, state: request.body.state }),
        signal: AbortSignal.timeout(5000), redirect: "error",
      });
      if (!response.ok) return reply.code(response.status === 404 ? 404 : 422).send({ detail: "demo_code_invalid_or_replayed" });
      const payload: unknown = await response.json();
      if (!isDemoParticipant(payload)) return reply.code(503).send({ detail: "verification_invalid_response" });
      if (payload.fsp_id !== fixture) return reply.code(422).send({ detail: "demo_fixture_mismatch" });
      return reply.header("cache-control", "no-store").send(await options.profiles.completeFspDemoAuthorization(identity.accountId, request.body.expected_revision, request.body.state, payload));
    } catch (error) { return profileFailure(reply, error); }
  });
  app.post<{ Body: CandidateVerificationRequest; Reply: unknown }>("/candidate/profile/verifications/fsp", {
    schema: { tags: ["candidate-verification"], security: [{ bearerAuth: [] }, { candidateSession: [] }], body: candidateVerificationRequestSchema, response: { 200: candidateProfileViewSchema } }, onRequest: guard,
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (!identity || !request.body.fsp_id || !request.body.origin) return reply.code(400).send({ detail: "invalid_fsp_request" });
    try {
      const input = { fsp_id: request.body.fsp_id, claimed_rating: request.body.claimed_rating ?? null, origin: request.body.origin,
        ...(request.body.fsp_claim_id ? { claim_id: request.body.fsp_claim_id } : {}) };
      const response = await invoke("/verify/fsp", input);
      if (response.status >= 500) return reply.code(503).send({ detail: "verification_unavailable" });
      if (response.status >= 400) return reply.code(422).send({ detail: "verification_rejected" });
      const result = providerResult(response.payload, "fsp", request.body.fsp_id);
      if (!result) return reply.code(503).send({ detail: "verification_invalid_response" });
      if (result.status === "rejected") return reply.code(422).send({ detail: result.error_code ?? "verification_rejected" });
      return reply.header("cache-control", "no-store").send(await options.profiles.applyVerification(
        identity.accountId, request.body.expected_revision, `external:fsp:${request.body.fsp_id}${request.body.fsp_claim_id ? `:${request.body.fsp_claim_id}` : ""}`, input,
        { kind: "fsp", subject: request.body.fsp_id, ...(request.body.fsp_claim_id ? { claim_id: request.body.fsp_claim_id } : {}) },
        { status: result.status, verified_value: result.verified_value, provenance: result.provenance },
      ));
    } catch (error) { return profileFailure(reply, error); }
  });
  app.post<{ Body: CandidateVerificationRequest; Reply: unknown }>("/candidate/profile/verifications/repository", {
    schema: { tags: ["candidate-verification"], security: [{ bearerAuth: [] }, { candidateSession: [] }], body: candidateVerificationRequestSchema, response: { 200: candidateProfileViewSchema } }, onRequest: guard,
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (!identity || !request.body.repository_url || !request.body.contribution_narrative) return reply.code(400).send({ detail: "invalid_repository_request" });
    try {
      if (!request.body.claim_kind || !request.body.claim_id) return reply.code(400).send({ detail: "repository_claim_required" });
      const input = { url: request.body.repository_url, contribution_narrative: request.body.contribution_narrative };
      const response = await invoke("/verify/repository", input);
      if (response.status >= 500) return reply.code(503).send({ detail: "verification_unavailable" });
      if (response.status >= 400) return reply.code(422).send({ detail: "verification_rejected" });
      let subject: string;
      try {
        const parsed = new URL(request.body.repository_url);
        let pathname = parsed.pathname.replace(/\/$/, "");
        if (pathname.endsWith(".git")) pathname = pathname.slice(0, -4);
        subject = `${parsed.protocol}//${parsed.hostname.toLowerCase()}${pathname}`;
      } catch { return reply.code(400).send({ detail: "invalid_repository_url" }); }
      const result = providerResult(response.payload, "repository", subject);
      if (!result) return reply.code(503).send({ detail: "verification_invalid_response" });
      if (result.status === "rejected") return reply.code(422).send({ detail: result.error_code ?? "verification_rejected" });
      return reply.header("cache-control", "no-store").send(await options.profiles.applyVerification(
        identity.accountId, request.body.expected_revision, `external:repository:${request.body.claim_kind}:${request.body.claim_id}`, input,
        { kind: "repository", subject: request.body.claim_kind, claim_id: request.body.claim_id, url: subject, narrative: request.body.contribution_narrative },
        { status: result.status, verified_value: result.verified_value, provenance: result.provenance },
      ));
    } catch (error) { return profileFailure(reply, error); }
  });
  app.post<{ Body: CandidateVerificationRequest; Reply: unknown }>("/candidate/profile/verifications/hh", {
    schema: { tags: ["candidate-verification"], security: [{ bearerAuth: [] }, { candidateSession: [] }], body: candidateVerificationRequestSchema, response: { 200: candidateProfileViewSchema } }, onRequest: guard,
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (!identity || !request.body.hh_resume_id || request.body.claim_kind !== "experience" || !request.body.claim_id) return reply.code(400).send({ detail: "invalid_hh_request" });
    try {
      const response = await invoke("/verify/hh", { resume_id: request.body.hh_resume_id });
      if (response.status >= 500) return reply.code(503).send({ detail: "verification_unavailable" });
      if (response.status >= 400) return reply.code(422).send({ detail: "verification_rejected" });
      const result = providerResult(response.payload, "hh", request.body.hh_resume_id);
      if (!result) return reply.code(503).send({ detail: "verification_invalid_response" });
      if (result.status === "rejected") return reply.code(422).send({ detail: result.error_code ?? "verification_rejected" });
      return reply.header("cache-control", "no-store").send(await options.profiles.applyVerification(
        identity.accountId, request.body.expected_revision, `external:hh:${request.body.hh_resume_id}`, { resume_id: request.body.hh_resume_id },
        { kind: "hh", subject: request.body.hh_resume_id, claim_kind: "experience", claim_id: request.body.claim_id },
        { status: result.status, verified_value: result.verified_value, provenance: result.provenance },
      ));
    } catch (error) { return profileFailure(reply, error); }
  });
  app.post<{ Body: CandidateVerificationDocumentUpload; Reply: unknown }>("/candidate/profile/verification-documents", {
    bodyLimit: 14_000_000,
    schema: { tags: ["candidate-verification"], security: [{ bearerAuth: [] }, { candidateSession: [] }], body: {
      type: "object", additionalProperties: false, required: ["filename", "content_type", "content_base64", "claim_kind", "claim_id"],
      properties: { filename: { type: "string", minLength: 1, maxLength: 255 }, content_type: { type: "string", minLength: 1, maxLength: 120 }, content_base64: { type: "string", minLength: 1, maxLength: 14000000 }, claim_kind: { type: "string", enum: ["projects", "experience", "achievements"] }, claim_id: { type: "string", format: "uuid" } },
    }, response: { 201: resultSchema } }, onRequest: guard,
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
    try {
      const content = decodeVerificationDocument(request.body.content_base64);
      const saved = await options.profiles.uploadVerificationDocument(identity.accountId, request.body, content);
      return reply.code(201).header("cache-control", "no-store").send(saved);
    } catch (error) { return profileFailure(reply, error); }
  });
  app.get<{ Reply: unknown }>("/candidate/profile/verification-documents", {
    schema: { tags: ["candidate-verification"], security: [{ bearerAuth: [] }, { candidateSession: [] }], response: { 200: { type: "array", items: resultSchema } } }, onRequest: guard,
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
    try { return reply.header("cache-control", "no-store").send(await options.profiles.verificationDocuments(identity.accountId)); }
    catch (error) { return profileFailure(reply, error); }
  });
  app.get<{ Params: { id: string } }>("/candidate/profile/verification-documents/:id", {
    schema: { tags: ["candidate-verification"], security: [{ bearerAuth: [] }, { candidateSession: [] }], params: { type: "object", additionalProperties: false, required: ["id"], properties: { id: { type: "string", format: "uuid" } } } }, onRequest: guard,
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
    try {
      const document = await options.profiles.downloadVerificationDocument(identity.accountId, request.params.id);
      const safeFilename = document.filename.replace(/["\\\r\n]/g, "_").replace(/[^\x20-\x7e]/g, "_");
      const encodedFilename = encodeURIComponent(document.filename).replace(/[!'()*]/g, character => `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
      return reply.header("cache-control", "no-store").header("x-content-type-options", "nosniff")
        .header("content-disposition", `attachment; filename="${safeFilename}"; filename*=UTF-8''${encodedFilename}`).type(document.content_type).send(document.content);
    } catch (error) { return profileFailure(reply, error); }
  });
}

function profileFailure(reply: FastifyReply, error: unknown): FastifyReply {
  if (error instanceof ProfileError && [400, 404, 409, 422].includes(error.status)) return reply.code(error.status).send({ detail: error.code });
  return reply.code(503).send({ detail: "verification_unavailable" });
}

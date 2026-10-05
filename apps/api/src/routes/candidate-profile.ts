/** Account-owned candidate source data, separate from trusted verification and matching projections. */
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { CandidateProfileMutation } from "@comit/shared";
import type { AuthOptions } from "./auth.js";
import { originAllowed, resolveSession, type SessionIdentity } from "../auth/session.js";
import { ProfileStore } from "../profiles/store.js";
import { candidateProfileMutationSchema, candidateProfileViewSchema } from "../profiles/schemas.js";

/** Shared auth and database boundaries are injected; the browser cannot choose a profile owner. */
export interface CandidateProfileOptions extends AuthOptions { profiles: ProfileStore }

/** Persist incomplete or complete declarations with CAS and a durable matching handoff. */
export async function candidateProfileRoutes(app: FastifyInstance, options: CandidateProfileOptions): Promise<void> {
  const identities = new WeakMap<FastifyRequest, SessionIdentity>();
  const guard = async (request: FastifyRequest, reply: FastifyReply) => {
    try {
      const identity = await resolveSession(request, "candidate", options.config, options.store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      if (request.method !== "GET" && !originAllowed(request, options.config, identity.transport === "bearer")) {
        return reply.code(403).send({ detail: "origin_forbidden" });
      }
      identities.set(request, identity);
    } catch { return reply.code(503).send({ detail: "auth_unavailable" }); }
  };
  const read = async (request: FastifyRequest, reply: FastifyReply) => {
    const identity = identities.get(request);
    if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
    try {
      const profile = await options.profiles.get(identity.accountId);
      return profile ? reply.header("cache-control", "no-store").send(profile)
        : reply.code(404).send({ detail: "profile_not_found" });
    }
    catch (error) { return failure(reply, error); }
  };
  app.get<{ Reply: unknown }>("/candidate/profile", {
    schema: { tags: ["candidate-profile"], security: [{ bearerAuth: [] }, { candidateSession: [] }],
      response: { 200: candidateProfileViewSchema } }, onRequest: guard,
  }, read);
  for (const method of ["PUT", "PATCH"] as const) {
    app.route<{ Body: CandidateProfileMutation; Reply: unknown }>({ method, url: "/candidate/profile",
      schema: { tags: ["candidate-profile"], security: [{ bearerAuth: [] }, { candidateSession: [] }],
        body: candidateProfileMutationSchema, response: { 200: candidateProfileViewSchema } }, onRequest: guard,
      handler: async (request, reply) => {
        const identity = identities.get(request);
        if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
        try {
          return reply.header("cache-control", "no-store").send(
            await options.profiles.save(identity.accountId, request.body, method === "PUT"));
        } catch (error) { return failure(reply, error); }
      },
    });
  }
  app.post<{ Reply: unknown }>("/candidate/profile/projection/retry", {
    schema: { tags: ["candidate-profile"], security: [{ bearerAuth: [] }, { candidateSession: [] }],
      body: { type: "object", additionalProperties: false, properties: {} }, response: { 200: candidateProfileViewSchema } },
    onRequest: guard,
  }, async (request, reply) => {
    const identity = identities.get(request);
    if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
    try { return reply.header("cache-control", "no-store").send(await options.profiles.retry(identity.accountId)); }
    catch (error) { return failure(reply, error); }
  });
}

function failure(reply: FastifyReply, error: unknown): FastifyReply {
  if (typeof error === "object" && error !== null && "status" in error && "code" in error
    && typeof error.status === "number" && [400, 404, 409, 422].includes(error.status)
    && typeof error.code === "string" && /^[a-z_]+$/.test(error.code)) {
    return reply.code(error.status).send({ detail: error.code });
  }
  return reply.code(503).send({ detail: "profile_unavailable" });
}

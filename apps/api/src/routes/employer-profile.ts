/** Authenticated employer company settings, shared by onboarding and invitations. */
import type { FastifyInstance, FastifyReply, FastifyRequest, FastifySchema } from "fastify";
import type { EmployerProfileMutation } from "@comit/shared";
import type { AuthOptions } from "./auth.js";
import { resolveSession, originAllowed, type SessionIdentity } from "../auth/session.js";
import { EmployerProfileError, type EmployerProfileStore } from "../employers/store.js";
import { employerProfileSchema, employerProfileMutationSchema } from "../employers/schemas.js";
/** Reuse the revocable role session and inject the company store. */
export interface EmployerProfileOptions extends AuthOptions { employerProfiles: EmployerProfileStore }
/** Register GET/PATCH with server-derived ownership and cookie origin checks. */
export async function employerProfileRoutes(app: FastifyInstance, options: EmployerProfileOptions): Promise<void> {
  const identities = new WeakMap<FastifyRequest, SessionIdentity>();
  const guard = async (request: FastifyRequest, reply: FastifyReply) => {
    reply.header("cache-control", "no-store");
    try {
      const identity = await resolveSession(request, "employer", options.config, options.store);
      if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
      if (request.method !== "GET" && !originAllowed(request, options.config, identity.transport === "bearer"))
        return reply.code(403).send({ detail: "origin_forbidden" });
      identities.set(request, identity);
    } catch { return reply.code(503).send({ detail: "auth_unavailable" }); }
  };
  const perform = async (request: FastifyRequest, reply: FastifyReply, input?: EmployerProfileMutation) => {
    const identity = identities.get(request);
    if (!identity) return reply.code(401).send({ detail: "unauthenticated" });
    try { return reply.send(await (input ? options.employerProfiles.update(identity.accountId, input) : options.employerProfiles.get(identity.accountId))); }
    catch (error) {
      return error instanceof EmployerProfileError ? reply.code(error.status).send({ detail: error.code })
        : reply.code(503).send({ detail: "employer_profile_unavailable" });
    }
  };
  const schema: FastifySchema = { tags: ["employer-profile"], security: [{ bearerAuth: [] }, { employerSession: [] }], response: { 200: employerProfileSchema } };
  app.get("/employer/profile", { schema, onRequest: guard }, (request, reply) => perform(request, reply));
  app.patch<{ Body: EmployerProfileMutation }>("/employer/profile", { schema: { ...schema, body: employerProfileMutationSchema }, onRequest: guard },
    (request, reply) => perform(request, reply, request.body));
}

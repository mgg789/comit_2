/** Configure the auth domain while leaving internal/offline services usable when secrets are absent. */
import type { FastifyInstance } from "fastify";
import { Pool } from "pg";
import { loadAuthConfig } from "./config.js";
import { readSmtpConfig } from "./mail.js";
import { AuthStore } from "./store.js";
import { authRoutes } from "../routes/auth.js";
import { accountRoutes } from "../routes/accounts.js";
import { candidateProfileRoutes } from "../routes/candidate-profile.js";
import { ProfileStore } from "../profiles/store.js";
import { RecruitmentStore } from "../recruitment/store.js";
import { recruitmentRoutes } from "../routes/recruitment.js";
import { chatConfiguration, chatRoutes } from "../routes/chat.js";
import { correlatedFetch } from "../operations/index.js";
import { EmployerStatisticsStore } from "../statistics/store.js";
import { statisticsRoutes } from "../routes/statistics.js";

/** Wire production dependencies once; unavailable config never creates a demo session or leaks a code. */
export async function authDomain(app: FastifyInstance): Promise<void> {
  let config;
  try {
    config = loadAuthConfig();
    if (!process.env.DATABASE_URL) throw new Error("auth_database_missing");
  } catch {
    for (const role of ["candidate", "employer"]) {
      for (const path of ["code", "verify", "logout", "logout-all"]) {
        app.post(`/auth/${role}/${path}`, { schema: { tags: ["auth"] } }, async (_request, reply) =>
          reply.code(503).send({ detail: "auth_not_configured" }));
      }
      app.get(`/auth/${role}/me`, { schema: { tags: ["auth"] } }, async (_request, reply) =>
        reply.code(503).send({ detail: "auth_not_configured" }));
    }
    return;
  }
  const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 10,
    connectionTimeoutMillis: 5000, idleTimeoutMillis: 30000, statement_timeout: 10000 });
  pool.on("error", () => app.log.error({ code: "auth_database_unavailable" }, "Auth database connection failed"));
  app.addHook("onClose", () => pool.end());
  const store = new AuthStore(pool, config.secret, { accountConsentVersion: config.consentVersion });
  let mailConfigured = true;
  try { readSmtpConfig(); } catch { mailConfigured = false; }
  const options = { config, store, mailConfigured, profiles: new ProfileStore(pool), transport: correlatedFetch() };
  await app.register(authRoutes, options);
  await app.register(candidateProfileRoutes, options);
  await app.register(accountRoutes, options);
  const recruitment = new RecruitmentStore(pool, chatConfiguration() !== null, config.secret);
  await app.register(recruitmentRoutes, { ...options, recruitment });
  await app.register(chatRoutes, { ...options, recruitment });
  await app.register(statisticsRoutes, { ...options, statistics: new EmployerStatisticsStore(pool) });
}

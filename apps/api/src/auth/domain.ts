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
import { organizationAgentToolsRoutes } from "../routes/organization-agent-tools.js";
import { EmployerProfileStore } from "../employers/store.js";
import { employerProfileRoutes } from "../routes/employer-profile.js";
import { SystemNotificationsStore } from "../notifications/system-store.js";
import { notificationRoutes } from "../routes/notifications.js";
import { CandidateRecommendationsStore } from "../recommendations/store.js";
import { recommendationsRoutes } from "../recommendations/routes.js";
import { WeeklyRecommendationWorker } from "../recommendations/worker.js";
import { VacancyLifecycle } from "../recruitment/vacancy-lifecycle.js";

/** Wire production dependencies once; unavailable config never creates a demo session or leaks a code. */
export async function authDomain(app: FastifyInstance): Promise<void> {
  let config;
  try {
    config = loadAuthConfig();
    if (!process.env.DATABASE_URL) throw new Error("auth_database_missing");
  } catch {
    for (const kind of ["vacancies", "candidates"]) {
      app.get(`/public/showcase/${kind}`, { schema: { tags: ["public-showcase"], security: [],
        response: { 503: { type: "object", required: ["detail"], properties: { detail: { type: "string" } } } } } },
      async (_request, reply) => reply.header("cache-control", "no-store").code(503).send({ detail: "showcase_unavailable" }));
    }
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
  const vacancyLifecycle = new VacancyLifecycle(pool, correlatedFetch());
  const notifications = new SystemNotificationsStore(pool);
  const employerProfiles = new EmployerProfileStore(pool);
  const options = { config, store, employerProfiles, mailConfigured, profiles: new ProfileStore(pool), transport: correlatedFetch(), vacancyLifecycle };
  let resuming = false;
  const resume = async () => {
    if (resuming) return;
    resuming = true;
    try { await vacancyLifecycle.resume(); } catch { app.log.warn({ code: "vacancy_lifecycle_retry" }, "Vacancy lifecycle reconciliation deferred"); }
    finally { resuming = false; }
  };
  const interval = setInterval(() => { void resume(); }, 5000);
  interval.unref();
  app.addHook("onClose", async () => { clearInterval(interval); });
  await app.register(authRoutes, options);
  await app.register(candidateProfileRoutes, options);
  await app.register(accountRoutes, options);
  const recruitment = new RecruitmentStore(pool, chatConfiguration() !== null, config.secret,
    (owner, draftId, client) => vacancyLifecycle.writable(owner, draftId, client),
    { initialWorkingDays: 4, reminderWorkingDays: 3, hireOn: "offer_accept" }, notifications);
  await app.register(employerProfileRoutes, { ...options, employerProfiles });
  await app.register(notificationRoutes, { ...options, notifications });
  const recommendations = new CandidateRecommendationsStore(pool, { notifyInTransaction: async (client, candidateId, item) => {
    await notifications.append(client, candidateId, `recommendation:${item.recommendation_id}`, "recommendation.weekly",
      item.state === "ready" ? "Новые рекомендации от платформы готовы. Посмотрите их в статистике." : "Обновлены рекомендации от платформы. Посмотрите их в статистике.",
      { kind: "candidate_profile", path: "/statistics" });
  } });
  await app.register(recommendationsRoutes, { ...options, recommendations });
  const recommendationWorker = new WeeklyRecommendationWorker(recommendations, {
    matchingUrl: process.env.MATCHING_ENGINE_URL ?? "http://matching-engine:8101", matchingToken: process.env.CANDIDATE_PIPELINE_TOKEN ?? "",
    agentsUrl: process.env.AGENTS_SERVICE_URL ?? "http://agents-service:8104", agentsToken: process.env.AGENTS_SERVICE_TOKEN ?? "", transport: correlatedFetch() });
  let sweeping = false, recommending = false;
  const sweep = async () => {
    if (sweeping) return; sweeping = true;
    try { await recruitment.processDeadlines(); } catch { app.log.warn({ code: "recruitment_deadlines_retry" }, "Hiring clock reconciliation deferred"); }
    finally { sweeping = false; }
  };
  const recommend = async () => {
    if (recommending) return; recommending = true;
    try { await recommendations.enqueueCurrentWeek(); await recommendationWorker.processOnce(); }
    catch { app.log.warn({ code: "recommendations_retry" }, "Weekly recommendation reconciliation deferred"); }
    finally { recommending = false; }
  };
  const hiringInterval = setInterval(() => { void sweep(); }, 5000); hiringInterval.unref();
  const recommendationInterval = setInterval(() => { void recommend(); }, 60_000); recommendationInterval.unref();
  app.addHook("onClose", async () => { clearInterval(hiringInterval); clearInterval(recommendationInterval); });
  await app.register(recruitmentRoutes, { ...options, recruitment });
  await app.register(chatRoutes, { ...options, recruitment });
  const statistics = new EmployerStatisticsStore(pool);
  await app.register(statisticsRoutes, { ...options, statistics });
  await app.register(organizationAgentToolsRoutes, { ...options, pool, recruitment, statistics });
}

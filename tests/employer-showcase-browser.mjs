/** Deterministic browser acceptance: synthetic fixtures exist only inside intercepted test requests.
 * Run: PLAYWRIGHT_MODULE=/absolute/path/playwright/index.mjs node tests/employer-showcase-browser.mjs
 * Requires the employer Vite server; never invokes real auth, chat, providers, or write APIs.
 */
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";

const playwright = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base =
  process.env.EMPLOYER_BROWSER_URL || "http://127.0.0.1:5174/employer/";
const output =
  process.env.EMPLOYER_SHOWCASE_SCREENSHOTS || "/tmp/comit-employer-showcase-browser";
await mkdir(output, { recursive: true });
const now = "2026-10-07T10:00:00.000Z";
const ids = {
  owner: "10000000-0000-4000-8000-000000000001",
  candidate: "10000000-0000-4000-8000-000000000002",
  draft: "10000000-0000-4000-8000-000000000003",
  vacancy: "10000000-0000-4000-8000-000000000004",
  process: "10000000-0000-4000-8000-000000000005",
};
/** @type {import('../packages/shared/src/auth.ts').AccountView} */
const account = {
  id: ids.owner,
  email: "synthetic-employer@example.test",
  role: "employer",
  email_verified: true,
  created_at: now,
  consent: { processing: true, version: "account-v1", accepted_at: now },
};
/** @type {import('../packages/shared/src/agents.ts').AgentsDraftView} */
const draft = {
  draft_id: ids.draft,
  vacancy_id: ids.vacancy,
  owner_id: ids.owner,
  revision: 1,
  state: "created",
  business_status: "open",
  archived: false,
  fields: {
    title: "Backend-разработчик",
    zp_min: 180000,
    zp_max: 240000,
    daily: "Полный день",
    city: "Москва",
    type: "remote",
    bio: "Синтетическая вакансия для браузерной проверки",
    class: "backend",
    domens: {},
    stacks: { Python: 4 },
    soft_skills: {},
  },
  missing_requirements: [],
  publication: {
    status: "accepted",
    source_revision: 1,
    matching_status: "ready",
  },
};
/** @type {import('../packages/shared/src/recruitment.ts').RecruitmentView} */
const recruitment = {
  id: ids.process,
  owner_id: ids.owner,
  candidate_id: ids.candidate,
  vacancy_id: ids.vacancy,
  draft_id: ids.draft,
  profile_revision: 1,
  revision: 1,
  state: "sent",
  terms: {
    company_name: "Synthetic Test",
    description: "Приглашаем обсудить вакансию",
    salary: { from_rub: 180000, to_rub: 240000 },
    contact_method: "platform",
    vacancy_title: draft.fields.title,
    city: "Москва",
    daily: "Полный день",
    work_type: "remote",
  },
  invited_at: now,
  closure: null,
  interviews: [],
  offers: [],
  allowed_actions: ["withdraw"],
  chat_available: true,
  chat_writable: true,
  created_at: now,
  updated_at: now,
};
/** @type {import('../packages/shared/src/showcase.ts').PublicCandidateDetail} */
const candidate = {
  candidate_id: ids.candidate,
  profile_revision: 1,
  display_name: "Кандидат 01",
  city: "Москва",
  salary: { from_rub: 180000, to_rub: 240000 },
  category: {
    specialization: "backend",
    grade: "middle",
    grade_status: "approved",
  },
  rank: 0.9,
  confirmed_strength: 0.8,
  resume_url: "/api/synthetic-resume.pdf",
  search_factors: { lexical_rank: 1, semantic_rank: null, rrf_score: 0.01 },
  public_profile: {
    projection_version: 1,
    display_name: "Кандидат 01",
    city: "Москва",
    salary: { from_rub: 180000, to_rub: 240000 },
    description: "Синтетический профиль для проверки интерфейса",
    work_types: ["remote"],
    relocation_ready: false,
    relocation_cities: [],
    preferences: [],
    domains: [],
    stacks: [],
    soft_skills: [],
    projects: [],
    experience: [],
    achievements: [],
  },
};
/** @type {import('../packages/shared/src/matching.ts').FeedCard} */
const feedCard = {
  candidate_id: candidate.candidate_id,
  profile_revision: 1,
  position: 1,
  display_name: candidate.display_name,
  city: candidate.city,
  salary: candidate.salary,
  category: candidate.category,
  rank: candidate.rank,
  confirmed_strength: candidate.confirmed_strength,
  resume_url: candidate.resume_url,
  filter_factors: ["Соответствует синтетическим обязательным фильтрам"],
  explanation: {
    rating_score: 0.8,
    strict_score: 0.9,
    semantic_cosine: null,
    semantic_unavailable_reason: "synthetic_fixture",
    rating_contribution: 0.4,
    strict_contribution: 0.45,
    semantic_contribution: 0,
    score: 0.85,
    top_matched_features: [
      {
        index: 0,
        group: "stack",
        key: "python",
        target_level: 4,
        candidate_level: 5,
        status: "confirmed",
        agreement: 1,
        confidence: 1,
        contribution: 0.45,
      },
    ],
  },
  main_position: 1,
  rerank_position: null,
  mode: "main_fallback",
  kind: "recommended",
  rerank_score: null,
  rerank_contributions: {},
  artifact_hash: null,
  exposure_id: "synthetic-exposure",
  session_id: "synthetic-feed",
  served_at: now,
  decision: null,
  history_badges: [],
  change_assessment: null,
};
const counts = {
  delivered: 8,
  viewed: 7,
  liked: 4,
  disliked: 3,
  invited: 3,
  accepted: 2,
  interviewed: 1,
  offered: 1,
  hired: 1,
};
const stats = {
  version: "employer-statistics-v1",
  generated_at: now,
  timezone: "UTC",
  period: {
    from: "2026-09-08",
    to: "2026-10-07",
    previous_from: "2026-08-09",
    previous_to: "2026-09-07",
    vacancy_id: null,
  },
  inventory: {
    draft: 0,
    open: 1,
    closed: 0,
    archived: 0,
    saved_pairs: 4,
    active_processes: 3,
  },
  current: counts,
  previous: Object.fromEntries(Object.keys(counts).map((key) => [key, 0])),
  unique_candidates: 8,
  daily: [{ date: "2026-10-07", counts }],
  top_vacancies: [
    {
      vacancy_id: ids.vacancy,
      draft_id: ids.draft,
      title: draft.fields.title,
      status: "open",
      events: 8,
      candidates: 8,
    },
  ],
};
const captures = [];

const errors = [], writes = [], searches = [];
let forceFailure = false;
let openDrafts = [draft];
const browser = await playwright.chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await context.newPage();
page.on("pageerror", error => errors.push(error.message));
await context.route("**/api/**", async route => {
  const request = route.request(), url = new URL(request.url()), path = url.pathname.slice(4);
  const body = request.method() === "GET" ? null : request.postDataJSON();
  const reply = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
  if (path === "/auth/employer/me") return reply(account);
  if (path === "/employer/vacancy-drafts") return reply(openDrafts);
  if (path === "/employer/vacancies") return reply({ items: [draft], has_more: false, limit: 100, next_cursor: null });
  if (path === "/employer/candidates/search") { searches.push(body); return reply({ items: [candidate], total: 1, limit: 30, has_more: false, next_cursor: null, mode: "browse", fallback_reason: null }); }
  if (path.endsWith("/showcase-likes")) {
    writes.push(body);
    if (!body.mismatch_token) return reply({ detail: { code: "strict_filter_confirmation_required", candidate_id: candidate.candidate_id, profile_revision: candidate.profile_revision, vacancy_revision: 1, warnings: ["city"], mismatch_token: "synthetic-signed-token" } }, 409);
    if (forceFailure) return reply({ detail: "temporary_unavailable" }, 503);
    return reply({ candidate_id: candidate.candidate_id, profile_revision: candidate.profile_revision, vacancy_revision: 1, added: true });
  }
  if (path === "/employer/candidates/" + candidate.candidate_id) return reply(candidate);
  if (path.endsWith("/recruitment") || path === "/employer/recruitment") return reply({ items: [], limit: 100, offset: 0, has_more: false, next_cursor: null });
  if (path.endsWith("/capabilities")) return reply({ manual_editing: true, agent: false });
  if (path === "/employer/statistics") return reply(stats);
  return reply({ detail: "synthetic_not_found" }, 404);
});
try {
  await page.goto(base + "#/showcase");
  const plus = page.getByRole("button", { name: /Добавить в вакансию:/ });
  await plus.waitFor();
  await plus.click();
  const modal = page.getByRole("dialog", { name: "Выберите вакансию" });
  await modal.waitFor();
  assert.equal(writes.length, 0, "single vacancy must still require explicit target selection");
  await modal.getByRole("button", { name: draft.fields.title, exact: true }).click();
  await modal.waitFor({ state: "hidden" });
  assert.equal(writes.length, 2, "force add must negotiate signed mismatch once");
  assert.equal(writes[0].idempotency_key, writes[1].idempotency_key);
  assert.equal(writes[1].mismatch_token, "synthetic-signed-token");
  assert.equal(await page.getByRole("button", { name: "Вакансия", exact: true }).innerText(), "Вакансия", "picker target must not change the showcase filter");
  await page.getByRole("status").filter({ hasText: "Кандидат добавлен" }).waitFor();
  const search = page.getByRole("textbox", { name: "Поиск по имени, роли или стеку" });
  await search.fill("persistent backend");
  await page.getByRole("button", { name: "Найти", exact: true }).click();
  await page.getByRole("textbox", { name: "Стек", exact: true }).fill("python");
  await page.getByRole("button", { name: "Грейд", exact: true }).click();
  await page.getByRole("option", { name: "Middle", exact: true }).click();
  await page.evaluate(() => location.hash = "/vacancies");
  await page.getByRole("textbox", { name: "Поиск по вакансиям" }).fill("Backend");
  await page.reload();
  assert.equal(await page.getByRole("textbox", { name: "Поиск по вакансиям" }).inputValue(), "Backend");
  await page.evaluate(() => location.hash = "/showcase");
  await plus.waitFor();
  assert.equal(await search.inputValue(), "persistent backend");
  assert.equal(await page.getByRole("textbox", { name: "Стек", exact: true }).inputValue(), "python");
  await page.reload();
  await plus.waitFor();
  assert.equal(await search.inputValue(), "persistent backend");
  assert.ok(searches.some(body => body.q === "persistent backend" && body.filters.grades?.[0] === "middle"));
  const row = page.locator(".discovery-row").first();
  assert.equal(await row.evaluate(el => el.style.transform), "", "motion must not own hover transform");
  const transition = await row.evaluate(el => getComputedStyle(el).transitionDuration);
  assert.ok(transition.split(", ").every(value => parseFloat(value) > 0));
  await row.hover(); await page.waitForTimeout(80);
  const midway = await row.evaluate(el => new DOMMatrixReadOnly(getComputedStyle(el).transform).m42);
  await page.waitForTimeout(400);
  const final = await row.evaluate(el => new DOMMatrixReadOnly(getComputedStyle(el).transform).m42);
  assert.ok(Math.abs(midway) < Math.abs(final), "hover must animate through intermediate values");
  await page.mouse.move(0, 0); await page.waitForTimeout(400);
  await page.emulateMedia({ reducedMotion: "reduce" });
  const reducedDuration = await row.evaluate(el => getComputedStyle(el).transitionDuration);
  assert.ok(reducedDuration.split(", ").every(value => parseFloat(value) > 0.01), "bounded row feedback must retain duration under reduced motion");
  await row.hover(); await page.waitForTimeout(80);
  const reducedMid = await row.evaluate(el => new DOMMatrixReadOnly(getComputedStyle(el).transform).m42);
  await page.waitForTimeout(400);
  const reducedEnd = await row.evaluate(el => new DOMMatrixReadOnly(getComputedStyle(el).transform).m42);
  assert.ok(Math.abs(reducedMid) < Math.abs(reducedEnd));
  await page.emulateMedia({ reducedMotion: "no-preference" });
  // Failure and empty states remain actionable in the independent target picker.
  forceFailure = true;
  await plus.click(); await modal.waitFor();
  await modal.getByRole("button", { name: draft.fields.title, exact: true }).click();
  await modal.getByRole("alert").waitFor();
  assert.ok(await modal.isVisible());
  await modal.getByRole("button", { name: "Отмена", exact: true }).click();
  openDrafts = [];
  await page.reload(); await plus.waitFor(); await plus.click(); await modal.waitFor();
  await modal.getByText("Сначала опубликуйте вакансию, чтобы добавлять кандидатов.", { exact: true }).waitFor();
  await modal.getByRole("button", { name: "Отмена", exact: true }).click();
  // Unsaved creation fields restore after closing the modal and after reload.
  await page.evaluate(() => location.hash = "/vacancies");
  await page.getByRole("button", { name: "Добавить вакансию", exact: true }).first().click();
  await page.getByRole("dialog").waitFor();
  const title = page.getByRole("textbox", { name: "Название должности", exact: true });
  await title.fill("Restored draft title");
  await page.keyboard.press("Escape");
  await page.reload();
  await page.getByRole("button", { name: "Добавить вакансию", exact: true }).first().click();
  assert.equal(await title.inputValue(), "Restored draft title");
  assert.deepEqual(errors, []);
  console.log("PASS: independent single-vacancy target modal, signed force-add retry, errors/empty state, persistent filters and unsaved title, animated row hover");
} catch (error) { console.error("Browser errors", errors); console.error((await page.locator("body").innerText()).slice(0, 1800)); throw error; } finally { await browser.close(); }

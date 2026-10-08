/** Swipe acceptance with synthetic API interception; no live business calls. */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
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

const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
let savedCount = 1;
let failNext = false;
const decisions = [];
const cards = Array.from({ length: 8 }, (_, i) => ({ ...feedCard, exposure_id: `exposure-${i}`, display_name: `Карточка ${i}` }));
await context.route("**/api/**", async route => {
  const path = new URL(route.request().url()).pathname.slice(4);
  const reply = (data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
  if (path === "/auth/employer/me") return reply(account);
  if (path === "/employer/vacancies") return reply({ items: [draft], next_cursor: null });
  if (path === "/employer/vacancy-drafts") return reply([draft]);
  if (path === `/employer/vacancies/${ids.draft}`) return reply(draft);
  if (path === `/employer/candidates/${ids.candidate}`) return reply(candidate);
  if (path.endsWith("/recruitment")) return reply({ items: [], has_more: false, limit: 100, offset: 0 });
  if (path.endsWith("/ready")) return reply({ vacancy_id: ids.vacancy, status: "open" });
  if (path.endsWith("/presence")) return reply({ status: "active" });
  if (path.endsWith("/saved")) return reply({ items: Array.from({ length: savedCount }, (_, i) => ({
    vacancy_id: ids.vacancy, candidate_id: `saved-${i}`, exposure_id: `saved-${i}`, profile_revision: 1, saved_at: now,
    card: { ...feedCard, candidate_id: `saved-${i}` },
  })), limit: 100, offset: 0, has_more: false });
  if (path.endsWith("/feed")) return reply({ session_id: "synthetic-feed", items: cards, has_more: false, next_cursor: null });
  if (path.endsWith("/feed/impressions")) return reply({ recorded_at: now });
  if (path.endsWith("/feed/decisions")) {
    const data = route.request().postDataJSON(); decisions.push(data);
    if (failNext) { failNext = false; return reply({ detail: "synthetic_unavailable" }, 503); }
    if (data.action === "like") savedCount = 20;
    return reply({ ...data, saved: data.action === "like" });
  }
  return reply({ detail: "synthetic_unavailable" }, 503);
});
const output = "/tmp/comit-swipe-regression";
await mkdir(output, { recursive: true });
const card = page.locator(".candidate-swipe");
const waitCard = async n => { await card.getByRole("heading", { name: `Карточка ${n}`, exact: true }).waitFor(); };
const settle = async () => page.waitForFunction(() => {
  const m = new DOMMatrix(getComputedStyle(document.querySelector('.candidate-swipe')).transform);
  return Math.abs(m.m41) < 1 && Math.abs(m.m42) < 1 && Math.abs(m.m12) < .005;
});
const columnWidth = () => page.locator('.detail-stack').evaluate(node => node.getBoundingClientRect().width);
try {
  await page.goto((process.env.EMPLOYER_BROWSER_URL || "http://127.0.0.1:5174/employer/") + `#/vacancy/${ids.draft}`);
  await waitCard(0); await settle();
  const width = await columnWidth();
  assert.deepEqual(await page.locator('.candidate-controls button').evaluateAll(nodes => nodes.map(n => n.getAttribute('aria-label'))),
    ["Пропустить кандидата", "Открыть резюме", "Сохранить кандидата"]);
  await page.locator('.saved-resume').first().click();
  await page.getByRole('heading', { name: 'К вакансии', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await waitCard(0); await settle();
  await page.getByRole('button', { name: 'Открыть резюме', exact: true }).click();
  await page.getByRole('heading', { name: 'К вакансии', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await waitCard(0); await settle();
  let box = await card.boundingBox();
  const start = { x: box.x + box.width / 2, y: box.y + 80 };
  await page.mouse.move(start.x, start.y); await page.mouse.down();
  await page.mouse.move(start.x + 130, start.y + 120, { steps: 12 });
  const moved = await card.boundingBox();
  assert.ok(moved.y > box.y + 90 && moved.x > box.x + 90, "free drag must move both axes");
  // Release to the right of the feed column center.
  assert.ok(start.x + 130 > box.x + box.width / 2);
  await page.mouse.up();
  await waitCard(1); await settle();
  assert.equal(decisions[0].action, 'like');
  assert.ok(await card.evaluate(node => node.classList.contains('deck-tone-1')));
  assert.ok(Math.abs(await columnWidth() - width) < 1, "20 likes must not grow the column");
  await page.waitForFunction(() => document.querySelectorAll('.saved-row .saved-card').length === 20);
  assert.ok(await page.locator('.saved-row').evaluate(node => node.scrollWidth > node.clientWidth));
  await page.getByRole('button', { name: 'Пропустить кандидата', exact: true }).click();
  await waitCard(2); await settle();
  assert.equal(decisions[1].action, 'dislike');
  assert.ok(await card.evaluate(node => node.classList.contains('deck-tone-2')));
  failNext = true;
  await page.getByRole('button', { name: 'Сохранить кандидата', exact: true }).click();
  await page.getByRole('status').filter({ hasText: 'Не удалось сохранить действие' }).waitFor();
  await settle(); await waitCard(2);
  const retryKey = decisions.at(-1).idempotency_key;
  await page.getByRole('button', { name: 'Сохранить кандидата', exact: true }).click();
  await waitCard(3); await settle();
  assert.equal(decisions.at(-1).idempotency_key, retryKey);
  await card.scrollIntoViewIfNeeded();
  box = await card.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + 80); await page.mouse.down();
  await page.mouse.move(850, box.y + 160, { steps: 15 }); await page.mouse.up();
  await waitCard(4); await settle();
  assert.equal(decisions.at(-1).action, 'dislike');
  for (const n of [5, 6]) {
    await page.getByRole('button', { name: 'Пропустить кандидата', exact: true }).click();
    await waitCard(n); await settle();
  }
  for (const w of [1920, 1024, 390]) {
    await page.setViewportSize({ width: w, height: 1080 });
    await card.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: `${output}/swipe-${w}.png` });
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: 'passed', checks: ['free XY drag', 'drop-half selection', 'three card colors', 'button order and directions', 'resume return', 'fixed liked-column', 'API failure and idempotent retry', '1920/1024/390'], screenshots: output }));
} finally { await browser.close(); }

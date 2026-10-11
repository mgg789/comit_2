/** Deterministic browser acceptance: synthetic fixtures exist only inside intercepted test requests.
 * Run: PLAYWRIGHT_MODULE=/absolute/path/playwright/index.mjs node tests/employer-browser.mjs
 * Requires the employer Vite server; never invokes real auth, chat, providers, or write APIs.
 */
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";

const playwright = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base =
  process.env.EMPLOYER_BROWSER_URL || "http://127.0.0.1:5174/employer/";
const output =
  process.env.EMPLOYER_SCREENSHOTS || "/tmp/comit-employer-browser";
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
const requests = [];
const feedDecisions = [];
const chatSends = [];
const agentSends = [];
const runtimeErrors = [];
const chatOnly = process.env.EMPLOYER_CHAT_ONLY === "1";
let authenticated = chatOnly;
let codeUnavailable = true;
let chatFailOnce = true;
let chatReads = 0;
let contactsReads = 0;
let organizationReads = 0;
let processState = structuredClone(recruitment);
let durableMessages = [];
let organizationMessages = [];
let turnPolls = 0;
const browser = await playwright.chromium.launch({
  channel: process.env.PLAYWRIGHT_CHANNEL || "chrome",
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  reducedMotion: "reduce",
});
const page = await context.newPage();
page.on("pageerror", (error) => runtimeErrors.push(error.message));
/** Each API request is intercepted locally, including unknown endpoints; no live mutations escape. */
await context.route("**/api/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname.slice(4);
  const method = request.method();
  requests.push({ path, method });
  const body = method === "GET" ? undefined : request.postDataJSON();
  const reply = (json, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(json),
    });
  if (path === "/auth/employer/me")
    return reply(
      authenticated ? account : { detail: "unauthorized" },
      authenticated ? 200 : 401,
    );
  if (path === "/auth/employer/code")
    return codeUnavailable
      ? reply({ detail: "provider_unavailable" }, 503)
      : reply({
          challenge_id: "synthetic-otp-challenge",
          expires_at: "2099-01-01T00:00:00Z",
          resend_after: "2099-01-01T00:00:00Z",
        });
  if (path === "/auth/employer/verify") {
    assert.equal(body.consent.processing, true);
    assert.equal(body.consent.version, "account-v1");
    assert.equal(body.code, "123456");
    authenticated = true;
    return reply({
      account,
      access_token: "synthetic-test-token-never-used",
      token_type: "Bearer",
      expires_at: "2099-01-01T00:00:00Z",
    });
  }
  if (path === "/auth/employer/logout") {
    authenticated = false;
    return reply({ ok: true });
  }
  if (path === "/employer/vacancy-drafts") return reply([draft]);
  if (path === "/employer/vacancies")
    return reply({
      items: [draft],
      limit: 50,
      has_more: false,
      next_cursor: null,
    });
  if (path === `/employer/vacancies/${ids.draft}`) return reply(draft);
  if (path.endsWith("/capabilities"))
    return reply({
      manual_editing: true,
      agent: false,
      enrichment: false,
      fallback: "manual_editing",
      disabled_code: "agent_disabled_manual_available",
    });
  if (path === "/employer/candidates/search")
    return reply({
      items: [candidate],
      total: 1,
      limit: 50,
      has_more: false,
      next_cursor: null,
      mode: "browse",
      fallback_reason: null,
    });
  if (path === `/employer/candidates/${ids.candidate}`) return reply(candidate);
  if (path === "/employer/statistics") return reply(stats);
  if (
    path === "/employer/recruitment" ||
    (path.endsWith("/recruitment") && method === "GET")
  )
    return reply({
      items: [processState],
      limit: 100,
      offset: 0,
      has_more: false,
      next_cursor: null,
    });
  if (path === `/employer/recruitment/${ids.process}`)
    return reply(processState);
  if (path.endsWith("/contacts")) {
    contactsReads++;
    assert.ok(
      ["accepted", "offer_sent", "hired", "offer_declined"].includes(
        processState.state,
      ),
      "contacts fetched before acceptance",
    );
    return reply({
      candidate_id: ids.candidate,
      display_name: "Synthetic Accepted Candidate",
      email: "synthetic-candidate@example.test",
      phone: null,
      profile_revision: 1,
    });
  }
  if (path.endsWith("/chat/messages") && method === "GET") {
    chatReads++;
    const cursor = Number(url.searchParams.get("after_sequence") || 0);
    return reply({
      messages: durableMessages.filter((item) => item.sequence > cursor),
      next_sequence: durableMessages.at(-1)?.sequence || cursor,
    });
  }
  if (path.endsWith("/chat/messages") && method === "POST") {
    chatSends.push(body);
    if (chatFailOnce) {
      chatFailOnce = false;
      return reply({ detail: "chat_unavailable" }, 503);
    }
    const message = {
      id: "20000000-0000-4000-8000-000000000001",
      sequence: 1,
      sender_id: ids.owner,
      created_at: now,
      text: body.text,
      files: body.files,
    };
    durableMessages = [message];
    return reply(message);
  }
  if (path === "/employer/organization-agent/chat") {
    organizationReads++;
    return reply({
      chat_id: "synthetic-global-chat",
      owner_id: ids.owner,
      status: "active",
      presence: true,
    });
  }
  if (path === "/employer/organization-agent/presence")
    return reply({
      chat_id: "synthetic-global-chat",
      owner_id: ids.owner,
      status: "active",
      presence: Boolean(body.present),
    });
  if (path === "/employer/organization-agent/budget")
    return reply({
      limit_micro_usd: "1000000",
      spent_micro_usd: "200000",
      reserved_micro_usd: "0",
      available_micro_usd: "800000",
      window_days: 7,
      pricing_configured: true,
    });
  if (path === "/employer/organization-agent/pending-answers") return reply([]);
  if (path === "/employer/organization-agent/messages" && method === "GET") {
    const cursor = Number(url.searchParams.get("after_sequence") || 0);
    return reply({
      items: organizationMessages.filter((item) => item.sequence > cursor),
      next_sequence: organizationMessages.at(-1)?.sequence || cursor,
    });
  }
  if (path === "/employer/organization-agent/messages" && method === "POST") {
    agentSends.push(body);
    organizationMessages = [
      {
        message_id: "synthetic-user-message",
        chat_id: "synthetic-global-chat",
        role: "user",
        content: body.message,
        sequence: 1,
        created_at: now,
      },
      {
        message_id: "synthetic-tool-message",
        chat_id: "synthetic-global-chat",
        role: "tool",
        content: "PRIVATE_TOOL_FIXTURE",
        sequence: 2,
        created_at: now,
      },
    ];
    return reply({
      turn_id: "synthetic-turn",
      chat_id: "synthetic-global-chat",
      owner_id: ids.owner,
      sequence: 1,
      user_message: body.message,
      status: "queued",
      idempotency_key: body.idempotency_key,
    });
  }
  if (path === "/employer/organization-agent/turns/synthetic-turn") {
    turnPolls++;
    if (turnPolls >= 2)
      organizationMessages.push({
        message_id: "synthetic-ai-message",
        chat_id: "synthetic-global-chat",
        role: "assistant",
        content: "Синтетический ответ агента",
        sequence: 3,
        created_at: now,
      });
    return reply({
      turn_id: "synthetic-turn",
      chat_id: "synthetic-global-chat",
      owner_id: ids.owner,
      sequence: 1,
      user_message: agentSends[0].message,
      status: turnPolls >= 2 ? "succeeded" : "processing",
      idempotency_key: agentSends[0].idempotency_key,
    });
  }
  if (path.endsWith("/saved"))
    return reply({
      items: [
        {
          vacancy_id: ids.vacancy,
          candidate_id: ids.candidate,
          profile_revision: 1,
          exposure_id: feedCard.exposure_id,
          saved_at: now,
          card: feedCard,
        },
      ],
      limit: 100,
      offset: 0,
      has_more: false,
      next_cursor: null,
    });
  if (path.endsWith("/feed"))
    return reply({
      session_id: feedCard.session_id,
      vacancy_id: ids.vacancy,
      vacancy_revision: 1,
      policy_hash: "synthetic-policy",
      expires_at: "2099-01-01T00:00:00Z",
      cursor: null,
      next_cursor: null,
      has_more: false,
      order_revision: 1,
      items: [feedCard],
    });
  if (path.endsWith("/feed/impressions"))
    return reply({
      event_id: "synthetic-impression",
      exposure_id: body.exposure_id,
      recorded_at: now,
      replayed: false,
    });
  if (path.endsWith("/feed/decisions")) {
    feedDecisions.push(body);
    return reply({
      receipt_id: "synthetic-receipt",
      exposure_id: body.exposure_id,
      vacancy_id: ids.vacancy,
      candidate_id: ids.candidate,
      action: body.action,
      idempotency_key: body.idempotency_key,
      saved: body.action === "like",
      visited: body.action === "dislike",
      replayed: false,
      history_badges: [],
    });
  }
  if (path.endsWith("/selection"))
    return reply({ detail: "selection_not_ready" }, 409);
  if (path.endsWith("/ready"))
    return reply({
      vacancy_id: ids.vacancy,
      revision: 1,
      source_revision: 1,
      owner_id: ids.owner,
      status: "open",
      zp_min: 180000,
      zp_max: 240000,
      daily: "Полный день",
      city: "Москва",
      type: "remote",
      bio: draft.fields.bio,
      domens: {},
      stacks: {},
      soft_skills: {},
      metadata: {},
      class: "backend",
      build_mode: "class_only",
      strict_vector: [],
      requirement_mask: [],
      strict_schema_version: 1,
      strict_schema_hash: "synthetic-schema",
      semantic_vector: null,
      semantic_model_version: null,
      semantic_space_id: null,
      semantic_available: false,
      published_at: now,
    });
  return reply({ detail: "synthetic_endpoint_not_available" }, 503);
});

/** Assert viewport containment and fully decoded source artwork after each capture. */
async function capture(name, width = 1920) {
  await page.setViewportSize({ width, height: width < 760 ? 844 : 1080 });
  await page.waitForTimeout(650);
  await page.waitForFunction(() =>
    [
      ...document.querySelectorAll(
        ".main-content > div,.main-content > div > div,.modal,.modal-shade,.organization-overlay,.login-card",
      ),
    ].every((element) => Number(getComputedStyle(element).opacity) >= 0.99),
  );
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all(
      [...document.images].map((image) => image.decode().catch(() => {})),
    );
  });
  await page.screenshot({
    path: `${output}/${name}-${width}.png`,
    fullPage: true,
  });
  const geometry = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    viewport: innerWidth,
    clientWidth: document.documentElement.clientWidth,
    rootFontSize: getComputedStyle(document.documentElement).fontSize,
    mobile: matchMedia("(max-width:760px)").matches,
    nav: (() => {
      const nav = document.querySelector(".nav");
      if (!nav) return null;
      const rect = nav.getBoundingClientRect();
      return {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        opacity: getComputedStyle(nav).opacity,
        visibility: getComputedStyle(nav).visibility,
      };
    })(),
    broken: [...document.images]
      .filter((image) => !image.complete || !image.naturalWidth)
      .map((image) => image.getAttribute("src")),
  }));
  captures.push({ name, width, ...geometry });
  await writeFile(`${output}/geometry.json`, JSON.stringify(captures, null, 2));
  assert.ok(
    geometry.scroll <= geometry.viewport + 1,
    `${name} ${width}: horizontal overflow ${geometry.scroll}`,
  );
  assert.deepEqual(geometry.broken, [], `${name} ${width}: broken images`);
}

try {
  await page.goto(base);
  if (!chatOnly) {
  await page.getByRole("textbox", { name: "Email", exact: true }).waitFor();
  await page
    .getByRole("textbox", { name: "Email", exact: true })
    .fill(account.email);
  await page.getByRole("button", { name: "Получить код", exact: true }).click();
  await page
    .getByRole("alert")
    .filter({ hasText: "Отметьте согласие" })
    .waitFor();
  assert.equal(
    await page.getByRole("checkbox").getAttribute("aria-invalid"),
    "true",
  );
  await page.getByRole("button", { name: "Войти через Яндекс" }).click();
  assert.equal(await page.getByRole("checkbox").isChecked(), false);
  const legalLink = page.getByRole("link", { name: "Я согласен(а)" });
  const [policy] = await Promise.all([
    page.waitForEvent("popup"),
    legalLink.click(),
  ]);
  await policy
    .getByRole("heading", {
      name: "Тестовое согласие на обработку персональных данных",
      exact: true,
    })
    .waitFor();
  await policy.getByRole("heading", { name: "Аккаунт — account-v1", exact: true }).waitFor();
  await policy.getByRole("link", { name: "политика конфиденциальности", exact: true }).click();
  await policy.getByRole("heading", {
    name: "Тестовая политика конфиденциальности Comit",
    exact: true,
  }).waitFor();
  await policy.close();
  assert.equal(await page.getByRole("checkbox").isChecked(), false);
  for (const width of [1920, 1024, 390]) {
    await page.setViewportSize({ width, height: 1080 });
    const geometry = await page.evaluate(() => {
      const input = document
        .querySelector(".login-email input")
        .getBoundingClientRect();
      const yandex = document.querySelector(".yandex").getBoundingClientRect();
      const box = document
        .querySelector(".login-consent input")
        .getBoundingClientRect();
      const text = document
        .querySelector(".login-consent a")
        .getBoundingClientRect();
      return {
        input: input.height,
        yandex: yandex.height,
        // The consent text wraps; the checkbox is centred on its first line.
        alignment: Math.abs(box.y + box.height / 2 - text.y - parseFloat(getComputedStyle(document.querySelector(".login-consent a")).lineHeight) / 2),
      };
    });
    assert.ok(
      Math.abs(geometry.input - geometry.yandex) < 1,
      `login control heights at ${width}`,
    );
    assert.ok(geometry.alignment < 1, `consent centered at ${width}`);
  }
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Войти через Яндекс" }).click();
  await page
    .getByRole("alert")
    .filter({ hasText: "Вход через Яндекс пока не подключён" })
    .waitFor();
  await page.getByRole("button", { name: "Получить код", exact: true }).click();
  await page
    .getByRole("alert")
    .filter({ hasText: "Сервис временно недоступен" })
    .waitFor();
  for (const width of [1920, 1024, 390])
    await capture("login-provider-error", width);
  codeUnavailable = false;
  await page.getByRole("button", { name: "Получить код", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Код подтверждения из письма, 6 цифр", exact: true })
    .fill("123456");
  await page.getByRole("button", { name: "Продолжить", exact: true }).click();
  await page
    .getByRole("navigation", { name: "Навигация работодателя" })
    .waitFor();
  await page.getByText(draft.fields.title, { exact: true }).first().waitFor();
  for (const width of [1920, 1024, 390]) await capture("vacancies", width);
  await page.setViewportSize({ width: 1920, height: 1080 });
  const create = page
    .getByRole("button", { name: /Добавить вакансию|Создать|Новая вакансия/ })
    .first();
  await create.click();
  await page.getByRole("dialog").waitFor();
  for (const width of [1920, 1024, 390]) await capture("new-vacancy", width);
  await page.keyboard.press("Escape");
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "Соискатели", exact: true }).focus();
  await page.keyboard.press("Enter");
  await page.waitForURL(/#\/showcase/);
  await page
    .getByText(candidate.display_name, { exact: true })
    .first()
    .waitFor();
  for (const width of [1920, 1024, 390]) await capture("showcase", width);
  await page.evaluate((id) => {
    location.hash = `/resume/${id}`;
  }, ids.candidate);
  await page
    .getByText(candidate.public_profile.description, { exact: true })
    .waitFor();
  for (const width of [1920, 1024, 390]) await capture("resume", width);
  await page.evaluate((id) => {
    location.hash = `/vacancy/${id}`;
  }, ids.draft);
  await page
    .getByRole("heading", { name: "Данные вакансии", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Сохранить кандидата", exact: true })
    .waitFor();
  for (const width of [1920, 1024, 390]) await capture("vacancy-detail", width);
  await page.setViewportSize({ width: 1920, height: 1080 });
  const card = page.locator(".candidate-swipe");
  const bounds = await card.boundingBox();
  assert.ok(bounds);
  await page.mouse.move(
    bounds.x + bounds.width / 2,
    bounds.y + bounds.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    bounds.x + bounds.width / 2 - 200,
    bounds.y + bounds.height / 2,
    { steps: 12 },
  );
  await page.mouse.up();
  await page
    .getByText("Не можем найти подходящих кандидатов", { exact: true })
    .waitFor();
  assert.equal(feedDecisions.length, 1);
  assert.equal(feedDecisions[0].action, "dislike");
  await page.evaluate((id) => {
    location.hash = `/vacancy/${id}/resumes`;
  }, ids.draft);
  await page
    .getByRole("heading", { name: "Резюме по вакансии", exact: true })
    .waitFor();
  for (const width of [1920, 1024, 390])
    await capture("vacancy-resumes", width);
  }
  await page.getByRole("button", { name: "Переписка и приглашения", exact: true }).click();
  await page.getByRole("heading", { name: "Чаты", exact: true }).waitFor();
  assert.equal(await page.locator(".recruitment-conversation").count(), 0);
  assert.equal(chatReads, 0, "chat directory must not fetch private history");
  assert.match(await page.locator(".recruitment-chat-tile").first().innerText(), /Comit/);
  await page.locator(".recruitment-chat-tile").nth(1).click();
  await page.getByRole("textbox", { name: "Сообщение кандидату" }).waitFor();
  assert.equal(await page.locator(".chat-picker").count(), 0);
  assert.ok(page.url().includes("from=%2Fchat"));
  await page.reload();
  await page.getByRole("textbox", { name: "Сообщение кандидату" }).waitFor();
  await page.getByRole("button", { name: "Вернуться", exact: true }).click();
  await page.getByRole("heading", { name: "Чаты", exact: true }).waitFor();
  await page.evaluate(id => { location.hash = `/vacancy/${id}?filter=liked&search=react`; }, ids.draft);
  await page.getByRole("button", { name: "Открыть чат", exact: true }).first().waitFor();
  await page.getByRole("button", { name: "Открыть чат", exact: true }).first().click();
  await page.getByRole("textbox", { name: "Сообщение кандидату" }).waitFor();
  await page.reload();
  await page.getByRole("textbox", { name: "Сообщение кандидату" }).waitFor();
  await page.getByRole("button", { name: "Вернуться", exact: true }).click();
  assert.ok(page.url().endsWith(`/vacancy/${ids.draft}?filter=liked&search=react`));
  await page.getByRole("button", { name: "Открыть чат", exact: true }).first().click();
  await page.getByRole("textbox", { name: "Сообщение кандидату" }).waitFor();
  assert.equal(
    contactsReads,
    0,
    "contact identity must remain hidden until acceptance",
  );
  await page
    .getByRole("textbox", { name: "Сообщение кандидату" })
    .fill("Синтетическое сообщение кандидату");
  await page
    .getByRole("button", { name: "Отправить сообщение", exact: true })
    .click();
  await page
    .getByRole("alert")
    .filter({ hasText: "Сервис временно недоступен" })
    .waitFor();
  await page
    .getByRole("button", { name: "Отправить сообщение", exact: true })
    .click();
  await page
    .getByText("Синтетическое сообщение кандидату", { exact: true })
    .waitFor();
  assert.equal(chatSends.length, 2);
  assert.deepEqual(
    chatSends[0],
    chatSends[1],
    "retry must reuse UUID and identical payload",
  );
  assert.match(
    chatSends[0].idempotency_key,
    /^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i,
  );
  for (const width of [1920, 1024, 390]) {
    await capture("chat", width);
    assert.equal(await page.evaluate(() => {
      const send = document.querySelector(".recruitment-thread .send-button")?.getBoundingClientRect();
      const launcher = document.querySelector(".agent-launcher")?.getBoundingClientRect();
      return !!send && !!launcher && send.left < launcher.right && send.right > launcher.left && send.top < launcher.bottom && send.bottom > launcher.top;
    }), false, "floating AI launcher must not cover the chat send button");
  }
  processState = {
    ...processState,
    state: "accepted",
    revision: 2,
    allowed_actions: ["interview_propose", "offer_send", "reject"],
  };
  await page
    .getByText("Synthetic Accepted Candidate", { exact: true })
    .waitFor({ timeout: 15000 });
  assert.ok(
    contactsReads > 0,
    "accepted process should resolve authorized contact details",
  );
  if (!chatOnly) {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page
    .getByRole("button", { name: "Открыть AI-агента", exact: true })
    .click();
  await page.getByRole("dialog", { name: "AI-агент", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Чем могу помочь?" }).waitFor();
  const organizationReadsAtOpen = organizationReads;
  await page
    .getByRole("textbox", { name: "Сообщение AI-агенту" })
    .fill("Синтетический запрос агенту");
  await page
    .getByRole("button", { name: "Отправить сообщение агенту" })
    .click();
  await page.evaluate(() => {
    location.hash = "/vacancies";
  });
  assert.equal(
    await page.getByRole("dialog", { name: "AI-агент", exact: true }).count(),
    1,
    "agent overlay must survive navigation",
  );
  await page
    .getByText("Синтетический ответ агента", { exact: true })
    .waitFor({ timeout: 15000 });
  assert.equal(
    organizationReads,
    organizationReadsAtOpen,
    "navigation must not remount the global conversation (StrictMode may initially mount effects twice)",
  );
  assert.equal(
    await page.getByText("PRIVATE_TOOL_FIXTURE").count(),
    0,
    "tool messages must remain hidden",
  );
  assert.equal(agentSends.length, 1);
  assert.ok(turnPolls >= 2);
  for (const width of [1920, 1024, 390]) await capture("ai-overlay", width);
  await page.keyboard.press("Escape");
  await page
    .getByRole("dialog", { name: "AI-агент", exact: true })
    .waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "Статистика", exact: true }).click();
  await page.waitForURL(/#\/statistics/);
  await page
    .getByRole("heading", { name: "Статистика", exact: true })
    .waitFor();
  for (const width of [1920, 1024, 390]) await capture("statistics", width);
  await page.getByRole("button", { name: "Открыть меню аккаунта" }).click();
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await page
    .getByRole("dialog", { name: "Выйти из аккаунта?", exact: true })
    .getByRole("button", { name: "Выйти", exact: true })
    .click();
  await page.getByRole("textbox", { name: "Email", exact: true }).waitFor();
  assert.equal(authenticated, false);
  }
  assert.deepEqual(runtimeErrors, [], "unhandled browser exceptions");
  console.log(
    JSON.stringify(
      {
        result: "passed",
        screenshots: output,
        checks: chatOnly ? ["separate tiles without history", "chat return to list and vacancy with filters after reload", "no conversation picker", "identical UUID retry", "contacts acceptance gate", "source artwork decodes", "1920/1024/390 no overflow"] : [
          "OTP explicit consent and provider503",
          "keyboard navigation",
          "vacancy creation modal",
          "vacancy/resume/shortlist screens and actual drag reaction",
          "no horizontal overflow at1920/1024/390",
          "source artwork decodes",
          "QGramm identical UUID retry",
          "contacts acceptance gate",
          "persistent AI overlay",
          "queued turn polling and hidden tool messages",
          "logout",
        ],
        intercepted_requests: requests.length,
        viewport_diagnostics: captures.filter(
          (item) => item.name === "vacancies",
        ),
      },
      null,
      2,
    ),
  );
} catch (error) {
  // A hung capture must not hide the original failure.
  await page.screenshot({ path: `${output}/failure.png`, fullPage: true, timeout: 5000 }).catch(() => {});
  console.error(
    JSON.stringify(
      {
        runtimeErrors,
        url: page.url(),
        recentRequests: requests.slice(-12),
        body: (await page.locator("body").innerText()).slice(-1200),
      },
      null,
      2,
    ),
  );
  throw error;
} finally {
  await browser.close();
}

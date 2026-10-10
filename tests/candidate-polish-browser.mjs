/** Candidate screen acceptance uses synthetic owner-scoped intercepted API responses only. */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, reducedMotion: process.env.CANDIDATE_REDUCED_MOTION === "system" ? "reduce" : "no-preference" });
const base = process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/";
const output = process.env.CANDIDATE_POLISH_SCREENSHOTS || "/tmp/comit-candidate-polish";
await mkdir(output, { recursive: true });
const owner = "10000000-0000-4000-8000-000000000002", id = "10000000-0000-4000-8000-000000000003";
const now = new Date().toISOString();
const account = { id: owner, email: "candidate-browser@example.test", role: "candidate", email_verified: true, created_at: now, consent: { processing: true, version: "account-v1", accepted_at: now } };
let profile = { candidate_id: owner, revision: 1, fields: { display_name: "Тестовый кандидат", city: "Москва", primary_direction: "backend", declared_grade: "middle", description: "Синтетическое описание кандидата", salary: { from_rub: 180000, to_rub: 240000 }, work_types: ["remote"], fsp_id: "synthetic-fsp", fsp_claim_id: "synthetic-claim", stacks: [{ key: "python", level: 4 }], projects: [{ id: "10000000-0000-4000-8000-000000000006", title: "Синтетический проект", description: "Пример", facts: { role: "developer", duration_months: 12 } }] }, evidence: { skill: { status: "confirmed", input_hash: "synthetic", verified_value: 4, provenance: ["test"], checked_at: now } }, consents: { processing: true, pool: true, training: true, version: "profile-v1", accepted_at: now, training_version: "training-v1", training_accepted_at: now }, projection: { state: "delivered", source_revision: 1, projected_revision: 1, attempts: 1, error_code: null }, updated_at: now };
let recruitment = { id, owner_id: "10000000-0000-4000-8000-000000000001", candidate_id: owner, vacancy_id: "10000000-0000-4000-8000-000000000004", draft_id: "10000000-0000-4000-8000-000000000005", profile_revision: 1, revision: 1, state: "sent", terms: { company_name: "Тестовая компания", vacancy_title: "Backend-разработчик", description: "Описание реального по контракту приглашения из теста", salary: { from_rub: 200000, to_rub: 260000 }, contact_method: "platform", city: "Москва", daily: "Полный день", work_type: "remote" }, invited_at: now, closure: null, interviews: [{ id: "10000000-0000-4000-8000-000000000007", kind: "technical", state: "proposed", starts_at: now, time_zone: "Europe/Moscow", duration_minutes: 30, format: "remote", location: "Видеосвязь", completed_at: null, outcome: null, feedback: null }], offers: [], allowed_actions: ["view", "accept", "decline", "interview_accept", "interview_decline"], chat_available: true, chat_writable: true, created_at: now, updated_at: now };
const second = { ...recruitment, id: "10000000-0000-4000-8000-000000000008", state: "accepted", terms: { ...recruitment.terms, company_name: "Другая компания" }, allowed_actions: [] };
recruitment = { ...recruitment, chat_unread_count: 2 };
let mutations = [], readReceipts = 0;
const completeFields = structuredClone(profile.fields);
await context.route("**/api/**", async route => {
  const request = route.request(), url = new URL(request.url()), path = url.pathname;
  const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  if (path === "/api/auth/candidate/me") return reply(account);
  if (path === "/api/candidate/profile") {
    if (request.method() === "GET") return reply(profile);
    const body = request.postDataJSON(); mutations.push(body);
    assert.equal(body.expected_revision, profile.revision);
    assert.deepEqual(body.fields, {});
    assert.equal(body.consent.processing, true);
    profile = { ...profile, revision: profile.revision + 1, consents: { ...profile.consents, ...body.consent }, projection: { ...profile.projection, source_revision: profile.revision + 1 } };
    return reply(profile);
  }
  if (path === "/api/candidate/pipeline") return reply({ state: "ready", profile_revision: profile.projection.projected_revision, missing_requirements: [] });
  if (path === "/api/candidate/recruitment") return reply({ items: [recruitment, second], limit: 100, offset: 0, has_more: false });
  if (path === `/api/candidate/recruitment/${id}`) return reply({ ...recruitment, allowed_actions: [], interviews: [] });
  if (path === `/api/candidate/recruitment/${id}/chat/read`) { readReceipts++; assert.equal(request.postDataJSON().through_sequence, 1); recruitment = { ...recruitment, chat_unread_count: 0 }; return reply({ read_sequence: 1, unread_count: 0 }); }
  if (path === `/api/candidate/recruitment/${id}/chat/messages`) return reply({ messages: [{ id: "test-incoming", sequence: 1, sender_id: recruitment.owner_id, text: "Новое сообщение", files: [], created_at: now }], next_sequence: 1 });
  return reply({ detail: "synthetic_unavailable" }, 503);
});
const page = await context.newPage(), errors = [];
page.on("pageerror", error => errors.push(error.message));
try {
  for (const width of [1920, 1024, 390]) {
    await page.setViewportSize({ width, height: 1080 });
    await page.goto(base + `#/offer/${id}`);
    const terms = page.locator(".candidate-offer-terms"), chat = page.getByRole("button", { name: "Перейти в чат", exact: true });
    await chat.waitFor(); await page.waitForTimeout(350);
    const geometry = await page.evaluate(() => {
      const content = document.querySelector(".candidate-offer-detail").getBoundingClientRect();
      const terms = document.querySelector(".candidate-offer-terms").getBoundingClientRect();
      const button = document.querySelector(".candidate-offer-chat-link .action").getBoundingClientRect();
      return { content: { left: content.left, right: content.right }, terms: { left: terms.left, right: terms.right, bottom: terms.bottom }, button: { left: button.left, right: button.right, top: button.top, height: button.height }, root: Number.parseFloat(getComputedStyle(document.documentElement).fontSize) };
    });
    assert.ok(Math.abs(geometry.terms.left - geometry.content.left) < 1);
    assert.ok(Math.abs(geometry.terms.right - geometry.content.right) < 1);
    assert.ok(geometry.button.top > geometry.terms.bottom);
    assert.ok(geometry.button.height <= Math.max(70 * geometry.root, 70));
    assert.ok(Math.abs((geometry.button.left + geometry.button.right) / 2 - (geometry.content.left + geometry.content.right) / 2) < 1);
    await page.screenshot({ path: `${output}/offer-detail-${width}.png`, fullPage: true });
    await page.goto(base + "#/offers"); await page.locator(".candidate-offer-card").first().waitFor();
    assert.equal(await page.locator(".candidate-company-initial").count(), 0);
    assert.equal(await page.locator(".candidate-offer-card .card-arrow").count(), 2);
    assert.equal(await page.locator(".candidate-offer-card .card-arrow .icon-vacancy-chevron").count(), 2);
    const filter = page.getByRole("button", { name: "Показать предложения", exact: true });
    await filter.click(); await page.getByRole("option", { name: "Принятые приглашения", exact: true }).click();
    await page.goto(base + "#/statistics"); await page.goto(base + "#/offers");
    assert.match(await filter.innerText(), /Принятые/);
    await page.reload(); await page.locator(".candidate-offer-card").first().waitFor(); assert.equal(await page.locator(".candidate-offer-card").count(), 1);
    await filter.click(); await page.getByRole("option", { name: "Все предложения", exact: true }).click();
    await page.goto(base + "#/chat"); await page.getByRole("heading", { name: "Чаты", exact: true }).waitFor();
    if (width === 1920) await page.getByText("2 новых сообщения", { exact: true }).waitFor();
    assert.equal(await page.locator(".nav-item.selected").getAttribute("aria-label"), "Предложения");
    await page.goto(base + `#/chat/${id}`); await page.locator(".participant-header").waitFor();
    assert.equal(await page.locator(".nav-item.selected").getAttribute("aria-label"), "Предложения");
    await page.getByText("Новое сообщение", { exact: true }).waitFor(); await page.waitForTimeout(250);
    assert.ok(readReceipts > 0);
    await page.goto(base + "#/chat"); await page.locator(".recruitment-chat-list").waitFor(); assert.equal(await page.getByText("2 новых сообщения", { exact: true }).count(), 0);
  }
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(base + "#/resume"); await page.getByRole("heading", { name: "Тестовый кандидат", exact: true }).waitFor();
  const chatIcon = page.locator('.account-tools .icon-message');
  await page.mouse.move(0, 0); await page.waitForTimeout(350);
  await page.getByRole("button", { name: "Чаты", exact: true }).hover(); await page.waitForTimeout(65);
  const midpoint = await chatIcon.evaluate(icon => new DOMMatrix(getComputedStyle(icon).transform).a);
  assert.ok(midpoint > 1 && midpoint < 1.06, `hover midpoint ${midpoint}`);
  await page.waitForTimeout(350); assert.ok(await chatIcon.evaluate(icon => new DOMMatrix(getComputedStyle(icon).transform).a) >= 1.059);
  await page.getByRole("button", { name: "Отозвать резюме", exact: true }).click();
  const confirmation = page.getByRole("dialog", { name: "Отозвать резюме из подбора?", exact: true });
  await confirmation.getByRole("button", { name: "Отмена", exact: true }).click(); assert.equal(mutations.length, 0);
  await page.getByRole("button", { name: "Отозвать резюме", exact: true }).click(); await confirmation.getByRole("button", { name: "Отозвать резюме", exact: true }).click();
  await page.getByRole("button", { name: "Опубликовать резюме", exact: true }).waitFor();
  assert.equal(mutations[0].consent.pool, false); assert.equal(mutations[0].consent.training, false); assert.deepEqual(profile.fields, completeFields);
  await page.reload(); await page.getByRole("button", { name: "Опубликовать резюме", exact: true }).click();
  await page.getByRole("button", { name: "Отозвать резюме", exact: true }).waitFor(); assert.equal(mutations[1].consent.pool, true); assert.equal(mutations[1].consent.training, false);
  await page.getByRole("button", { name: "Редактировать резюме", exact: true }).click();
  await page.getByLabel("Ваше имя", { exact: true }).fill("Сохранённый локально черновик");
  await page.getByLabel("Согласен(а) на обработку данных резюме", { exact: true }).uncheck();
  await page.goto(base + "#/offers"); await page.goto(base + "#/resume"); await page.getByRole("button", { name: "Редактировать резюме", exact: true }).click();
  assert.equal(await page.getByLabel("Ваше имя", { exact: true }).inputValue(), "Сохранённый локально черновик");
  assert.equal(await page.getByLabel("Согласен(а) на обработку данных резюме", { exact: true }).isChecked(), true);
  await page.reload(); await page.getByRole("button", { name: "Редактировать резюме", exact: true }).click();
  assert.equal(await page.getByLabel("Ваше имя", { exact: true }).inputValue(), "Сохранённый локально черновик");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: "passed", checks: ["fullwidth offer and centered normalheight chat button", "offer cards reuse employer arrow and omit company initials", "chat/list select offers tab and unread count clears after incoming message is read", "offer filters and unsaved editor inputs survive navigation and reload", "consents always restored from server rather than browser draft", "explicit revision-checked publication and confirmed withdrawal preserve resume/processing and never infer training"], screenshots: output }));
} finally { await browser.close(); }

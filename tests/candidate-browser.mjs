/** Candidate screen acceptance uses synthetic owner-scoped intercepted API responses only. */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, reducedMotion: process.env.CANDIDATE_REDUCED_MOTION === "system" ? "reduce" : "no-preference" });
const base = process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/";
const output = process.env.CANDIDATE_SCREENSHOTS || "/tmp/comit-candidate-browser";
await mkdir(output, { recursive: true });
const owner = "10000000-0000-4000-8000-000000000002", id = "10000000-0000-4000-8000-000000000003";
const now = new Date().toISOString();
const account = { id: owner, email: "candidate-browser@example.test", role: "candidate", email_verified: true, created_at: now, consent: { processing: true, version: "account-v1", accepted_at: now } };
let profile = { candidate_id: owner, revision: 1, fields: { display_name: "Тестовый кандидат", city: "Москва", primary_direction: "backend", declared_grade: "middle", description: "Синтетическое описание кандидата", salary: { from_rub: 180000, to_rub: 240000 }, work_types: ["remote"], fsp_id: "synthetic-fsp", fsp_claim_id: "synthetic-claim", stacks: [{ key: "python", level: 4 }], projects: [{ id: "10000000-0000-4000-8000-000000000006", title: "Синтетический проект", description: "Пример", facts: { role: "developer", duration_months: 12 } }] }, evidence: { skill: { status: "confirmed", input_hash: "synthetic", verified_value: 4, provenance: ["test"], checked_at: now } }, consents: { processing: true, pool: true, training: true, version: "profile-v1", accepted_at: now, training_version: "training-v1", training_accepted_at: now }, projection: { state: "delivered", source_revision: 1, projected_revision: 1, attempts: 1, error_code: null }, updated_at: now };
let recruitment = { id, owner_id: "10000000-0000-4000-8000-000000000001", candidate_id: owner, vacancy_id: "10000000-0000-4000-8000-000000000004", draft_id: "10000000-0000-4000-8000-000000000005", profile_revision: 1, revision: 1, state: "sent", terms: { company_name: "Тестовая компания", vacancy_title: "Backend-разработчик", description: "Описание реального по контракту приглашения из теста", salary: { from_rub: 200000, to_rub: 260000 }, contact_method: "platform", city: "Москва", daily: "Полный день", work_type: "remote" }, invited_at: now, closure: null, interviews: [{ id: "10000000-0000-4000-8000-000000000007", kind: "technical", state: "proposed", starts_at: now, time_zone: "Europe/Moscow", duration_minutes: 30, format: "remote", location: "Видеосвязь", completed_at: null, outcome: null, feedback: null }], offers: [], allowed_actions: ["view", "accept", "decline", "interview_accept", "interview_decline"], chat_available: true, chat_writable: true, created_at: now, updated_at: now };
const second = { ...recruitment, id: "10000000-0000-4000-8000-000000000008", state: "accepted", terms: { ...recruitment.terms, company_name: "Другая компания" }, allowed_actions: [] };
let saves = 0, viewCalls = 0, acceptCalls = 0, declineCalls = 0, chatCalls = 0, chatReads = 0, cursorReads = 0;
const acceptKeys = [], chatKeys = [];
let messages = [{ id: "message-1", sequence: 1, sender_id: recruitment.owner_id, created_at: now, text: "Добрый день", files: [] }];
await context.route("**/api/**", async route => {
  const request = route.request(), url = new URL(request.url()), path = url.pathname;
  const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  if (path === "/api/auth/candidate/me") return reply(account);
  if (path === "/api/candidate/profile") {
    if (request.method() === "GET") return reply(profile);
    const body = request.postDataJSON(); saves++;
    if (saves === 3) {
      assert.equal(body.expected_revision, 3); assert.deepEqual(body.fields, {});
      assert.deepEqual(body.consent, { processing: false, pool: false, version: "profile-v1", training: false });
      profile = { ...profile, revision: 4, consents: { ...profile.consents, ...body.consent, training_version: null, training_accepted_at: null } };
      return reply(profile);
    }
    assert.equal(body.fields.fsp_id, undefined); assert.equal(body.fields.fsp_claim_id, undefined); assert.equal(body.evidence, undefined);
    assert.deepEqual(body.fields.projects[0].facts, { role: "developer", duration_months: 12 });
    if (saves === 1) { assert.equal(body.expected_revision, 1); profile = { ...profile, revision: 2 }; return reply({ detail: "profile_revision_conflict" }, 409); }
    assert.equal(body.expected_revision, 2); assert.equal(body.consent, undefined);
    profile = { ...profile, revision: 3, fields: { ...profile.fields, ...body.fields }, consents: { ...profile.consents, ...body.consent } }; return reply(profile);
  }
  if (path === "/api/candidate/recruitment") {
    if (url.searchParams.get("cursor") === "next-page") { cursorReads++; return reply({ items: [second], limit: 100, offset: 0, has_more: false }); }
    return reply({ items: [recruitment], limit: 100, offset: 0, has_more: true, next_cursor: "next-page" });
  }
  if (path === `/api/candidate/recruitment/${id}`) return reply(recruitment);
  if (path === `/api/candidate/recruitment/${id}/actions`) {
    const body = request.postDataJSON(); assert.equal(body.expected_revision, recruitment.revision); assert.match(body.idempotency_key, /^[0-9a-f-]{36}$/);
    if (body.action === "view") { viewCalls++; recruitment = { ...recruitment, revision: 2, state: "viewed", allowed_actions: recruitment.allowed_actions.filter(value => value !== "view") }; }
    else if (body.action === "accept") { acceptCalls++; acceptKeys.push(body.idempotency_key); if (acceptCalls === 1) return reply({ detail: "provider_unavailable" }, 503); recruitment = { ...recruitment, revision: 3, state: "accepted", allowed_actions: ["interview_accept", "interview_decline"] }; }
    else if (body.action === "interview_decline") { declineCalls++; assert.equal(body.reason, "Буду свободен завтра"); recruitment = { ...recruitment, revision: 4, interviews: recruitment.interviews.map(value => ({ ...value, state: "declined" })), allowed_actions: [] }; }
    else assert.fail(`Unexpected action ${body.action}`);
    return reply(recruitment);
  }
  if (path === `/api/candidate/recruitment/${id}/chat/messages`) {
    if (request.method() === "GET") { chatReads++; return reply({ messages: messages.filter(message => message.sequence > Number(url.searchParams.get("after_sequence") || 0)), next_sequence: messages.at(-1)?.sequence || 0 }); }
    const body = request.postDataJSON(); chatCalls++; chatKeys.push(body.idempotency_key); if (chatCalls === 1) return reply({ detail: "chat_unavailable" }, 503);
    const message = { id: "message-2", sequence: 2, sender_id: owner, created_at: now, text: body.text, files: [] }; messages.push(message); return reply(message);
  }
  return reply({ detail: "synthetic_unavailable" }, 503);
});
const page = await context.newPage(), errors = [];
page.on("pageerror", error => errors.push(error.message));
try {
  await page.goto(base + "#/resume");
  await page.getByRole("heading", { name: "Тестовый кандидат", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Мини-задание", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Пройти задание", exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "Редактировать резюме", exact: false }).first().click();
  assert.equal(await page.getByLabel(/^ID участника ФСП/).getAttribute("readonly"), "");
  await page.getByLabel("Ваше имя", { exact: true }).fill("Обновлённый кандидат");
  await page.getByRole("button", { name: "Сохранить резюме", exact: true }).click();
  await page.getByRole("alert").waitFor(); assert.equal(saves, 1);
  await page.getByRole("button", { name: "Загрузить актуальное резюме", exact: true }).click();
  await page.getByRole("button", { name: "Редактировать резюме", exact: false }).first().click();
  await page.getByLabel("Ваше имя", { exact: true }).fill("Обновлённый кандидат");
  const processing = page.getByLabel("Согласен(а) на обработку данных резюме", { exact: true });
  const pool = page.getByLabel("Показывать резюме работодателям в подборе", { exact: true });
  const save = page.getByRole("button", { name: "Сохранить резюме", exact: true });
  assert.equal(await processing.isChecked(), true); assert.equal(await pool.isChecked(), true);
  await processing.uncheck(); assert.equal(await save.isDisabled(), true);
  await page.locator(".candidate-profile-editor form").evaluate(form => form.requestSubmit());
  await page.getByRole("alert").waitFor(); assert.equal(saves, 1);
  await processing.focus(); await page.keyboard.press("Space"); assert.equal(await processing.isChecked(), true);
  await pool.uncheck(); assert.equal(await save.isDisabled(), true);
  await pool.check(); assert.equal(await save.isDisabled(), false);
  const onsite = page.getByLabel("В офисе", { exact: true });
  await onsite.focus(); await page.keyboard.press("Space"); assert.equal(await onsite.isChecked(), true);
  assert.equal(await onsite.evaluate(input => getComputedStyle(input).appearance), "none");
  for (const width of [1920, 1024, 390]) {
    await page.setViewportSize({ width, height: 1080 });
    assert.equal(await processing.evaluate(input => getComputedStyle(input).appearance), "none");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.screenshot({ path: `${output}/resume-checkboxes-${width}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.getByRole("button", { name: "Сохранить резюме", exact: true }).click();
  await page.getByRole("heading", { name: "Обновлённый кандидат", exact: true }).waitFor(); assert.equal(saves, 2);
  await page.getByRole("button", { name: "Предложения", exact: true }).click();
  await page.getByRole("heading", { name: "Входящие предложения", exact: true }).waitFor();
  assert.equal(await page.locator(".candidate-offer-card").count(), 2); assert.ok(cursorReads >= 1);
  assert.match(await page.locator(".candidate-offer-card").first().innerText(), /200–260к ₽/);
  await page.getByRole("button", { name: "Открыть предложение Тестовая компания", exact: true }).click();
  await page.getByText("Приглашение просмотрено", { exact: true }).waitFor(); assert.equal(viewCalls, 1);
  assert.equal(await page.getByLabel("Сообщение работодателю", { exact: true }).count(), 0); assert.equal(chatReads, 0);
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await page.getByText("Приглашение просмотрено", { exact: true }).waitFor(); assert.equal(viewCalls, 1);
  await page.getByRole("button", { name: "Принять приглашение", exact: true }).click(); await page.getByRole("alert").first().waitFor();
  await page.getByRole("button", { name: "Принять приглашение", exact: true }).click(); await page.getByText("Приглашение принято", { exact: true }).waitFor(); assert.equal(acceptKeys[0], acceptKeys[1]);
  await page.getByRole("button", { name: "Отклонить время собеседования", exact: true }).click();
  await page.getByLabel("Почему время не подходит?", { exact: true }).fill("Буду свободен завтра");
  await page.getByRole("button", { name: "Отправить ответ", exact: true }).click();
  await page.getByRole("button", { name: "Отклонить время собеседования", exact: true }).waitFor({ state: "detached" }); assert.equal(declineCalls, 1);
  assert.equal(chatReads, 0);
  await page.getByRole("button", { name: "Перейти в чат", exact: true }).click();
  await page.getByText("Добрый день", { exact: true }).waitFor();
  assert.equal(await page.locator(".candidate-offer-terms").count(), 0);
  assert.equal(await page.locator(".recruitment-chat-list").count(), 0);
  assert.equal(decodeURIComponent(new URL(page.url()).hash.split("from=")[1]), `/offer/${id}`);
  await page.reload();
  await page.getByText("Добрый день", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Вернуться", exact: true }).click();
  await page.getByRole("button", { name: "Перейти в чат", exact: true }).waitFor();
  assert.equal(new URL(page.url()).hash, `#/offer/${id}`);
  const readsBeforeList = chatReads;
  await page.getByRole("button", { name: "Чаты", exact: true }).click();
  await page.getByRole("heading", { name: "Чаты", exact: true }).waitFor();
  await page.locator(".recruitment-chat-tile").first().waitFor();
  assert.equal(await page.locator(".recruitment-chat-tile").count(), 2);
  assert.equal(await page.getByLabel("Сообщение работодателю", { exact: true }).count(), 0);
  assert.equal(chatReads, readsBeforeList);
  await page.locator(".recruitment-chat-tile").filter({ hasText: "Тестовая компания" }).click();
  await page.getByText("Добрый день", { exact: true }).waitFor();
  await page.reload(); await page.getByText("Добрый день", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Вернуться", exact: true }).click();
  await page.getByRole("heading", { name: "Чаты", exact: true }).waitFor();
  assert.equal(new URL(page.url()).hash, "#/chat");
  await page.locator(".recruitment-chat-tile").filter({ hasText: "Тестовая компания" }).click();
  await page.getByText("Добрый день", { exact: true }).waitFor();
  recruitment = { ...recruitment, chat_writable: false };
  await page.getByText("Чат доступен только для чтения. Отправка сообщений и файлов сейчас недоступна.", { exact: true }).waitFor();
  assert.equal(await page.getByLabel("Сообщение работодателю", { exact: true }).isDisabled(), true);
  recruitment = { ...recruitment, chat_writable: true };
  await page.getByText("Чат доступен только для чтения. Отправка сообщений и файлов сейчас недоступна.", { exact: true }).waitFor({ state: "detached" });

  await page.getByLabel("Сообщение работодателю", { exact: true }).fill("Спасибо за приглашение");
  await page.getByRole("button", { name: "Отправить сообщение", exact: true }).click(); await page.getByRole("alert").last().waitFor();
  assert.equal(await page.getByLabel("Сообщение работодателю", { exact: true }).inputValue(), "Спасибо за приглашение");
  await page.getByRole("button", { name: "Отправить сообщение", exact: true }).click(); await page.getByText("Спасибо за приглашение", { exact: true }).waitFor(); assert.equal(chatKeys[0], chatKeys[1]);
  for (const width of [1920, 1024, 390]) {
    await page.setViewportSize({ width, height: 1080 });
    for (const route of ["/resume", "/offers", "/statistics", `/offer/${id}`, "/chat", `/chat/${id}?from=%2Fchat`]) {
      await page.goto(base + "#" + route); await page.locator(".candidate-content h1").first().waitFor();
      await page.waitForTimeout(320);
      if (route === "/offers") { const duration = await page.locator(".candidate-offer-card").first().evaluate(card => getComputedStyle(card).transitionDuration); assert.ok(duration.includes("0.3s"), `hover duration ${duration}`); }
      const geometry = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
      assert.ok(geometry.scroll <= geometry.width + 1, `${route} at ${width}: ${JSON.stringify(geometry)}`);
      await page.screenshot({ path: `${output}/${route.startsWith("/offer/") ? "offer-detail" : route.startsWith("/chat/") ? "conversation" : route.slice(1)}-${width}.png`, fullPage: true });
    }
  }
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto(base + "#/resume");
  await page.getByRole("button", { name: "Редактировать резюме", exact: true }).click();
  await page.getByLabel("Согласен(а) на обработку данных резюме", { exact: true }).uncheck();
  await page.getByLabel("Показывать резюме работодателям в подборе", { exact: true }).uncheck();
  assert.equal(await page.getByRole("button", { name: "Сохранить резюме", exact: true }).isDisabled(), true);
  await page.getByRole("button", { name: "Отозвать согласия резюме", exact: true }).click();
  const withdrawal = page.getByRole("dialog", { name: "Отозвать согласия резюме?", exact: true });
  await withdrawal.getByRole("button", { name: "Отмена", exact: true }).click();
  assert.equal(saves, 2);
  await page.getByLabel("Ваше имя", { exact: true }).fill("Эта правка не должна сохраниться");
  await page.getByRole("button", { name: "Отозвать согласия резюме", exact: true }).click();
  await page.screenshot({ path: `${output}/consent-withdrawal-confirmation.png`, fullPage: true });
  await withdrawal.getByRole("button", { name: "Отозвать согласия", exact: true }).click();
  await page.getByRole("heading", { name: "Обновлённый кандидат", exact: true }).waitFor(); assert.equal(saves, 3);
  await page.getByRole("button", { name: "Редактировать резюме", exact: true }).click();
  assert.equal(await page.getByLabel("Согласен(а) на обработку данных резюме", { exact: true }).isChecked(), false);
  assert.equal(await page.getByLabel("Показывать резюме работодателям в подборе", { exact: true }).isChecked(), false);
  assert.equal(await page.getByRole("button", { name: "Сохранить резюме", exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "Отозвать согласия резюме", exact: true }).count(), 0);
  profile = { ...profile, revision: 5, fields: {}, consents: { ...profile.consents, processing: true, pool: true } };
  await page.reload();
  await page.getByRole("button", { name: "Редактировать резюме", exact: true }).click();
  await page.getByRole("button", { name: "Сохранить резюме", exact: true }).click();
  await page.getByRole("alert").waitFor();
  assert.match(await page.getByRole("alert").innerText(), /имя, город, зарплатные ожидания, направление, категорию опыта/);
  assert.equal(saves, 3);
  await page.locator(".candidate-profile-editor form").evaluate(form => form.requestSubmit());
  assert.equal(saves, 3);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: "passed", checks: ["CAS conflict then explicit reload", "FSP/evidence never in mutations", "editor consent gate, persisted values and keyboard checkbox controls", "separate consent withdrawal confirmation, cancel and no unsaved field mutation", "missing matching fields show complete readable list before any request", "paginated invitations and mandatory salary", "view recorded once", "allowed actions with stable retry keys", "interview decline reason", "separate offer/list/conversation routes with no eager private history and return origin after reload", "process polling updates read-only permissions", "chat read/send stable retry", "1920/1024/390 no overflow"], screenshots: output }));
} finally { await browser.close(); }

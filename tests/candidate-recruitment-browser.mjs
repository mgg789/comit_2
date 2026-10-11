/** Candidate refusal, complaint draft and loaded weekly task use synthetic API responses. */
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const base = process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/";
const candidate = "10000000-0000-4000-8000-000000000002";
const vacancy = "10000000-0000-4000-8000-000000000004";
const invitedId = "10000000-0000-4000-8000-000000000003";
const acceptedId = "10000000-0000-4000-8000-000000000008";
const now = new Date().toISOString();
const interviewStartsAt = "2030-02-03T10:30:00.000Z";
const account = { id: candidate, email: "candidate@example.test", role: "candidate", email_verified: true, created_at: now, consent: { processing: true, version: "account-v1", accepted_at: now } };
const profile = { candidate_id: candidate, revision: 1, fields: { display_name: "Тестовый кандидат", city: "Москва", primary_direction: "backend", declared_grade: "middle", description: "Тест", work_types: ["remote"], salary: { from_rub: 100000, to_rub: 150000 } }, evidence: {}, consents: { processing: true, pool: true, training: false, version: "profile-v1", accepted_at: now, training_version: null, training_accepted_at: null }, projection: { state: "delivered", source_revision: 1, projected_revision: 1, attempts: 1, error_code: null }, updated_at: now };
const makeProcess = (id, state, allowed_actions) => ({ id, owner_id: "10000000-0000-4000-8000-000000000001", candidate_id: candidate, vacancy_id: vacancy, draft_id: "10000000-0000-4000-8000-000000000005", profile_revision: 1, revision: 1, state, terms: { company_name: "Тестовая компания", vacancy_title: "Backend-разработчик", description: "Тестовое описание вакансии", salary: { from_rub: 100000, to_rub: 150000 }, contact_method: "platform", city: "Москва", daily: "Полный день", work_type: "remote" }, invited_at: now, closure: null, interviews: [], offers: [], allowed_actions, chat_available: true, chat_writable: true, created_at: now, updated_at: now });
const acceptedProcess = { ...makeProcess(acceptedId, "accepted", ["withdraw"]), interviews: [{ id: "10000000-0000-4000-8000-000000000009", kind: "technical", state: "scheduled", starts_at: interviewStartsAt, time_zone: "Europe/Moscow", duration_minutes: 45, format: "remote", location: "Видеосвязь", completed_at: null, outcome: null, feedback: null }] };
const processes = new Map([[invitedId, makeProcess(invitedId, "sent", ["view", "accept", "decline"])], [acceptedId, acceptedProcess]]);
const actions = [];
let supportMessages = 0;
const weekly = { week: "2026-10-05", expires_at: "2026-10-12T00:00:00.000Z", status: "available", questions: [], correct: null, passed: null, completed_at: null };
await context.route("**/api/**", async route => {
  const request = route.request(), url = new URL(request.url()), path = url.pathname;
  const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  if (path === "/api/auth/candidate/me") return reply(account);
  if (path === "/api/candidate/profile") return reply(profile);
  if (path === "/api/candidate/pipeline") return reply({ state: "ready", profile_revision: 1, missing_requirements: [] });
  if (path === "/api/candidate/weekly-challenge") return reply(weekly);
  if (path === "/api/candidate/skills/catalog") return reply({ domains: [], stacks: [], skills: [] });
  if (path === "/api/candidate/recruitment") return reply({ items: [...processes.values()], limit: 100, offset: 0, has_more: false });
  const match = path.match(/^\/api\/candidate\/recruitment\/([0-9a-f-]+)$/u);
  if (match) return reply(processes.get(match[1]) || { detail: "not_found" }, processes.has(match[1]) ? 200 : 404);
  const actionMatch = path.match(/^\/api\/candidate\/recruitment\/([0-9a-f-]+)\/actions$/u);
  if (actionMatch) {
    const current = processes.get(actionMatch[1]);
    if (!current) return reply({ detail: "not_found" }, 404);
    const body = request.postDataJSON(); actions.push(body);
    assert.equal(body.expected_revision, current.revision);
    const state = body.action === "decline" ? "declined" : body.action === "withdraw" ? "withdrawn" : body.action === "accept" ? "accepted" : "viewed";
    const allowed_actions = body.action === "view" ? ["accept", "decline"] : [];
    const updated = { ...current, revision: current.revision + 1, state, allowed_actions, chat_writable: state === "declined" || state === "withdrawn" ? false : current.chat_writable, updated_at: now };
    processes.set(current.id, updated); return reply(updated);
  }
  if (path === "/api/candidate/system-chat" && request.method() === "GET") return reply({ items: [{id:"10000000-0000-4000-8000-000000000020",sequence:1,type:"invitation.sent",text:"Приглашение в компанию",created_at:now,files:[],target:{path:`/offer/${invitedId}`}}], next_sequence: 1, read_sequence: 0, has_more: false });
  if (path === "/api/candidate/system-chat/read") return reply({ read_sequence: request.postDataJSON().read_sequence, unread_count: 0 });
  if (path === "/api/candidate/system-chat/messages" && request.method() === "POST") { supportMessages++; return reply({ id: "support-1", sequence: supportMessages, type: "support.user_message", text: request.postDataJSON().text, created_at: now, files: [] }); }
  if (path.endsWith("/chat/messages") && request.method() === "GET") return reply({ messages: [], next_sequence: 0 });
  if (path.endsWith("/chat/read")) return reply({ read_sequence: 0, unread_count: 0 });
  return reply({ detail: "synthetic_unavailable" }, 503);
});
const page = await context.newPage(), errors = [];
page.on("pageerror", error => errors.push(error.message));
try {
  await page.goto(base + "#/resume");
  await page.getByRole("heading", { name: "Тестовый кандидат", exact: true }).waitFor();
  await page.getByRole("button", { name: "Пройти задание", exact: true }).waitFor();
  assert.equal(await page.getByText("Пока ничего нет", { exact: true }).count(), 0);

  await page.goto(base + `#/offer/${invitedId}`);
  const inviteRefusal = page.getByRole("button", { name: "Отказаться от вакансии", exact: true });
  await inviteRefusal.waitFor();
  await page.getByRole("button", { name: "Пожаловаться на вакансию", exact: true }).click();
  const supportDraft = page.getByLabel("Сообщение в поддержку", { exact: true });
  await supportDraft.waitFor();
  assert.equal(await supportDraft.inputValue(), `У меня жалоба по вакансии ${vacancy}: `);
  assert.equal(supportMessages, 0, "opening support must not send the draft");
  const openButton = page.getByRole("button", {name:"Открыть",exact:true});
  assert.equal(await openButton.count(), 1);
  assert.ok(await openButton.evaluate(el => getComputedStyle(el).backgroundImage !== "none"));
  await page.locator(".system-inbox .recruitment-messages").evaluate(el => { el.scrollTop = el.scrollHeight; });
  const lastMessage = await page.locator(".system-inbox .message-bubble").last().boundingBox();
  const composer = await page.locator(".communication-composer-wrap").boundingBox();
  assert.ok(lastMessage.y + lastMessage.height < composer.y, "last message must clear the fixed composer");

  await page.goto(base + `#/offer/${invitedId}`);
  await inviteRefusal.waitFor();
  await inviteRefusal.click();
  await page.getByRole("dialog", { name: "Отклонить приглашение?", exact: true }).getByRole("button", { name: "Подтвердить отказ", exact: true }).click();
  assert.equal(actions.at(-1)?.action, "decline");

  await page.goto(base + `#/chat/${acceptedId}`);
  await page.locator('summary[aria-label="Действия с вакансией"]').click();
  const interviewLabel = `${new Intl.DateTimeFormat("ru-RU", { dateStyle: "long", timeStyle: "short", timeZone: "Europe/Moscow" }).format(new Date(interviewStartsAt))} · Europe/Moscow`;
  await page.getByText(interviewLabel, { exact: false }).waitFor();
  assert.ok((await page.locator(".candidate-process-menu-interview").innerText()).includes("Видеосвязь"));
  await page.getByRole("button", { name: "Отказаться от вакансии", exact: true }).click();
  await page.getByRole("dialog", { name: "Отказаться от вакансии?", exact: true }).getByRole("button", { name: "Подтвердить отказ", exact: true }).click();
  assert.equal(actions.at(-1)?.action, "withdraw");
  assert.equal(processes.get(acceptedId)?.state, "withdrawn");
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}

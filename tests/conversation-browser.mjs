/** Shared recruitment UI regression: only synthetic candidate-role requests are intercepted. */
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, acceptDownloads: true });
const base = process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/";
const owner = "10000000-0000-4000-8000-000000000002", id = "10000000-0000-4000-8000-000000000003";
const now = new Date().toISOString();
const account = { id: owner, email: "candidate-browser@example.test", role: "candidate", email_verified: true, created_at: now, consent: { processing: true, version: "account-v1", accepted_at: now } };
const profile = { candidate_id: owner, revision: 1, fields: { display_name: "Тестовый кандидат", city: "Москва", primary_direction: "backend", declared_grade: "middle", description: "Синтетическое описание кандидата", salary: { from_rub: 180000, to_rub: 240000 }, work_types: ["remote"], fsp_id: "synthetic-fsp", fsp_claim_id: "synthetic-claim", stacks: [{ key: "python", level: 4 }], projects: [{ id: "10000000-0000-4000-8000-000000000006", title: "Синтетический проект", description: "Пример", facts: { role: "developer", duration_months: 12 } }] }, evidence: { skill: { status: "confirmed", input_hash: "synthetic", verified_value: 4, provenance: ["test"], checked_at: now } }, consents: { processing: true, pool: true, training: true, version: "profile-v1", accepted_at: now, training_version: "training-v1", training_accepted_at: now }, projection: { state: "delivered", source_revision: 1, projected_revision: 1, attempts: 1, error_code: null }, updated_at: now };
const recruitment = { id, owner_id: "10000000-0000-4000-8000-000000000001", candidate_id: owner, vacancy_id: "10000000-0000-4000-8000-000000000004", draft_id: "10000000-0000-4000-8000-000000000005", profile_revision: 1, revision: 1, state: "sent", terms: { company_name: "Тестовая компания", vacancy_title: "Backend-разработчик", description: "Описание реального по контракту приглашения из теста", salary: { from_rub: 200000, to_rub: 260000 }, contact_method: "platform", city: "Москва", daily: "Полный день", work_type: "remote" }, invited_at: now, closure: null, interviews: [{ id: "10000000-0000-4000-8000-000000000007", kind: "technical", state: "proposed", starts_at: now, time_zone: "Europe/Moscow", duration_minutes: 30, format: "remote", location: "Видеосвязь", completed_at: null, outcome: null, feedback: null }], offers: [], allowed_actions: ["view", "accept", "decline", "interview_accept", "interview_decline"], chat_available: true, chat_writable: true, created_at: now, updated_at: now };
const second = { ...recruitment, id: "10000000-0000-4000-8000-000000000008", state: "accepted", terms: { ...recruitment.terms, company_name: "Другая компания" }, allowed_actions: [] };
let writable = true, available = true, sends = [], uploads = 0, reads = 0, downloads = 0;
const attachment = { id: "file-synthetic", name: "note.txt", media_type: "text/plain", size: 4, sha256: "synthetic" };
const messages = Array.from({ length: 101 }, (_, index) => ({ id: `incoming-${index}`, sequence: index + 1, sender_id: recruitment.owner_id, created_at: now, text: index === 100 ? "Входящее сообщение" : `История ${index}`, files: index === 100 ? [attachment] : [] }));
await context.route("**/api/**", async route => {
  const request = route.request(), url = new URL(request.url()), path = url.pathname;
  const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  assert.equal(path.startsWith("/api/employer"), false, "candidate must never acquire employer authority");
  if (path === "/api/auth/candidate/me") return reply(account);
  if (path === "/api/candidate/profile") return reply(profile);
  if (path === `/api/candidate/recruitment/${id}`) return reply({ ...recruitment, state: "accepted", allowed_actions: [], chat_available: available, chat_writable: writable });
  if (path === `/api/candidate/recruitment/${id}/chat/messages`) {
    if (request.method() === "GET") { reads++; const page = messages.filter(message => message.sequence > Number(url.searchParams.get("after_sequence") || 0)).slice(0, 100); return reply({ messages: page, next_sequence: page.at(-1)?.sequence || messages.at(-1).sequence }); }
    const body = request.postDataJSON(); sends.push(body);
    if (sends.length === 1) return reply({ detail: "synthetic_provider_failure" }, 503);
    const message = { id: "message-outgoing", sequence: 102, sender_id: owner, created_at: now, text: body.text, files: body.files }; messages.push(message); return reply(message);
  }
  if (path === `/api/candidate/recruitment/${id}/chat/files` && request.method() === "POST") { uploads++; assert.equal(request.postDataJSON().data_base64, "bm90ZQ=="); return reply(attachment); }
  if (path === `/api/candidate/recruitment/${id}/chat/files/${attachment.id}`) { downloads++; return reply({ ...attachment, data_base64: "bm90ZQ==" }); }
  return reply({ detail: "synthetic_missing" }, 404);
});
const page = await context.newPage(), errors = []; page.on("pageerror", reason => errors.push(reason.message));
try {
  await page.goto(base + `#/chat/${id}?from=%2Foffers`);
  await page.getByText("Входящее сообщение", { exact: true }).waitFor();
  assert.equal(await page.locator(".recruitment-conversation .message-bubble.incoming").count(), 101);
  const download = page.waitForEvent("download"); await page.locator(".message-file").click(); await download;
  assert.equal(downloads, 1);
  await page.locator('.recruitment-conversation input[type="file"]').setInputFiles({ name: "note.txt", mimeType: "text/plain", buffer: Buffer.from("note") });
  await page.locator(".pending-files").waitFor(); assert.equal(uploads, 1);
  await page.getByLabel("Сообщение работодателю", { exact: true }).fill("Ответ с файлом");
  await page.getByRole("button", { name: "Отправить сообщение", exact: true }).click();
  await page.getByRole("alert").waitFor();
  assert.equal(await page.getByLabel("Сообщение работодателю", { exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "Прикрепить файл до 8 МБ" }).isDisabled(), true);
  await page.getByRole("button", { name: "Отправить сообщение", exact: true }).click();
  await page.locator(".message-bubble.outgoing").getByText("Ответ с файлом", { exact: true }).waitFor();
  assert.deepEqual(sends[0], sends[1], "unknown result must retain exact payload and retry key");
  assert.equal(await page.locator(".message-bubble.outgoing").count(), 1);
  writable = false;
  await page.waitForTimeout(3200);
  await page.getByText(/Чат доступен только для чтения\. Отправка/).waitFor();
  assert.equal(await page.getByRole("button", { name: "Отправить сообщение", exact: true }).isDisabled(), true);
  const before = sends.length;
  assert.equal(await page.getByRole("button", { name: "Прикрепить файл до 8 МБ" }).isDisabled(), true);
  assert.equal(sends.length, before);
  for (const width of [1024, 390]) { await page.setViewportSize({ width, height: 1080 }); await page.waitForTimeout(320); assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 2)); }
  available = false; await page.waitForTimeout(3200); await page.getByText("Чат недоступен для этого приглашения", { exact: true }).waitFor();
  assert.equal(await page.locator(".message-bubble").count(), 0, "unavailable conversation hides private history");
  const settledReads = reads; await page.waitForTimeout(2800); assert.equal(reads, settledReads, "unavailable conversation stops history reads");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: "passed", checks: ["shared bubbles and composer", "cursor history beyond 100 messages", "candidate-only authority", "attachments upload/download", "immutable send retry", "read-only and unavailable gates", "1024/390 no overflow"] }));
} finally { await browser.close(); }

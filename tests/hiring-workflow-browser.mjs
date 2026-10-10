/** Hiring UI regression uses synthetic intercepted responses; no real accounts or chat writes. */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const output = "/tmp/comit-hiring-ui";
await mkdir(output, { recursive: true });
const now = new Date().toISOString(), processId = "10000000-0000-4000-8000-000000000003";
const owner = "10000000-0000-4000-8000-000000000001", candidate = "10000000-0000-4000-8000-000000000002";
try {
  for (const role of (process.env.HIRING_BROWSER_ROLE ? [process.env.HIRING_BROWSER_ROLE] : ["candidate", "employer"])) for (const width of (process.env.HIRING_BROWSER_WIDTH ? [Number(process.env.HIRING_BROWSER_WIDTH)] : [1920, 390])) {
    const context = await browser.newContext({ viewport: { width, height: 1080 } });
    const errors = [], requests = [];
    const page = await context.newPage(); page.on("pageerror", error => errors.push(error.message));
    const base = role === "candidate" ? "http://127.0.0.1:5177/candidate/" : "http://127.0.0.1:5174/employer/";
    let profile = { candidate_id: candidate, revision: 1, fields: {}, consents: { processing: true, pool: true, training: true, version: "profile-v1", training_version: "training-v1" }, projection: { state: "delivered" }, evidence: {}, updated_at: now };
    let systemRead = 0, chatRead = 0, company = { company_name: "Сохранённая компания", revision: 1, updated_at: now };
    let p = { id: processId, owner_id: owner, candidate_id: candidate, vacancy_id: owner, draft_id: owner, profile_revision: 1, revision: 1, state: "accepted", terms: { company_name: company.company_name, vacancy_title: "Backend-разработчик", description: "Синтетическое приглашение", salary: { from_rub: 100000, to_rub: 200000 }, contact_method: "platform", city: "Москва", daily: "5/2", work_type: "remote" }, invited_at: now, closure: null, interviews: [], offers: [], allowed_actions: ["reject"], chat_available: true, chat_writable: true, chat_unread_count: 1, deadline: { stage: "initial", due_at: new Date(Date.now() + 4 * 86400000).toISOString(), remind_at: null, paused_until: null, business_days: 4, time_zone: "Europe/Moscow" }, created_at: now, updated_at: now };
    const messages = [{ id: "system-one", sequence: 1, type: "welcome", text: "Добро пожаловать в Comit", created_at: now, target: null }, { id: "system-two", sequence: 2, type: "recommendations_ready", text: "Ваши рекомендации обновлены", created_at: now, target: { kind: "candidate_profile", path: role === "candidate" ? "/resume" : "/settings" } }];
    await context.route("**/api/**", async route => {
      const request = route.request(), url = new URL(request.url()), path = url.pathname;
      const reply = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
      if (path === `/api/auth/${role}/me`) return reply({ id: role === "candidate" ? candidate : owner, role, email: `${role}@example.test`, email_verified: true, created_at: now, consent: { processing: true, version: "account-v1", accepted_at: now } });
      if (path === `/api/${role}/chat-unread`) return reply({ system_unread_count: 2 - systemRead, recruitment_unread_count: 1 - chatRead, total_unread_count: 3 - systemRead - chatRead });
      if (path === `/api/${role}/system-chat`) return reply({ id: "comit-system", title: "Comit", items: messages.filter(item => item.sequence > Number(url.searchParams.get("after_sequence") || 0)), next_sequence: 2, read_sequence: systemRead, unread_count: 2 - systemRead, has_more: false });
      if (path === `/api/${role}/system-chat/read`) { systemRead = request.postDataJSON().read_sequence; assert.ok(systemRead <= 2); return reply({ read_sequence: systemRead, unread_count: 2 - systemRead }); }
      if (path === `/api/${role}/recruitment`) return reply({ items: [p], limit: 100, has_more: false });
      if (path === `/api/${role}/recruitment/${processId}`) return reply(p);
      if (path === `/api/${role}/recruitment/${processId}/chat/messages`) return reply({ messages: [{ id: "incoming", sequence: 1, sender_id: role === "candidate" ? owner : candidate, text: "Новое сообщение по приглашению", created_at: now, files: [] }], next_sequence: 1 });
      if (path === `/api/${role}/recruitment/${processId}/chat/read`) { chatRead = 1; return reply({ read_sequence: 1, unread_count: 0 }); }
      if (path === `/api/${role}/recruitment/${processId}/actions`) { const body = request.postDataJSON(); requests.push(body); assert.equal(body.action, "reject"); assert.equal(body.expected_revision, 1); assert.deepEqual(body.categories, ["experience"]); p = { ...p, revision: 2, state: "rejected", deadline: null, allowed_actions: [], closure: { actor: "employer", at: now, reason: body.reason, categories: body.categories } }; return reply(p); }
      if (path === "/api/employer/profile") { if (request.method() === "GET") return reply(company); const body = request.postDataJSON(); assert.deepEqual(body, { expected_revision: 1, company_name: "Новое название компании" }); company = { company_name: body.company_name, revision: 2, updated_at: now }; return reply(company); }
      if (path === "/api/candidate/profile") { if (request.method() === "GET") return reply(profile); const body = request.postDataJSON(); assert.deepEqual(body, { expected_revision: 1, fields: {}, consent: { processing: true, pool: true, version: "profile-v1", training: true, training_version: "training-v2" } }); profile = { ...profile, revision: 2, consents: { ...profile.consents, ...body.consent } }; return reply(profile); }
      if (path === "/api/candidate/recommendations/latest") { if (profile.consents.training_version !== "training-v2") return reply({ item: null }); return reply({ item: { recommendation_id: "test-recommendations", week_start: now, state: "ready", text: "Добавьте примеры проектов и подробнее опишите ваш вклад.\n\nУкажите, как вы проверяли качество своей работы.", generated_at: now, evidence: [] } }); }
      return reply({ detail: "synthetic_unavailable" }, 503);
    });
    await page.goto(base + "#/chat");
    await page.locator(".recruitment-chat-tile").first().waitFor();
    assert.match(await page.locator(".recruitment-chat-tile").first().innerText(), /Comit/);
    assert.equal(await page.locator(".recruitment-chat-tile").first().locator(".icon-logo").count(), 1);
    await page.waitForFunction(() => document.querySelector(".chat-unread-badge")?.textContent === "3");
    await page.locator(".recruitment-chat-tile").first().click();
    await page.getByText("Ваши рекомендации обновлены", { exact: true }).waitFor();
    assert.equal(await page.locator(".chat-composer").count(), 0);
    assert.equal(await page.locator(".system-inbox textarea,.system-inbox input").count(), 0);
    await page.waitForFunction(() => document.querySelector(".chat-unread-badge")?.textContent === "1");
    await page.screenshot({ path: `${output}/system-${role}-${width}.png`, fullPage: true });
    await page.goto(base + `#/chat/${processId}`);
    await page.locator(".recruitment-deadline").waitFor();
    assert.match(await page.locator(".recruitment-deadline").innerText(), /рабочего времени/);
    await page.getByText("Новое сообщение по приглашению", { exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector(".chat-unread-badge"));
    if (role === "employer") {
      await page.getByLabel("Действия с кандидатом").click();
      await page.getByRole("button", { name: "Отказать кандидату", exact: true }).click();
      await page.getByLabel("Подробная обратная связь", { exact: true }).fill("x".repeat(100));
      await page.getByRole("button", { name: "Подтвердить", exact: true }).click();
      await page.getByText(/не менее 10 слов/, { exact: false }).last().waitFor();
      assert.equal(requests.length, 0);
      await page.locator(".communication-modal").waitFor();
      await page.getByLabel("Подробная обратная связь", { exact: true }).fill("Для этой вакансии нам требуется опыт самостоятельной разработки и поддержки крупных проектов с большим числом пользователей.");
      await page.getByRole("checkbox", { name: "Опыт", exact: true }).check();
      await page.getByRole("button", { name: "Подтвердить", exact: true }).click();
      await page.getByText("Отказ", { exact: true }).waitFor();
      assert.equal(requests.length, 1);
      await page.goto(base + "#/settings");
      const field = page.getByLabel("Название компании", { exact: true }); await field.waitFor();
      assert.equal(await field.inputValue(), "Сохранённая компания");
      await field.fill("Новое название компании");
      await page.getByRole("button", { name: "Сохранить название", exact: true }).click();
      await page.getByText("Название компании сохранено.", { exact: true }).waitFor();
      await page.reload(); await field.waitFor(); assert.equal(await field.inputValue(), "Новое название компании");
    } else {
      p = { ...p, deadline: { ...p.deadline, stage: "interview_wait", due_at: null, paused_until: now } };
      await page.locator(".recruitment-deadline").waitFor({ state: "hidden" });
      await page.goto(base + "#/statistics");
      await page.getByRole("button", { name: "Настроить рекомендации", exact: true }).click();
      const optIn = page.getByRole("checkbox", { name: "Разрешаю использование обезличенных данных для обучения и рекомендаций", exact: true });
      await optIn.waitFor(); assert.equal(await optIn.isChecked(), false);
      assert.equal(await page.getByRole("button", { name: "Сохранить выбор", exact: true }).isDisabled(), true);
      await optIn.check(); await page.getByRole("button", { name: "Сохранить выбор", exact: true }).click();
      await page.getByText("Выбор сохранён.", { exact: true }).waitFor();
      await page.goto(base + "#/statistics");
      await page.getByText("Добавьте примеры проектов и подробнее опишите ваш вклад.", { exact: true }).waitFor();
      assert.equal(await page.getByText(/Lorem ipsum/).count(), 0);
    }
    assert.deepEqual(errors, []);
    await context.close();
  }
  console.log("PASS: both roles permanent read-only Comit chat, visible receipts and global badge, weekday clocks, detailed rejection, persistent server company, real recommendations; desktop/mobile");
} finally { await browser.close(); }

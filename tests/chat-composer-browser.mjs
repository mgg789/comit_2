/** Shared AI/recruitment composer parity against live Vite with synthetic intercepted APIs only. */
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const owner = "10000000-0000-4000-8000-000000000002", id = "10000000-0000-4000-8000-000000000003";
const now = new Date().toISOString();
const account = { id: owner, email: "candidate-browser@example.test", role: "candidate", email_verified: true, created_at: now, consent: { processing: true, version: "account-v1", accepted_at: now } };
const profile = { candidate_id: owner, revision: 1, fields: { display_name: "Тестовый кандидат", city: "Москва", primary_direction: "backend", declared_grade: "middle", description: "Синтетическое описание кандидата", salary: { from_rub: 180000, to_rub: 240000 }, work_types: ["remote"], fsp_id: "synthetic-fsp", fsp_claim_id: "synthetic-claim", stacks: [{ key: "python", level: 4 }], projects: [{ id: "10000000-0000-4000-8000-000000000006", title: "Синтетический проект", description: "Пример", facts: { role: "developer", duration_months: 12 } }] }, evidence: { skill: { status: "confirmed", input_hash: "synthetic", verified_value: 4, provenance: ["test"], checked_at: now } }, consents: { processing: true, pool: true, training: true, version: "profile-v1", accepted_at: now, training_version: "training-v1", training_accepted_at: now }, projection: { state: "delivered", source_revision: 1, projected_revision: 1, attempts: 1, error_code: null }, updated_at: now };
const recruitment = { id, owner_id: "10000000-0000-4000-8000-000000000001", candidate_id: owner, vacancy_id: "10000000-0000-4000-8000-000000000004", draft_id: "10000000-0000-4000-8000-000000000005", profile_revision: 1, revision: 1, state: "sent", terms: { company_name: "Тестовая компания", vacancy_title: "Backend-разработчик", description: "Описание реального по контракту приглашения из теста", salary: { from_rub: 200000, to_rub: 260000 }, contact_method: "platform", city: "Москва", daily: "Полный день", work_type: "remote" }, invited_at: now, closure: null, interviews: [{ id: "10000000-0000-4000-8000-000000000007", kind: "technical", state: "proposed", starts_at: now, time_zone: "Europe/Moscow", duration_minutes: 30, format: "remote", location: "Видеосвязь", completed_at: null, outcome: null, feedback: null }], offers: [], allowed_actions: ["view", "accept", "decline", "interview_accept", "interview_decline"], chat_available: true, chat_writable: true, created_at: now, updated_at: now };

const errors = [];
const pages = [];
try {
  for (const role of ["candidate", "employer"]) {
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, reducedMotion: "no-preference" });
    await context.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      const reply = body => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
      if (path === `/api/auth/${role}/me`) return reply({ ...account, id: role === "candidate" ? owner : recruitment.owner_id, role });
      if (path === `/api/${role}/recruitment/${id}`) return reply({ ...recruitment, state: "accepted", allowed_actions: [], chat_writable: true });
      if (path.endsWith("/chat/messages")) return reply({ messages: [], next_sequence: 0 });
      if (path.endsWith("/budget")) return reply({ limit_micro_usd: "1000000", available_micro_usd: "900000", used_micro_usd: "100000", reserved_micro_usd: "0", window_days: 7, pricing_configured: true });
      if (path.endsWith("/messages")) return reply({ items: [], next_sequence: null });
      if (path.endsWith("/pending-answers")) return reply([]);
      if (path.endsWith("/context")) return reply({ revision: 1, current_summary: {} });
      if (path.endsWith("/chat") || path.endsWith("/presence")) return reply({ chat_id: "synthetic-chat", status: "active" });
      if (path.endsWith("/recruitment-events")) return reply({ items: [], next_sequence: 0, has_more: false });
      return reply({ items: [], next_cursor: null });
    });
    const page = await context.newPage(); page.on("pageerror", reason => errors.push(reason.message));
    await page.goto(role === "candidate" ? `http://127.0.0.1:5177/candidate/#/chat/${id}` : "http://127.0.0.1:5174/employer/");
    if (role === "employer") await page.getByRole("button", { name: "Открыть AI-агента", exact: true }).click();
    await page.locator(".message-composer textarea").waitFor(); pages.push(page);
  }
  for (const width of [1920, 1024, 390]) {
    const styles = [];
    for (const page of pages) {
      await page.setViewportSize({ width, height: width < 760 ? 844 : 1080 });
      await page.locator(".message-composer textarea").fill("Первая строка\nВторая строка\nТретья строка\nЧетвёртая строка\nПятая строка");
      await page.waitForTimeout(250);
      styles.push(await page.locator(".message-composer").evaluate(form => {
        const field = form.querySelector("textarea"), attach = form.querySelector(".attach-button"), send = form.querySelector(".send-button");
        const box = form.getBoundingClientRect(), text = field.getBoundingClientRect(), css = getComputedStyle(form), type = getComputedStyle(field);
        return { width: box.width, height: box.height, containsText: text.y >= box.y && text.bottom <= box.bottom, fontSize: type.fontSize, lineHeight: type.lineHeight, background: css.backgroundImage + css.backgroundColor, borderRadius: css.borderRadius, attachWidth: attach.getBoundingClientRect().width, sendWidth: send.getBoundingClientRect().width, attachIconWidth: attach.querySelector(".icon").getBoundingClientRect().width, sendIconWidth: send.querySelector(".icon").getBoundingClientRect().width };
      }));
      assert.equal(styles.at(-1).containsText, true, `multiline stays inside composer at ${width}`);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
    }
    if (width < 760) {
      // The AI wrapper reserves space for its floating tools; the shared panel itself is identical.
      assert.equal(styles[0].width, width - 36); assert.equal(styles[1].width, width - 100);
      delete styles[0].width; delete styles[1].width;
    }
    assert.deepEqual(styles[0], styles[1], `AI and recruitment panel style/geometry parity at ${width}`);
    const peer = pages[0];
    assert.ok(await peer.locator(".recruitment-messages").evaluate(list => list.getBoundingClientRect().bottom <= document.querySelector(".communication-composer-wrap").getBoundingClientRect().top));
  }
  const link = pages[0].getByRole("link", { name: "QGramm", exact: true });
  assert.equal(await link.getAttribute("href"), "https://github.com/mgg789/QGramm");
  assert.ok(await pages[0].locator(".qgramm-mark").evaluate(image => image.complete && image.naturalWidth > 0));
  assert.deepEqual(errors, []);
  console.log("PASS identical AI/recruitment composer, multiline containment, history clearance, QGramm asset/link and 1920/1024/390");
} finally { await browser.close(); }

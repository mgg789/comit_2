/** Browser regression for viewport containment and reachable message actions; synthetic APIs only. */
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const now = new Date().toISOString(), owner = "10000000-0000-4000-8000-000000000001";
try {
  for (const role of ["employer", "candidate"]) {
    const context = await browser.newContext({ reducedMotion: "reduce" });
    await context.route("**/api/**", route => {
      const path = new URL(route.request().url()).pathname;
      const reply = value => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
      if (path === `/api/auth/${role}/me`) return reply({ id: owner, role, email: "viewport@example.test", email_verified: true, created_at: now, consent: { processing: true, version: "account-v1", accepted_at: now } });
      if (path === `/api/${role}/system-chat`) return reply({ items: Array.from({ length: 20 }, (_, index) => ({ id: `message-${index}`, sequence: index + 1, text: `Уведомление ${index + 1}. Подробности доступны по кнопке ниже.`, created_at: now, target: { path: "/statistics" } })), next_sequence: 20, read_sequence: 0, has_more: false });
      if (path === `/api/${role}/system-chat/read`) return reply({ read_sequence: 20 });
      if (path.endsWith("/events")) return reply({ events: [], next_sequence: 0 });
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "synthetic_not_available" }) });
    });
    const page = await context.newPage();
    const errors = []; page.on("pageerror", error => errors.push(error.message));
    for (const size of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(size);
      const base = role === "employer" ? process.env.EMPLOYER_URL || "http://127.0.0.1:5174/employer/" : process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/";
      await page.goto(base + "#/chat/comit-system");
      await page.locator(".message-bubble").last().waitFor({ state: "attached" });
      await page.locator(".recruitment-messages").evaluate(list => { list.scrollTop = list.scrollHeight; });
      await page.waitForTimeout(350);
      const bounds = await page.evaluate(() => {
        const list = document.querySelector(".recruitment-messages"), last = list.querySelector(".message-bubble:last-child"), button = last.querySelector("button"), nav = document.querySelector(".nav");
        return { documentHeight: document.documentElement.scrollHeight, height: innerHeight, listBottom: list.getBoundingClientRect().bottom, buttonBottom: button.getBoundingClientRect().bottom, navTop: nav.getBoundingClientRect().top, mask: getComputedStyle(list).maskImage, blur: getComputedStyle(list.parentElement, "::after").backdropFilter };
      });
      assert.ok(bounds.documentHeight <= bounds.height + 1, "document must not scroll");
      assert.ok(bounds.buttonBottom < bounds.listBottom - 24, "last action clears fading edge");
      if (size.width === 390) assert.ok(bounds.listBottom < bounds.navTop, "history clears mobile navigation");
      assert.notEqual(bounds.mask, "none"); assert.notEqual(bounds.blur, "none");
    }
    assert.deepEqual(errors, []);
    await context.close();
  }
  console.log("PASS system chat viewport and reachable final actions in both roles and sizes");
} finally { await browser.close(); }

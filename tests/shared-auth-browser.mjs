/** Both roles must render the established employer login and OTP, including all CSS dependencies. */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const output = process.env.AUTH_SCREENSHOTS || "/tmp/comit-shared-auth";
await mkdir(output, { recursive: true });
const roles = { employer: process.env.EMPLOYER_URL || "http://127.0.0.1:5174/employer/", candidate: process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/" };
try {
  for (const width of [1920, 1024, 390]) {
    const samples = {};
    for (const [role, base] of Object.entries(roles)) {
      const context = await browser.newContext({ viewport: { width, height: 1080 } });
      let calls = 0;
      await context.route("**/api/**", async route => {
        const path = new URL(route.request().url()).pathname;
        assert.ok(!path.includes(`/auth/${role === "candidate" ? "employer" : "candidate"}/`), "role isolation");
        if (path.endsWith("/code")) { calls++; return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ challenge_id: "synthetic", resend_after: "2099-01-01T00:00:00Z" }) }); }
        return route.fulfill({ status: 401, contentType: "application/json", body: "{}" });
      });
      const page = await context.newPage();
      const errors = []; page.on("pageerror", error => errors.push(error.message));
      await page.goto(base); await page.getByLabel("Email", { exact: true }).waitFor(); await page.waitForTimeout(500);
      const appearance = () => page.evaluate(() => {
        const pick = selector => { const element = document.querySelector(selector), style = getComputedStyle(element), box = element.getBoundingClientRect(); return { display: style.display, background: style.backgroundColor, radius: style.borderRadius, gap: style.gap, align: style.alignItems, font: style.fontFamily, size: style.fontSize, height: box.height, width: box.width }; };
        return { login: pick(".login"), card: pick(".login-card"), yandex: pick(".yandex"), consent: pick(".login-consent"), input: pick(".login-email input"), overflow: document.documentElement.scrollWidth - innerWidth };
      });
      const email = await appearance();
      assert.equal(email.yandex.display, "grid"); assert.equal(email.yandex.background, "rgb(240, 240, 240)");
      assert.equal(email.consent.display, "flex"); assert.equal(email.consent.align, "center"); assert.ok(email.overflow <= 1);
      await page.screenshot({ path: `${output}/${role}-email-${width}.png`, fullPage: true });
      await page.getByLabel("Email", { exact: true }).fill("auth-parity@example.test");
      await page.getByRole("button", { name: "Получить код", exact: true }).click(); assert.equal(calls, 0);
      await page.getByRole("checkbox").check(); await page.getByRole("button", { name: "Получить код", exact: true }).click();
      await page.getByLabel("Код подтверждения из письма, 6 цифр").waitFor(); await page.waitForTimeout(350);
      const otp = await page.locator(".otp-card").evaluate(card => { const style = getComputedStyle(card), box = card.getBoundingClientRect(); return { width: box.width, height: box.height, padding: style.padding, radius: style.borderRadius, digits: card.querySelectorAll(".otp-digit").length }; });
      assert.equal(otp.digits, 6); assert.equal(calls, 1);
      await page.screenshot({ path: `${output}/${role}-otp-${width}.png`, fullPage: true });
      assert.deepEqual(errors, []); samples[role] = { email, otp }; await context.close();
    }
    assert.deepEqual(samples.candidate, samples.employer, `auth visual/style parity at ${width}`);
  }
  console.log(`PASS shared login/OTP exact style and geometry parity, consent and role isolation at 1920/1024/390; screenshots ${output}`);
} finally { await browser.close(); }

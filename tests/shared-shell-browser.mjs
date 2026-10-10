/** Shared shell regression uses intercepted synthetic accounts; no real authentication or writes. */
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", headless: true });
const results = [];
try {
  for (const role of ["employer", "candidate"]) {
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
    await context.route("**/api/**", route => {
      const path = new URL(route.request().url()).pathname.replace(/^\/api/, "");
      const payload = path === `/auth/${role}/me` ? { id: "synthetic-owner", email: "shell@example.test", role, email_verified: true } : path === "/candidate/profile" ? { error: "not_found" } : { items: [], next_cursor: null, has_more: false };
      return route.fulfill({ status: path === "/candidate/profile" ? 404 : 200, contentType: "application/json", body: JSON.stringify(payload) });
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const url = role === "employer" ? (process.env.EMPLOYER_URL || "http://127.0.0.1:5174/employer/") : (process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/");
    await page.goto(url);
    await page.getByRole("button", { name: "Открыть меню аккаунта", exact: true }).click();
    await page.locator(".account-menu").waitFor();
    assert.equal(await page.locator(".account-tools .account-menu").count(), 0, "Menu must stay outside the tool capsule");
    assert.equal(await page.locator(".nav-label").count(), role === "employer" ? 5 : 4, "All navigation labels stay mounted");
    await page.getByRole("button", { name: "Выйти", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Выйти из аккаунта?" });
    await dialog.waitFor();
    await page.waitForTimeout(350);
    const style = await dialog.evaluate(element => {
      const css = getComputedStyle(element);
      return { className: element.className, width: css.width, radius: css.borderRadius, padding: css.padding, font: css.fontSize };
    });
    results.push(style);
    assert.equal(style.className, "modal account-confirmation glass");
    await page.getByRole("button", { name: "Отмена", exact: true }).click();
    await dialog.waitFor({ state: "detached" });
    assert.deepEqual(errors, []);
    await context.close();
  }
  assert.deepEqual(results[0], results[1], "Both apps must render the identical employer confirmation shell");
  console.log("PASS: shared navigation/account structure and identical confirmation presentation");
} finally { await browser.close(); }

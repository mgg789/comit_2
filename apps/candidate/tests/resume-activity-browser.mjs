/** Synthetic layout and source-state regression; never mutates real candidate accounts. */
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage();
const owner = "10000000-0000-4000-8000-000000000002", now = new Date().toISOString();
let historyState = "ready";
const daily = Array.from({ length: 30 }, (_, index) => ({ date: new Date(Date.UTC(2026, 8, 11 + index)).toISOString().slice(0, 10), count: index === 6 ? 3 : index === 12 ? 1 : 0 }));
const profile = { candidate_id: owner, revision: 1, fields: { display_name: "Тестовый кандидат", primary_direction: "frontend", city: "Москва", salary: { from_rub: 100000, to_rub: 150000 }, work_types: ["remote"] }, evidence: {}, consents: { processing: true, pool: true, version: "profile-v1", accepted_at: now }, projection: { state: "delivered", source_revision: 1, projected_revision: 1, attempts: 1, error_code: null }, updated_at: now };
await page.route("**/api/**", async route => {
  const path = new URL(route.request().url()).pathname;
  const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  if (path === "/api/auth/candidate/me") return reply({ id: owner, email: "activity@example.test", role: "candidate", email_verified: true, created_at: now, consent: { processing: true, version: "account-v1", accepted_at: now } });
  if (path === "/api/candidate/profile") return reply(profile);
  if (path === "/api/candidate/pipeline") return reply({ state: "ready", profile_revision: 1, missing_requirements: [] });
  if (path === "/api/candidate/recruitment") return reply({ items: [], has_more: false, limit: 100, offset: 0 });
  if (path === "/api/candidate/testing-history") return historyState === "error" ? reply({ detail: "service_unavailable" }, 503) : reply({ timezone: "Europe/Moscow", kind: "confirmed_results", period: { from: daily[0].date, to: daily.at(-1).date }, daily: historyState === "empty" ? daily.map(day => ({ ...day, count: 0 })) : daily, total: historyState === "empty" ? 0 : 4 });
  return reply({ detail: "synthetic_unavailable" }, 503);
});
const base = process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/";
const errors = []; page.on("pageerror", error => errors.push(error.message));
try {
  for (const width of [1920, 390]) {
    await page.setViewportSize({ width, height: 1080 });
    await page.goto(base + "#/resume");
    await page.getByRole("heading", { name: "Тестовый кандидат", exact: true }).waitFor();
    await page.getByRole("button", { name: "Обновить статус", exact: true }).waitFor();
    await page.waitForTimeout(400);
    const geometry = await page.evaluate(() => {
      const actions = [...document.querySelectorAll(".candidate-publication-actions > .action")].map(node => { const box = node.getBoundingClientRect(); return { x: box.x, y: box.y, width: box.width }; });
      const hero = document.querySelector(".candidate-profile-hero").getBoundingClientRect(), body = document.querySelector(".candidate-profile-body").getBoundingClientRect(), chips = document.querySelector(".candidate-profile-hero > .candidate-chips").getBoundingClientRect();
      return { actions, gap: body.top - chips.bottom, heroTop: hero.top, width: document.documentElement.scrollWidth };
    });
    assert.ok(Math.abs(geometry.actions[0].y - geometry.actions[1].y) < 1, JSON.stringify(geometry));
    assert.ok(Math.abs(geometry.actions[0].width - geometry.actions[1].width) < 1);
    assert.ok(geometry.actions[0].x < geometry.actions[1].x);
    assert.ok(geometry.gap <= 50 && geometry.gap >= 0, JSON.stringify(geometry));
    assert.ok(geometry.width <= width + 1);
    assert.ok(await page.getByRole("button", { name: "Отозвать резюме", exact: true }).evaluate(node => node.classList.contains("action-danger")));
    await page.goto(base + "#/statistics");
    await page.locator(".candidate-testing-days").waitFor();
    assert.equal(await page.locator('.candidate-testing-days [role="listitem"]').count(), 30);
    assert.equal(await page.locator(".candidate-testing-days .intensity-3").count(), 1);
    await page.locator(".candidate-testing-days .intensity-3").focus();
    await page.locator(".intensity-3 .candidate-testing-tooltip").waitFor({ state: "visible" });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  }
  historyState = "empty"; await page.reload(); await page.getByText("За последние 30 дней подтверждённых тестов пока нет.", { exact: true }).waitFor();
  assert.equal(await page.locator('.candidate-testing-days [role="listitem"]').count(), 30);
  historyState = "error"; await page.reload(); await page.locator('.candidate-stat-history [role="alert"]').waitFor();
  assert.equal(await page.locator(".candidate-testing-days").count(), 0);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ result: "passed", checks: ["equal publication buttons and red withdrawal", "resume hero content-sized gap", "30 actual-source day cells and accessible tooltip", "true zero empty state and error never rendered as zeros"], viewports: [1920, 390] }));
} finally { await browser.close(); }

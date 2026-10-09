/** Synthetic candidate dashboard geometry, skill values and background regression. */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const now = new Date().toISOString(), owner = "10000000-0000-4000-8000-000000000002";
let failProfile = false, emptySkills = false;
const profile = { candidate_id: owner, revision: 1, fields: { display_name: "Тестовый кандидат", primary_direction: "frontend", domains: [{ key: "web", level: 5 }], stacks: [{ key: "react", level: 4 }], soft_skills: [{ key: "communication", level: 3 }] }, evidence: {}, consents: { processing: true, pool: true, version: "profile-v1", accepted_at: now }, projection: { state: "delivered", source_revision: 1, projected_revision: 1, attempts: 1, error_code: null }, updated_at: now };
await page.route("**/api/**", async route => {
  const path = new URL(route.request().url()).pathname;
  const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  if (path === "/api/auth/candidate/me") return reply({ id: owner, email: "statistics@example.test", role: "candidate", email_verified: true, created_at: now, consent: { processing: true, version: "account-v1", accepted_at: now } });
  if (path === "/api/candidate/profile") return failProfile ? reply({ detail: "service_unavailable" }, 503) : reply(emptySkills ? { ...profile, fields: { display_name: "Тестовый кандидат" } } : profile);
  if (path === "/api/candidate/recruitment") return reply({ items: [], has_more: false, limit: 100, offset: 0 });
  return reply({ detail: "synthetic_unavailable" }, 503);
});
const errors = []; page.on("pageerror", error => errors.push(error.message));
const base = process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/";
const output = "/tmp/comit-candidate-statistics"; await mkdir(output, { recursive: true });
try {
  for (const width of [1920, 1024, 390]) {
    await page.setViewportSize({ width, height: 1080 });
    await page.goto(base + "#/statistics"); await page.locator(".candidate-statistics").waitFor(); await page.waitForTimeout(450);
    const geometry = await page.evaluate(() => ({ height: document.documentElement.scrollHeight, appBottom: document.querySelector(".candidate-app").getBoundingClientRect().bottom + scrollY, width: innerWidth, scrollWidth: document.documentElement.scrollWidth, cards: [...document.querySelectorAll(".candidate-statistics > section")].map(card => ({ top: card.getBoundingClientRect().top, bottom: card.getBoundingClientRect().bottom })) }));
    if (process.env.CANDIDATE_STATS_BEFORE) { console.log(width, geometry); continue; }
    assert.ok(geometry.scrollWidth <= width + 1); assert.ok(geometry.appBottom >= geometry.height - 1, JSON.stringify(geometry));
    if (width > 1200) for (const [left, right] of [[0, 1], [2, 3]]) { assert.ok(Math.abs(geometry.cards[left].top - geometry.cards[right].top) < 1); assert.ok(Math.abs(geometry.cards[left].bottom - geometry.cards[right].bottom) < 1); }
    assert.equal(await page.getByText("Вы на платформе", { exact: true }).count(), 0);
    assert.equal(await page.locator(".candidate-stat-skills").innerText(), "");
    const bars = await page.locator(".candidate-skill-map .candidate-skill-value").evaluateAll(nodes => nodes.map(node => Number(node.getAttribute("width")))); assert.deepEqual(bars, [500 * 5 / 7, 500 * 4 / 7, 500 * 3 / 7]);
    await page.screenshot({ path: `${output}/statistics-${width}.png`, fullPage: true });
    await page.goto(base + "#/resume"); await page.getByRole("heading", { name: "Тестовый кандидат", exact: true }).waitFor(); await page.waitForTimeout(400);
    assert.ok(await page.evaluate(() => document.querySelector(".candidate-app").getBoundingClientRect().bottom + scrollY >= document.documentElement.scrollHeight - 1), `resume background at ${width}`);
  }
  if (!process.env.CANDIDATE_STATS_BEFORE) {
    emptySkills = true; await page.goto(base + "#/statistics"); await page.getByRole("img", { name: "Навыки пока не указаны" }).waitFor(); assert.equal(await page.locator(".candidate-skill-value").count(), 0);
    failProfile = true; await page.reload(); await page.getByRole("alert").waitFor(); await page.getByRole("button", { name: "Обновить", exact: true }).waitFor(); failProfile = false; await page.getByRole("button", { name: "Обновить", exact: true }).click(); await page.getByRole("img", { name: "Навыки пока не указаны" }).waitFor();
    assert.deepEqual(errors, []); console.log(`PASS aligned dashboard rows, actual skill bars without visible labels, empty/error states and full background at 1920/1024/390; ${output}`);
  }
} finally { await browser.close(); }

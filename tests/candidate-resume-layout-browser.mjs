/** Synthetic readiness and independent skill-level regression; never submits real account data. */
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const id = "10000000-0000-4000-8000-000000000002", now = new Date().toISOString();
let pool = false, delivery = "delivered", ready = "ready", revision = 9, missing = [], retries = 0;
const errors = []; page.on("pageerror", error => errors.push(error.message));
await page.route("**/api/**", async route => {
  const path = new URL(route.request().url()).pathname;
  const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  const profile = () => ({ candidate_id: id, revision: 2, fields: { display_name: "Тестовый кандидат", description: "Описание резюме\nВторая строка", relocation_ready: false, relocation_cities: ["Казань"], declared_grade: "middle", domains: [{ key: "web", level: 5 }], stacks: [{ key: "react", level: 3 }], soft_skills: [{ key: "communication", level: 4 }] }, evidence: { "domains:web": { status: "confirmed" } }, consents: { processing: true, pool, version: "profile-v1", accepted_at: now }, projection: { state: delivery, source_revision: 2, projected_revision: 9, attempts: 1, error_code: null }, updated_at: now });
  if (path === "/api/auth/candidate/me") return reply({ id, email: "readiness@example.test", role: "candidate", email_verified: true, consent: { processing: true, version: "account-v1", accepted_at: now }, created_at: now });
  if (path === "/api/candidate/profile/projection/retry") { retries++; delivery = "delivered"; return reply(profile()); }
  if (path === "/api/candidate/profile") return reply(profile());
  if (path === "/api/candidate/pipeline") return reply({ candidate_id: id, profile_revision: revision, state: ready, missing_requirements: missing, category: null, strict_schema_version: 1, attempt_count: 1, error_code: null });
  if (path === "/api/candidate/recruitment") return reply({ items: [], has_more: false, offset: 0, limit: 100 });
  return reply({ detail: "synthetic_unavailable" }, 503);
});
const base = process.env.CANDIDATE_URL || "http://localhost:8088/candidate/";
try {
  await page.goto(base + "#/resume");
  const bio = page.locator(".candidate-profile-bio");
  await bio.waitFor();
  assert.ok(await bio.evaluate(el => el.classList.contains("panel") && getComputedStyle(el).textAlign === "left" && getComputedStyle(el).backgroundColor !== "rgba(0, 0, 0, 0)"));
  await page.getByRole("button", { name: "Редактировать резюме", exact: true }).click();
  const ready = page.getByRole("checkbox", {name:"Готовность к переезду",exact:true});
  const cities = page.getByRole("textbox", {name:"Города для переезда, через запятую",exact:true});
  assert.equal(await cities.isDisabled(), true);
  await ready.check(); assert.equal(await cities.isEnabled(), true);
  await cities.fill("Казань, Санкт-Петербург");
  await ready.uncheck(); assert.equal(await cities.isDisabled(), true);
  await ready.check(); assert.equal(await cities.inputValue(), "Казань, Санкт-Петербург");
  const gap = await page.locator(".candidate-relocation-check").evaluate(el => parseFloat(getComputedStyle(el).gap));
  assert.ok(gap > 0 && gap <= 12);
  assert.deepEqual(errors, []);
  console.log("PASS description panel, relocation spacing, disabled cities, preserved draft");
} finally { await browser.close(); }

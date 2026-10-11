/** Salary input/save/view regression with synthetic API responses; never writes real accounts. */
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const id = "10000000-0000-4000-8000-000000000002", now = new Date().toISOString();
let savedSalary = 63000, submittedSalary;
let pool = true, delivery = "delivered", ready = "ready", revision = 9, missing = [], retries = 0;
const errors = []; page.on("pageerror", error => errors.push(error.message));
await page.route("**/api/**", async route => {
  const path = new URL(route.request().url()).pathname;
  const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  const profile = () => ({ candidate_id: id, revision: 2, fields: { display_name: "Тестовый кандидат", description: "Зарплата от 180 000 руб. из исходного PDF", city:"Москва", primary_direction:"backend", salary:{from_rub:savedSalary,to_rub:null}, relocation_ready: false, relocation_cities: ["Казань"], declared_grade: "middle", domains: [{ key: "web", level: 5 }], stacks: [{ key: "react", level: 3 }], soft_skills: [{ key: "communication", level: 4 }] }, evidence: { "domains:web": { status: "confirmed" } }, consents: { processing: true, pool, version: "profile-v1", accepted_at: now }, projection: { state: delivery, source_revision: 2, projected_revision: 9, attempts: 1, error_code: null }, updated_at: now });
  if (path === "/api/auth/candidate/me") return reply({ id, email: "readiness@example.test", role: "candidate", email_verified: true, consent: { processing: true, version: "account-v1", accepted_at: now }, created_at: now });
  if (path === "/api/candidate/profile/projection/retry") { retries++; delivery = "delivered"; return reply(profile()); }
  if (path === "/api/candidate/profile" && route.request().method() === "PATCH") {
    submittedSalary = route.request().postDataJSON().fields.salary;
    savedSalary = submittedSalary.from_rub;
    return reply(profile());
  }
  if (path === "/api/candidate/profile") return reply(profile());
  if (path === "/api/candidate/pipeline") return reply({ candidate_id: id, profile_revision: revision, state: ready, missing_requirements: missing, category: null, strict_schema_version: 1, attempt_count: 1, error_code: null });
  if (path === "/api/candidate/recruitment") return reply({ items: [], has_more: false, offset: 0, limit: 100 });
  return reply({ detail: "synthetic_unavailable" }, 503);
});
const base = process.env.CANDIDATE_URL || "http://localhost:8088/candidate/";
try {
  await page.goto(base + "#/resume");
  await page.getByRole("button", {name:"Редактировать резюме",exact:true}).click();
  const amount = page.locator("input[inputmode=numeric]");
  await amount.fill("200000");
  const box = await amount.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, 600); await page.waitForTimeout(200);
  assert.equal(await amount.inputValue(), "200000");
  await amount.focus(); await page.keyboard.press("ArrowDown");
  assert.equal(await amount.inputValue(), "200000");
  await amount.fill("200 000"); assert.equal(await amount.inputValue(), "200000");
  await page.getByRole("button", {name:"Сохранить резюме",exact:true}).click();
  await page.locator(".candidate-profile-hero .tag").filter({hasText:"от 200к ₽"}).waitFor();
  assert.deepEqual(submittedSalary, {from_rub:200000,to_rub:null});
  await page.reload();
  await page.locator(".candidate-profile-hero .tag").filter({hasText:"от 200к ₽"}).waitFor();
  await page.goto(base + "#/statistics");
  await page.locator(".candidate-stat-market .tag").filter({hasText:"от 200к ₽"}).waitFor();
  assert.deepEqual(errors, []);
  console.log("PASS salary wheel/arrow protection, RUB payload, reload and statistics agreement");
} finally { await browser.close(); }

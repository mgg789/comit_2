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
  const profile = () => ({ candidate_id: id, revision: 2, fields: { display_name: "Тестовый кандидат", declared_grade: "middle", domains: [{ key: "web", level: 5 }], stacks: [{ key: "react", level: 3 }], soft_skills: [{ key: "communication", level: 4 }] }, evidence: { "domains:web": { status: "confirmed" } }, consents: { processing: true, pool, version: "profile-v1", accepted_at: now }, projection: { state: delivery, source_revision: 2, projected_revision: 9, attempts: 1, error_code: null }, updated_at: now });
  if (path === "/api/auth/candidate/me") return reply({ id, email: "readiness@example.test", role: "candidate", email_verified: true, consent: { processing: true, version: "account-v1", accepted_at: now }, created_at: now });
  if (path === "/api/candidate/profile/projection/retry") { retries++; delivery = "delivered"; return reply(profile()); }
  if (path === "/api/candidate/profile") return reply(profile());
  if (path === "/api/candidate/pipeline") return reply({ candidate_id: id, profile_revision: revision, state: ready, missing_requirements: missing, category: null, strict_schema_version: 1, attempt_count: 1, error_code: null });
  if (path === "/api/candidate/recruitment") return reply({ items: [], has_more: false, offset: 0, limit: 100 });
  return reply({ detail: "synthetic_unavailable" }, 503);
});
const base = process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/";
async function open() { await page.goto(base + "#/resume"); await page.reload(); await page.getByRole("heading", { name: "Уровни навыков", exact: true }).waitFor(); }
try {
  await open(); await page.getByText(/Показ работодателям выключен/).waitFor();
  const skills = page.locator(".candidate-grade-section"); assert.ok((await skills.innerText()).includes("5 / 7")); assert.ok((await skills.innerText()).includes("3 / 7")); assert.ok((await skills.innerText()).includes("4 / 7")); assert.ok((await skills.innerText()).includes("Подтверждено")); assert.equal(await skills.getByText(/Middle/).count(), 0);
  pool = true; await open(); await page.getByText("Резюме участвует в подборе работодателей.", { exact: true }).waitFor();
  revision = 1; await open(); await page.getByText("Резюме сохранено. Готовим его для подбора.", { exact: true }).waitFor(); assert.equal(await page.getByText("Резюме участвует в подборе работодателей.", { exact: true }).count(), 0);
  revision = 9; ready = "draft"; missing = ["city", "salary"]; await open(); await page.getByText("Для подбора добавьте: город, зарплатные ожидания.", { exact: true }).waitFor();
  missing = []; ready = "ready"; delivery = "failed"; await open(); await page.getByRole("button", { name: "Повторить отправку", exact: true }).click(); await page.getByText("Резюме участвует в подборе работодателей.", { exact: true }).waitFor(); assert.equal(retries, 1);
  assert.deepEqual(errors, []); console.log("PASS consent, exact-revision readiness, missing requirements, delivery retry and independent skill levels");
} finally { await browser.close(); }

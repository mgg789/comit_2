/** Real rendered directory polling; only synthetic role-scoped APIs are intercepted. */
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const owner = "10000000-0000-4000-8000-000000000001", now = new Date().toISOString();
const account = { id: owner, email: "chat-list@example.test", role: "employer", email_verified: true, created_at: now, consent: { processing: true, version: "account-v1", accepted_at: now } };
const id = index => `10000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
const item = index => ({ id: id(index), owner_id: owner, candidate_id: id(500 + index), vacancy_id: id(1000 + index), draft_id: id(1500 + index), profile_revision: 1, revision: 1, state: "accepted", terms: { company_name: "Synthetic", vacancy_title: `Чат ${index}`, description: "Synthetic invitation", salary: { from_rub: 100000, to_rub: 200000 }, contact_method: "platform", city: "Москва", daily: "5/2", work_type: "remote" }, invited_at: now, closure: null, interviews: [], offers: [], allowed_actions: [], chat_available: true, chat_writable: true, chat_unread_count: 0, created_at: now, updated_at: now });
let items = Array.from({ length: 101 }, (_, index) => item(index + 1));
let histories = 0;
await context.route("**/api/**", async route => {
  const request = route.request(), url = new URL(request.url()), path = url.pathname;
  const reply = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
  if (path === "/api/auth/employer/me") return reply(account);
  if (path === "/api/employer/recruitment") {
    const offset = Number(url.searchParams.get("cursor") || url.searchParams.get("offset") || 0), limit = Number(url.searchParams.get("limit"));
    const page = items.slice(offset, offset + limit), next = offset + page.length;
    return reply({ items: page, has_more: next < items.length, next_cursor: next < items.length ? String(next) : null });
  }
  if (path.includes("/chat/messages")) histories++;
  if (path.endsWith("/events")) return reply({ events: [], next_sequence: 0 });
  return reply({ detail: "synthetic_not_available" }, 404);
});
const page = await context.newPage(), errors = [];
page.on("pageerror", error => errors.push(error.message));
try {
  await page.goto((process.env.EMPLOYER_URL || "http://127.0.0.1:5174/employer/") + "#/chat");
  await page.getByText("Чат 1", { exact: true }).waitFor();
  assert.equal(await page.locator(".recruitment-chat-tile").count(), 101);
  assert.match(await page.locator(".recruitment-chat-tile").first().innerText(), /Comit/);
  await page.getByRole("button", { name: "Другие разговоры", exact: true }).click();
  await page.getByText("Чат 101", { exact: true }).waitFor();
  items = [item(102), ...items.map(row => row.id === id(101) ? { ...row, chat_unread_count: 2 } : row)];
  await page.getByText("Чат 102", { exact: true }).waitFor({ timeout: 12000 });
  const last = page.locator(".recruitment-chat-tile").filter({ has: page.getByText("Чат 101", { exact: true }) });
  await last.getByText("2 новых сообщения", { exact: true }).waitFor();
  assert.equal(await page.locator(".recruitment-chat-tile").count(), 103);
  assert.equal(histories, 0, "directory must not read message history");
  assert.deepEqual(errors, []);
  console.log("PASS newly created chats and unread on loaded second page update without fetching private history");
} finally { await browser.close(); }

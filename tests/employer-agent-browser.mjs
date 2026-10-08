/** Focused chat regressions against Vite; every API call uses synthetic intercepted data.
 * Run with PLAYWRIGHT_MODULE pointing to the installed Playwright module.
 */
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "chrome", headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await context.newPage();
const failures = [];
page.on("pageerror", (error) => failures.push(error.message));
const created = "2026-10-08T12:00:00Z";
let messages = Array.from({ length: 100 }, (_, index) => ({
  message_id: `history-${index}`, chat_id: "synthetic-chat", turn_id: `history-turn-${Math.floor(index / 2)}`,
  role: index % 2 ? "assistant" : "user", content: index % 2 ? "Синтетический ответ из истории" : "Синтетический вопрос",
  sequence: index + 1, created_at: index % 2 ? "2026-10-08T12:00:23Z" : created,
}));
let polls = 0;
let sends = 0;
let cursorReads = [];
await context.route("**/api/**", async (route) => {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname.replace(/^\/api/, "");
  const reply = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
  if (path === "/auth/employer/me") return reply({ id: "synthetic-owner", email: "agent-browser@example.test", role: "employer",
    email_verified: true, created_at: created, consent: { processing: true, version: "test", accepted_at: created } });
  if (path === "/employer/vacancies") return reply({ items: [], next_cursor: null });
  if (path.endsWith("/presence") || path.endsWith("/chat")) return reply({ chat_id: "synthetic-chat", status: "active" });
  if (path.endsWith("/budget")) return reply({ limit_micro_usd: "1000000", available_micro_usd: "900000", used_micro_usd: "100000", reserved_micro_usd: "0", window_days: 7, pricing_configured: true });
  if (path.endsWith("/pending-answers")) return reply([]);
  if (path.endsWith("/messages") && request.method() === "GET") {
    const cursor = Number(url.searchParams.get("after_sequence") || 0);
    cursorReads.push(cursor);
    const rest = messages.filter((item) => item.sequence > cursor);
    const items = rest.slice(0, 100);
    return reply({ items, next_sequence: rest.length > 100 ? items.at(-1).sequence : null });
  }
  if (path.endsWith("/messages") && request.method() === "POST") {
    sends++;
    messages.push({ message_id: "new-user", chat_id: "synthetic-chat", turn_id: "new-turn", role: "user",
      content: request.postDataJSON().message, sequence: 101, created_at: created });
    return reply({ turn_id: "new-turn", status: "queued", created_at: created }, 202);
  }
  if (path.endsWith("/turns/new-turn")) {
    polls++;
    if (polls === 2) messages.push({ message_id: "new-answer", chat_id: "synthetic-chat", turn_id: "new-turn", role: "assistant",
      content: "**Готово, синтетический ответ**\n\n- Один\n- Два", sequence: 102, created_at: "2026-10-08T12:00:23Z" });
    return reply({ turn_id: "new-turn", status: polls >= 2 ? "succeeded" : "processing", created_at: created });
  }
  if (path.includes("/turns/history-turn-")) return reply({ turn_id: path.split("/").at(-1), status: "succeeded", created_at: created });
  return reply({ detail: "synthetic_not_available" }, 503);
});
const output = process.env.EMPLOYER_SCREENSHOTS || "/tmp/comit-agent-regression";
await mkdir(output, { recursive: true });
try {
  await page.goto(process.env.EMPLOYER_BROWSER_URL || "http://127.0.0.1:5174/employer/");
  await page.getByRole("button", { name: "Открыть AI-агента", exact: true }).click();
  await page.locator(".organization-message").last().waitFor();
  await page.waitForFunction(() => document.querySelectorAll(".organization-message").length === 100);
  await page.waitForFunction(() => {
    const node = document.querySelector(".organization-history");
    return node.scrollTop > 100 && node.scrollHeight - node.clientHeight - node.scrollTop < 2;
  });
  const readScroll = () => page.evaluate(() => ({ chat: document.querySelector(".organization-history").scrollTop, background: window.scrollY }));
  for (const x of [8, 1912, 960]) {
    const before = await readScroll();
    await page.mouse.move(x, 450);
    await page.mouse.wheel(0, -500);
    await page.waitForFunction((old) => document.querySelector(".organization-history").scrollTop < old, before.chat);
    const after = await readScroll();
    assert.equal(after.background, before.background, "background must stay fixed at either gutter and center");
  }
  assert.equal(await page.evaluate(() => document.body.style.overflow), "hidden");
  await page.getByRole("textbox", { name: "Сообщение AI-агенту" }).fill("Проверка чата");
  await page.getByRole("button", { name: "Отправить сообщение агенту" }).click();
  await page.getByRole("status", { name: "Агент думает" }).waitFor();
  assert.equal(await page.locator(".agent-thinking .agent-think-dot").count(), 3);
  assert.equal(await page.getByRole("textbox", { name: "Сообщение AI-агенту" }).inputValue(), "");
  await page.waitForFunction(() => {
    const node = document.querySelector(".organization-history");
    return node.scrollHeight - node.clientHeight - node.scrollTop < 2;
  });
  await page.screenshot({ path: `${output}/thinking.png` });
  await page.getByText("Готово, синтетический ответ", { exact: true }).waitFor();
  assert.equal(await page.getByText("Проверка чата", { exact: true }).count(), 1, "optimistic bubble must be replaced without duplicates");
  assert.equal(await page.locator(".agent-thinking").count(), 0);
  assert.equal(sends, 1);
  assert.ok(cursorReads.includes(100), "terminal full page must advance cursor");
  assert.ok(cursorReads.filter((cursor) => cursor === 0).length <= 2, "no 50ms terminal-page replay loop");
  assert.equal(await page.getByRole("button", { name: "Ответ помог", exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "Ответ не помог", exact: true }).count(), 0);
  assert.equal(await page.locator(".agent-reply-header details,.agent-reply-header summary").count(), 0);
  await page.locator(".agent-final-answer .copy-message").last().click();
  await page.locator(".copy-feedback").getByText("Скопировано", { exact: true }).waitFor();
  const copyAlignment = await page.locator(".agent-final-answer .copy-message").last().evaluate((button) => {
    const glyph = button.querySelector('[aria-hidden="true"]').getBoundingClientRect();
    const label = button.querySelector('.copy-feedback').getBoundingClientRect();
    return Math.abs(glyph.y + glyph.height / 2 - label.y - label.height / 2);
  });
  assert.ok(copyAlignment < 3, "copy feedback must align with checkmark center");
  for (const width of [1920, 1024, 390]) {
    await page.setViewportSize({ width, height: width < 760 ? 844 : 1080 });
    await page.locator(".agent-final-answer").last().scrollIntoViewIfNeeded();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflow, false, `viewport ${width} must not overflow horizontally`);
    await page.screenshot({ path: `${output}/answer-${width}.png` });
  }
  await page.keyboard.press("Escape");
  await page.getByRole("dialog", { name: "AI-агент", exact: true }).waitFor({ state: "hidden" });
  assert.equal(await page.evaluate(() => document.body.style.overflow), "");
  const chatButton = page.getByRole("button", { name: "Переписка и приглашения", exact: true });
  await chatButton.hover();
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.account-tools .icon-message')).transform !== "none");
  assert.deepEqual(failures, []);
  console.log(JSON.stringify({ result: "passed", checks: ["full terminal-page recovery", "left/right/center scroll", "background lock and restoration", "three-dot waiting", "non-interactive time pill", "no ratings", "copy feedback alignment", "header chat hover", "real answer Markdown", "optimistic replacement", "1920/1024/390"], screenshots: output }));
} finally {
  await browser.close();
}

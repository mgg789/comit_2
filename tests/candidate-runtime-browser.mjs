/** Synthetic API pagination regressions exercise the actual candidate runtime module. */
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext();
const base = process.env.CANDIDATE_URL || "http://127.0.0.1:5177/candidate/";
let mode = "cursor", calls = [];
await context.route("**/api/**", async route => {
  const url = new URL(route.request().url());
  const respond = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  if (!url.pathname.endsWith("/candidate/recruitment")) return respond({}, 503);
  calls.push({ cursor: url.searchParams.get("cursor"), offset: url.searchParams.get("offset") });
  const index = calls.length;
  if (mode === "cursor") return respond(index === 1 ? { items: [{ id: "a" }], has_more: true, next_cursor: "next" } : { items: [{ id: "b" }], has_more: false });
  if (mode === "offset") return respond(index === 1 ? { items: [{ id: "a" }], has_more: true } : { items: [{ id: "b" }], has_more: false });
  if (mode === "empty") return respond({ items: [], has_more: true, next_cursor: "next" });
  if (mode === "duplicate") return respond({ items: [{ id: "a" }], has_more: true, next_cursor: `next-${index}` });
  if (mode === "missing") return respond(index === 1 ? { items: [{ id: "a" }], has_more: true, next_cursor: "next" } : { items: [{ id: "b" }], has_more: true });
  return respond({ items: [{ id: "a" }], has_more: true, next_cursor: "next" });
});
try {
  const page = await context.newPage();
  await page.goto(base);
  const read = () => page.evaluate(async () => { const runtime = await import("/candidate/src/runtime.ts"); try { return { ids: (await runtime.readInvitations()).map(item => item.id) }; } catch (error) { return { error: error.message }; } });
  let value = await read(); assert.deepEqual(value.ids, ["a", "b"]); assert.equal(calls[1].cursor, "next");
  mode = "offset"; calls = []; value = await read(); assert.deepEqual(value.ids, ["a", "b"]); assert.equal(calls[1].offset, "1");
  for (const scenario of ["empty", "duplicate", "missing"]) { mode = scenario; calls = []; value = await read(); assert.ok(value.error, scenario); assert.ok(calls.length <= 2, scenario); }
  console.log("PASS candidate pagination: cursor, offset, empty/duplicate/missing cursor stop safely");
} finally { await browser.close(); }

import assert from "node:assert/strict";
import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";

const root = path.resolve(import.meta.dirname, "..");
const output = path.join(root, "dist");
/** List generated pages recursively, preserving their relative URLs. */
async function pages(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(entry => entry.isDirectory()
    ? pages(path.join(directory, entry.name))
    : entry.name.endsWith(".html") ? [path.join(directory, entry.name)] : []));
  return nested.flat();
}

test("HonKit builds all guidance pages and same-origin assets under /help/", async () => {
  const summary = await readFile(path.join(root, "book/SUMMARY.md"), "utf8");
  const chapters = [...summary.matchAll(/\]\(([^)]+\.md)\)/g)].map(match => match[1]);
  assert.equal(chapters.length, 17);
  assert.equal(new Set(chapters).size, chapters.length);
  for (const chapter of chapters) {
    const filename = chapter === "README.md" ? "index.html" : chapter.replace(/\.md$/, ".html");
    const html = await readFile(path.join(output, filename), "utf8");
    assert.match(html, /book-summary/);
    assert.match(html, /Справка Comit/);
    assert.match(html, /gitbook/);
  }
  const files = await pages(output);
  assert.equal(files.length, 17);
  for (const file of files) {
    const html = await readFile(file, "utf8");
    // The gateway's global CSP forbids inline executable scripts.
    assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)[^>]*>\s*[^<\s]/i);
    for (const match of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
      const url = match[1].split(/[?#]/)[0];
      if (!url || url.startsWith("/") || /^[a-z]+:/i.test(url)) continue;
      const target = path.resolve(path.dirname(file), decodeURIComponent(url));
      assert.ok(target === output || target.startsWith(output + path.sep), `${url} must stay in the generated book`);
      const info = await stat(target);
      assert.ok(info.isFile() || info.isDirectory() && (await stat(path.join(target, "index.html"))).isFile(), `${file}: missing ${url}`);
    }
  }
  const support = await readFile(path.join(output, "general/support.html"), "utf8");
  assert.match(support, /https:\/\/t\.me\/droidje/);
  assert.match(support, /\/candidate\/#\/chats/);
  assert.match(support, /\/employer\/#\/chat/);
  const search = JSON.parse(await readFile(path.join(output, "search_index.json"), "utf8"));
  assert.ok(Object.keys(search).length > 0, "native HonKit search index is generated");
});

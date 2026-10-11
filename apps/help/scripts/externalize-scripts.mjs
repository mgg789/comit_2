import { createHash } from "node:crypto";
import { readFile, writeFile, readdir, mkdir } from "node:fs/promises";
import path from "node:path";

const output = path.resolve(import.meta.dirname, "../dist");
const scripts = path.join(output, "gitbook/comit-init");
await mkdir(scripts, { recursive: true });
// The legacy theme executes fetched page scripts through inline jQuery evaluation.
// Use browser navigation instead, preserving the native book UI under strict CSP.
const navigation = path.join(scripts, "full-page-navigation.js");
await writeFile(navigation, `document.addEventListener("click", function(event) {
  var anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
  if (!anchor || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || anchor.target || anchor.hasAttribute("download")) return;
  var href = anchor.getAttribute("href");
  if (!href || href.charAt(0) === "#" || !anchor.closest(".book")) return;
  // Keep normal links and native search result links on a full document navigation.
  event.stopPropagation();
}, true);`);
/** Keep HonKit initialization order while satisfying the gateway's self-only CSP. */
async function externalize(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) { await externalize(file); continue; }
    if (!entry.name.endsWith(".html")) continue;
    let html = await readFile(file, "utf8");
    const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
    if (inline.length !== 2) throw new Error(`Unexpected HonKit initialization in ${file}`);
    for (const [tag, source] of inline) {
      if (!source.includes("gitbook.page.hasChanged") && !source.includes("__init__restoreLastSidebarState")) {
        throw new Error(`Unknown inline script in ${file}`);
      }
      const name = createHash("sha256").update(source).digest("hex") + ".js";
      const target = path.join(scripts, name);
      await writeFile(target, source);
      const url = path.relative(path.dirname(file), target).split(path.sep).join("/");
      html = html.replace(tag, `<script src="${url}"></script>`);
    }
    // HonKit rewrites root-relative Markdown links as book paths. Preserve the
    // three explicit app mounts through a private build-time URL scheme.
    html = html.replace(/href="comit:(\/[^"]*)"/g, (_, route) => {
      if (!["/", "/candidate/#/chats", "/employer/#/chat"].includes(route)) throw new Error(`Unknown app route ${route}`);
      return `href="${route}"`;
    });
    const navigationUrl = path.relative(path.dirname(file), navigation).split(path.sep).join("/");
    html = html.replace("</body>", `<script src="${navigationUrl}"></script></body>`);
    await writeFile(file, html);
  }
}
await externalize(output);

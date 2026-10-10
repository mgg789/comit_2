// Render the original Figma 24cc7.png artwork before MIME delivery: mail clients do not support CSS blur/transform reliably.
// Run from any directory: PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node apps/api/scripts/render-email-background.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const source = readFileSync(new URL("./email-background-source.png", import.meta.url));
const src = `data:image/png;base64,${source.toString("base64")}`;

// Bake the soft background into both layers instead of relying on email CSS filters.
// Geometry and the source PNG are from Figma 556:16768; no design screenshot is embedded.
const html = `<style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #010101; }
  .card { position: relative; width: 578px; height: 363px; overflow: hidden; background: #010101; }
  .layer { position: absolute; display: flex; align-items: center; justify-content: center; filter: blur(23.15px); }
  .one { left: -156.31px; top: -171.64px; width: 950.591px; height: 459.823px; }
  .two { left: -203.57px; top: -138.54px; width: 977.154px; height: 725.909px; }
  .transform { transform: rotate(-171.41deg) scaleY(-1); flex: none; }
  .image { position: relative; }
  .one .image { width: 911.941px; height: 327.336px; filter: blur(23.15px); }
  .two .image { width: 897.849px; height: 598.566px; }
  .image img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
  .image:after { content: ""; position: absolute; inset: 0; background: rgba(0,0,0,.35); }
</style>
<div class="card">
  <div class="layer two"><div class="transform"><div class="image"><img src="${src}"></div></div></div>
  <div class="layer one"><div class="transform"><div class="image"><img src="${src}"></div></div></div>
</div>`;

const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 578, height: 363 }, deviceScaleFactor: 2 });
  await page.setContent(html);
  await page.evaluate(() => Promise.all(Array.from(document.images, image => image.decode())));
  await page.screenshot({ path: fileURLToPath(new URL("../src/auth/email-assets/context-background.png", import.meta.url)) });
} finally {
  await browser.close();
}

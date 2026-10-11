import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import { ResumeAccordion } from "../src/resume-accordion";

test("closed resume sections expose a keyboard button and inert associated content", () => {
  const html = renderToStaticMarkup(<ResumeAccordion title="Experience"><a href="/project">Project</a></ResumeAccordion>);
  assert.match(html, /type="button"/);
  assert.match(html, /aria-expanded="false"/);
  const controlled = html.match(/aria-controls="([^"]+)"/)?.[1];
  assert.ok(controlled);
  assert.ok(html.includes(`id="${controlled}"`));
  assert.match(html, /inert=""/);
  assert.match(html, /height:0/);
  assert.match(html, /aria-hidden="true"/);
});

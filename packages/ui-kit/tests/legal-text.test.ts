import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { LegalText } from "../src/legal-text";

test("canonical legal text has readable headings and safe navigable links", () => {
  const html = renderToStaticMarkup(createElement(LegalText, { text: '# Согласие\n\n**Важно** [Политика](privacy-policy.md) [unsafe](javascript:alert(1)) <script>bad</script>' }));
  assert.match(html, /<h1>Согласие<\/h1>/);
  assert.match(html, /<strong>Важно<\/strong>/);
  assert.match(html, /href="#\/legal\/privacy-policy"/);
  assert.doesNotMatch(html, /href="javascript:|<script>/);
});

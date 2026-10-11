import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { glassEffect } from "../src/glass-effect";
import { showcaseSalary } from "../../../apps/welcome/src/showcase-format";

test("Chromium refraction requires SVG backdrop support", () => {
  assert.equal(glassEffect("", "Chrome/140 Safari/537.36", true), "refractive");
  assert.equal(glassEffect("", "Chrome/140 Safari/537.36", false), "frosted");
});

test("Safari, Firefox and iOS Chromium use frosted fallback", () => {
  for (const browser of ["Version/26 Safari/605", "Firefox/140", "CriOS/140 Safari/605", "EdgiOS/140 Safari/605"]) {
    assert.equal(glassEffect("", browser, true), "frosted");
  }
});

test("one query flag restores classic glass regardless of renderer", () => {
  assert.equal(glassEffect("?glass=classic", "Chrome/140", true), "classic");
  assert.equal(glassEffect("?other=1&glass=classic", "Safari/605", false), "classic");
  assert.equal(glassEffect("?glass=unknown", "Chrome/140", true), "refractive");
});

test("public candidate salary does not invent an upper cap", () => {
  assert.equal(showcaseSalary({ from_rub: 120000, to_rub: null }), "от 120к ₽");
  assert.equal(showcaseSalary({ from_rub: 120000, to_rub: 180000 }), "120–180к ₽");
});

// Both rendering paths must preserve the contrast-bearing selected tab and composer.
test("experimental glass excludes foreground contrast surfaces in normal and fallback rules", () => {
  const css = readFileSync(new URL("../src/design.css", import.meta.url), "utf8");
  const selectors = css.split("\n").filter(line => line.includes(":root[data-comit-glass]:not"));
  assert.equal(selectors.length, 2);
  for (const selector of selectors) {
    assert.ok(selector.includes(".glass:not(.glass-surface):not(.nav-item.selected):not(.communication-composer)"));
  }
});

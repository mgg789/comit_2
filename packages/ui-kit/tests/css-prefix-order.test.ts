import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

/**
 * The production CSS optimizer keeps only the last of `backdrop-filter` / `-webkit-backdrop-filter`
 * when they are adjacent. With the standard property first, Chrome lost every glass blur in builds.
 */
test("prefixed backdrop-filter always precedes the standard one", () => {
  const root = join(import.meta.dirname, "..", "..", "..");
  const dirs = [join(root, "packages", "ui-kit", "src"), ...readdirSync(join(root, "apps")).map((app) => join(root, "apps", app, "src"))];
  const offenders: string[] = [];
  for (const dir of dirs) {
    let files: string[] = [];
    try { files = readdirSync(dir).filter((file) => file.endsWith(".css")); } catch { continue; }
    for (const file of files) {
      const lines = readFileSync(join(dir, file), "utf8").split(/\r?\n/);
      lines.forEach((line, index) => {
        if (/^\s*backdrop-filter:/.test(line) && /^\s*-webkit-backdrop-filter:/.test(lines[index + 1] ?? ""))
          offenders.push(`${file}:${index + 1}`);
      });
    }
  }
  assert.deepEqual(offenders, []);
});

import assert from "node:assert/strict";
import { test } from "node:test";
import { canonicalRepositoryClaimUrl } from "../src/profiles/repository-url.js";

test("repository evidence binding normalizes suffixes without accepting a different repository or credentials", () => {
  for (const suffix of ["", "/", ".git", ".git/"]) assert.equal(canonicalRepositoryClaimUrl(`https://github.com/acme/project${suffix}`), "https://github.com/acme/project");
  assert.notEqual(canonicalRepositoryClaimUrl("https://github.com/acme/other"), "https://github.com/acme/project");
  for (const link of ["https://user@github.com/acme/project", "http://github.com/acme/project", "https://github.com/acme/project?secret=x", "https://github.com/acme/project#file"]) assert.equal(canonicalRepositoryClaimUrl(link), null);
});

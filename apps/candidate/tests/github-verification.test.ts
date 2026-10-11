import assert from "node:assert/strict";
import { test } from "node:test";
import { githubEvidenceLabel, githubProjectEvidence, githubRepositoryUrl, githubVerificationRequest } from "../src/github-verification-model";

test("GitHub verification accepts repository roots and canonicalizes familiar suffixes", () => {
  assert.equal(githubRepositoryUrl("https://github.com/acme/project.git/"), "https://github.com/acme/project");
  for (const url of ["javascript:alert(1)", "http://github.com/acme/project", "https://github.com/acme", "https://github.com/acme/project/blob/main/file", "https://github.com/acme/project?token=x", "https://github.com/acme/project#readme", "https://user@github.com/acme/project", "https://github.com.evil.test/acme/project"]) assert.equal(githubRepositoryUrl(url), null);
});

test("repository request binds the saved project, description and revision without evidence assertions", () => {
  const project = { id: "claim-id", title: "Adapter", link: "https://github.com/acme/project.git", description: "Built API adapter" };
  assert.deepEqual(githubVerificationRequest(project, 4), { expected_revision: 4, claim_kind: "projects", claim_id: "claim-id", repository_url: "https://github.com/acme/project", contribution_narrative: "Built API adapter" });
  assert.throws(() => githubVerificationRequest({ ...project, description: " " }, 4));
  assert.throws(() => githubVerificationRequest({ ...project, id: undefined }, 4));
});

test("only matching durable evidence displays code verification and never claims authorship", () => {
  const project = { id: "claim-id", title: "Adapter", link: "https://github.com/acme/project/", description: "Built API adapter" };
  const evidence = { input_hash: "hash", status: "partial" as const, checked_at: "2026-10-11T00:00:00Z", provenance: ["repo:github.com:public_git_tree"], verified_value: { url: "https://github.com/acme/project", contribution_narrative: project.description, repository_nonempty: true, authorship_proven: false } };
  assert.equal(githubProjectEvidence(project, { "external:repository:projects:claim-id": evidence }), evidence);
  assert.match(githubEvidenceLabel(evidence), /Авторство не подтверждено/);
  assert.equal(githubProjectEvidence({ ...project, description: "Changed" }, { "external:repository:projects:claim-id": evidence }), undefined);
  assert.equal(githubProjectEvidence({ ...project, link: "https://github.com/acme/other" }, { "external:repository:projects:claim-id": evidence }), undefined);
  assert.equal(githubEvidenceLabel(undefined), "Пока не проверен");
});

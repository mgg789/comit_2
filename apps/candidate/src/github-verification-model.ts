import type { CandidateClaimEvidence, CandidateDeclaredNarrative, CandidateVerificationRequest } from "@comit/shared";

/** Only a canonical public GitHub repository root can bind evidence to a saved claim. */
export function githubRepositoryUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "github.com" || url.username || url.password || url.port || url.search || url.hash) return null;
    const path = url.pathname.replace(/\/$/, "").replace(/\.git$/, "");
    if (!/^\/[A-Za-z0-9._~-]+\/[A-Za-z0-9._~-]+$/.test(path) || path.split("/").some(part => part === "." || part === "..")) return null;
    return `https://github.com${path}`;
  } catch { return null; }
}

/** Reuse stored server evidence only when its URL and narrative still match this project. */
export function githubProjectEvidence(project: CandidateDeclaredNarrative, evidence: Record<string, CandidateClaimEvidence>): CandidateClaimEvidence | undefined {
  const result = project.id ? evidence[`external:repository:projects:${project.id}`] : undefined;
  const value = result?.verified_value;
  if (!value || typeof value !== "object" || Array.isArray(value) || value.url !== githubRepositoryUrl(project.link) || value.contribution_narrative !== project.description) return undefined;
  return result;
}

/** A Git tree proves public code exists; no status here asserts candidate authorship. */
export function githubEvidenceLabel(evidence: CandidateClaimEvidence | undefined): string {
  const value = evidence?.verified_value;
  if (evidence?.status === "pending") return "Проверка пока недоступна. Можно повторить позже.";
  if (evidence?.status === "rejected") return "Репозиторий не прошёл проверку.";
  if ((evidence?.status === "partial" || evidence?.status === "confirmed") && value && typeof value === "object" && !Array.isArray(value) && value.repository_nonempty === true) return "Публичный репозиторий с кодом проверен. Авторство не подтверждено.";
  return "Пока не проверен";
}

/** Build a candidate-session request from saved declarations, never from client evidence. */
export function githubVerificationRequest(project: CandidateDeclaredNarrative, revision: number): CandidateVerificationRequest {
  const url = githubRepositoryUrl(project.link);
  if (!project.id || !url || !project.description?.trim()) throw new Error("repository_claim_incomplete");
  return { expected_revision: revision, claim_kind: "projects", claim_id: project.id, repository_url: url, contribution_narrative: project.description };
}

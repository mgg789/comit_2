import type { CandidateProfileFields, ResumeImportResult } from "@comit/shared";

/** Fill only empty editable declarations; existing work and trusted identity remain untouched. */
export function mergeResumeSuggestions(current: CandidateProfileFields, imported: ResumeImportResult["fields"]): CandidateProfileFields {
  const result = { ...current };
  const assign = <K extends keyof ResumeImportResult["fields"]>(key: K) => {
    const previous = current[key], next = imported[key];
    if (next !== undefined && (previous === undefined || previous === null || previous === "" || Array.isArray(previous) && !previous.length)) Object.assign(result, { [key]: next });
  };
  (["display_name", "city", "phone", "description", "primary_direction", "declared_grade", "work_types", "experience", "projects", "achievements"] as const).forEach(assign);
  return result;
}

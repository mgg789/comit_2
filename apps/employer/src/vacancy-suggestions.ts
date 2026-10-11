import { ApiError } from "@comit/api-client";
import type { AgentsDraftFields_Input, AgentsDraftView, AgentsJobView } from "@comit/shared";

type Requirements = NonNullable<AgentsDraftFields_Input["stacks"]>;

/** Measure description length only; this is not a semantic quality or hiring score. */
export function descriptionLength(text: string): { length: number; tone: "short" | "medium" | "detailed"; label: string } {
  const length = Array.from(text.trim()).length;
  return { length, ...(length < 160
    ? { tone: "short" as const, label: "Мало деталей: опишите задачи и нужные навыки" }
    : length < 400
      ? { tone: "medium" as const, label: "Добавьте контекст проекта и ожидаемый опыт" }
      : { tone: "detailed" as const, label: "Достаточный объём для подбора требований" }) };
}

/** Omit unchanged groups so saving a form does not turn inferred or absent values into manual ones. */
export function changedRequirements(current: AgentsDraftView | undefined, groups: {
  stacks: Requirements; domens: Requirements; soft_skills: Requirements;
}, manuallyEdited: ReadonlySet<string> = new Set()): Pick<AgentsDraftFields_Input, "stacks" | "domens" | "soft_skills"> {
  const result: Pick<AgentsDraftFields_Input, "stacks" | "domens" | "soft_skills"> = {};
  for (const key of ["stacks", "domens", "soft_skills"] as const) {
    const previous = current?.fields[key] ?? {};
    const next = groups[key];
    if (manuallyEdited.has(key) || Object.keys(previous).length !== Object.keys(next).length ||
        Object.entries(next).some(([name, grade]) => previous[name] !== grade)) result[key] = next;
  }
  return result;
}

/** Minimal owner-authenticated transport; all mutations remain server-side revision guarded. */
export interface VacancySuggestionApi {
  enrichVacancy(draftId: string, revision: number): Promise<AgentsJobView>;
  getAgentJob(jobId: string): Promise<AgentsJobView>;
  getVacancy(draftId: string): Promise<AgentsDraftView>;
  patchVacancy(draftId: string, body: { expected_revision: number; fields: AgentsDraftFields_Input }): Promise<AgentsDraftView>;
}

/** Wait for the existing cached durable job; bounded manual fallback fences late worker writes. */
export async function suggestVacancyRequirements(api: VacancySuggestionApi, saved: AgentsDraftView,
  options: { polls?: number; wait?: () => Promise<void> } = {},
): Promise<{ draft: AgentsDraftView; suggested: boolean }> {
  const wait = options.wait ?? (() => new Promise<void>(resolve => setTimeout(resolve, 1000)));
  let succeeded = false;
  try {
    let job = await api.enrichVacancy(saved.draft_id, saved.revision);
    for (let poll = 0; poll < (options.polls ?? 30); poll++) {
      if (["succeeded", "failed", "obsolete"].includes(job.state)) break;
      await wait();
      job = await api.getAgentJob(job.job_id);
    }
    succeeded = job.state === "succeeded";
  } catch {
    // Provider/network failure is optional. Still fence an ambiguously accepted job below.
  }
  // Always fence the revision before review: approving alone does not bump a draft revision.
  // A worker racing this patch either finishes first or becomes obsolete; no late overwrite.
  for (let attempt = 0; attempt < 3; attempt++) {
    const latest = await api.getVacancy(saved.draft_id);
    for (const key of Object.keys(latest.fields) as Array<keyof AgentsDraftView["fields"]>) {
      const previous = saved.fields[key];
      const next = latest.fields[key];
      if (JSON.stringify(previous) === JSON.stringify(next)) continue;
      if (!["stacks", "domens", "soft_skills"].includes(key) || latest.field_sources?.[key]?.source !== "laya") {
        throw new Error("Черновик изменился в другом окне. Откройте его заново, чтобы сохранить все правки.");
      }
    }
    try {
      const draft = await api.patchVacancy(saved.draft_id, { expected_revision: latest.revision, fields: {} });
      return { draft, suggested: succeeded };
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 409 || attempt === 2) throw error;
    }
  }
  throw new Error("Не удалось обновить черновик. Повторите проверку вакансии.");
}

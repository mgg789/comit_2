import type { RecruitmentView } from "@comit/shared";
import { ACTION_ROW_BUTTON, ActionRow, Avatar, Button, MatchBadge, StateMessage, cx } from "@comit/ui-kit";
import { Link } from "react-router";
import { api, describeError } from "../api";
import { PageHeader } from "../layout/PageHeader";
import { matchPercent } from "../lib/explain";
import { candidateLabel } from "../lib/format";
import { useCandidateNames } from "../lib/use-candidate-names";
import { useResource } from "../lib/use-resource";
import { FUNNEL_STEPS, RECRUITMENT_STATE_LABEL, TERMINAL_STATES, funnelStep } from "./labels";

/** Keyset pages of 50 read for the overview; older processes beyond this cap are listed per vacancy. */
const MAX_PAGES = 6;

interface ResumesData {
  items: RecruitmentView[];
  truncated: boolean;
  /** candidate_id → server match percent from the vacancy's saved shortlist (absent for showcase-only pairs). */
  scores: Map<string, number>;
}

async function loadProcesses(): Promise<ResumesData> {
  const items: RecruitmentView[] = [];
  let cursor: string | undefined;
  let truncated = true;
  for (let page = 0; page < MAX_PAGES; page++) {
    const result = await api.getRecruitmentList("employer", 50, cursor);
    items.push(...result.items);
    if (!result.has_more || !result.next_cursor) {
      truncated = false;
      break;
    }
    cursor = result.next_cursor;
  }
  // Match percent lives on the saved feed card of each vacancy; one request per distinct vacancy.
  const scores = new Map<string, number>();
  const drafts = [...new Set(items.map((process) => process.draft_id))];
  const saved = await Promise.allSettled(drafts.map((draftId) => api.getSaved(draftId, 100)));
  for (const result of saved) {
    if (result.status !== "fulfilled") continue;
    for (const entry of result.value.items) if (entry.card) scores.set(entry.candidate_id, matchPercent(entry.card.explanation));
  }
  return { items, truncated, scores };
}

/** What the employer should do next, shown on the row button. */
function nextStepLabel(process: RecruitmentView): string {
  const allowed = new Set(process.allowed_actions);
  if (process.interviews.some((interview) => interview.state === "scheduled" && Date.parse(interview.starts_at) < Date.now()) && allowed.has("interview_complete")) {
    return "Заполнить ОС";
  }
  if (TERMINAL_STATES.has(process.state)) return "Обратная связь";
  if (allowed.has("interview_propose") && process.interviews.length === 0) return "Профиль кандидата";
  return "Открыть профиль";
}

/** «Резюме» (Figma «РЕЗЮМЕ»): every hiring process with its funnel stepper, full-width light rows. */
export function ResumesPage() {
  const processes = useResource(loadProcesses, []);
  const items = processes.data?.items ?? [];
  const names = useCandidateNames(items.map((process) => process.candidate_id));

  return (
    <>
      <PageHeader title="Резюме" backTo="/vacancies" backLabel="К вакансиям" />
      {processes.status === "loading" && items.length === 0 && <StateMessage kind="loading" title="Загружаем процессы найма" />}
      {processes.status === "error" && (
        <StateMessage
          kind="error"
          title="Не удалось загрузить процессы"
          description={describeError(processes.error)}
          action={
            <Button variant="secondary" onClick={processes.reload}>
              Повторить
            </Button>
          }
        />
      )}
      {processes.status === "ready" && items.length === 0 && (
        <StateMessage kind="empty" title="Процессов найма пока нет" description="Лайкните кандидатов в ленте вакансии и пригласите их — здесь появится воронка по каждому." />
      )}
      <ul className="flex flex-col gap-4 lg:gap-[1rem]">
        {items.map((process) => {
          const name = candidateLabel(names.get(process.candidate_id) ?? "Кандидат", process.candidate_id);
          const current = funnelStep(process.state, process.interviews.length > 0);
          const negative = TERMINAL_STATES.has(process.state) && process.state !== "hired";
          const score = processes.data?.scores.get(process.candidate_id);
          return (
            <li key={process.id}>
              <ActionRow
                badge={score !== undefined ? <MatchBadge percent={score} /> : undefined}
                leading={<Avatar name={name} size={70} />}
                title={name}
                subtitle={<span className="text-on-chip/60">{process.terms?.vacancy_title ?? "Шортлист без приглашения"}</span>}
                action={
                  <Link to={`/candidates/${process.candidate_id}?vacancy=${process.draft_id}`} className={ACTION_ROW_BUTTON}>
                    {nextStepLabel(process)}
                  </Link>
                }
              >
                <ol aria-label={RECRUITMENT_STATE_LABEL[process.state]} className="flex flex-wrap items-center gap-2 lg:gap-[1.25rem]">
                  {FUNNEL_STEPS.map((step, index) => {
                    const done = index < current;
                    const isCurrent = index === current;
                    return (
                      <li
                        key={step}
                        aria-current={isCurrent ? "step" : undefined}
                        className={cx(
                          "inline-flex h-9 items-center gap-2 rounded-chip px-3 text-sm font-medium lg:h-[2.75rem] lg:gap-[0.5rem] lg:px-[1.25rem] lg:text-[1.25rem]",
                          done && "bg-dark-button text-white",
                          isCurrent && !negative && "bg-step-current text-success-text",
                          isCurrent && negative && "bg-step-negative text-light",
                          !done && !isCurrent && "bg-light-strong text-step-future-text",
                        )}
                      >
                        <span aria-hidden className="size-2 rounded-full bg-current lg:size-[0.6875rem]" />
                        {isCurrent && negative ? RECRUITMENT_STATE_LABEL[process.state] : step}
                      </li>
                    );
                  })}
                </ol>
              </ActionRow>
            </li>
          );
        })}
      </ul>
      {processes.data?.truncated && <p className="mt-6 text-center text-sm text-muted">Показаны последние 300 процессов.</p>}
    </>
  );
}

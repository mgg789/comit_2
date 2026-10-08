import type { RecruitmentView } from "@comit/shared";
import { Avatar, Button, StateMessage, cx } from "@comit/ui-kit";
import { Link } from "react-router";
import { api, describeError } from "../api";
import { PageHeader } from "../layout/PageHeader";
import { useCandidateNames } from "../lib/use-candidate-names";
import { useResource } from "../lib/use-resource";
import { FUNNEL_STEPS, RECRUITMENT_STATE_LABEL, TERMINAL_STATES, funnelStep } from "./labels";

/** Keyset pages of 50 read for the overview; older processes beyond this cap are listed per vacancy. */
const MAX_PAGES = 6;

async function loadProcesses(): Promise<{ items: RecruitmentView[]; truncated: boolean }> {
  const items: RecruitmentView[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < MAX_PAGES; page++) {
    const result = await api.getRecruitmentList("employer", 50, cursor);
    items.push(...result.items);
    if (!result.has_more || !result.next_cursor) return { items, truncated: false };
    cursor = result.next_cursor;
  }
  return { items, truncated: true };
}

/** What the employer should do next, shown on the row button. */
function nextStepLabel(process: RecruitmentView): string {
  const allowed = new Set(process.allowed_actions);
  if (process.interviews.some((interview) => interview.state === "scheduled" && Date.parse(interview.starts_at) < Date.now()) && allowed.has("interview_complete")) {
    return "Заполнить ОС";
  }
  if (allowed.has("offer_send") && process.interviews.some((interview) => interview.outcome === "passed")) return "Сделать оффер";
  if (allowed.has("interview_propose") && process.interviews.length === 0) return "Назначить собеседование";
  if (TERMINAL_STATES.has(process.state)) return "Открыть профиль";
  return "Профиль кандидата";
}

/** «Резюме»: every hiring process of the employer with its funnel position (Figma «РЕЗЮМЕ»). */
export function ResumesPage() {
  const processes = useResource(loadProcesses, []);
  const items = processes.data?.items ?? [];
  const names = useCandidateNames(items.map((process) => process.candidate_id));

  return (
    <>
      <PageHeader title="Резюме" />
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
      <ul className="flex flex-col gap-4">
        {items.map((process) => {
          const name = names.get(process.candidate_id) ?? "Кандидат";
          const reached = funnelStep(process.state, process.interviews.length > 0);
          const closed = TERMINAL_STATES.has(process.state) && process.state !== "hired";
          return (
            <li key={process.id} className="flex flex-col gap-4 rounded-[20px] bg-light p-4 text-on-light lg:flex-row lg:items-center lg:gap-6">
              <div className="flex min-w-0 items-center gap-4 lg:w-[340px] lg:shrink-0">
                <Avatar name={name} size={52} />
                <div className="min-w-0">
                  <p className="truncate font-display text-lg leading-tight sm:text-xl">{name}</p>
                  <p className="truncate text-sm font-semibold text-faint">{process.terms?.vacancy_title ?? "Шортлист без приглашения"}</p>
                </div>
              </div>
              <ol aria-label={RECRUITMENT_STATE_LABEL[process.state]} className="flex flex-1 flex-wrap items-center gap-2">
                {FUNNEL_STEPS.map((step, index) => {
                  const done = index < reached;
                  const current = index === reached;
                  return (
                    <li
                      key={step}
                      aria-current={current ? "step" : undefined}
                      className={cx(
                        "inline-flex items-center gap-2 rounded-chip px-3 py-1 text-sm",
                        done && "bg-dark-button text-white",
                        current && !closed && "bg-success-soft text-success-text",
                        current && closed && "bg-danger-soft text-white",
                        !done && !current && "bg-black/5 text-faint",
                      )}
                    >
                      <span aria-hidden className="size-1.5 rounded-full bg-current" />
                      {current && closed ? RECRUITMENT_STATE_LABEL[process.state] : step}
                    </li>
                  );
                })}
              </ol>
              <Link
                to={`/candidates/${process.candidate_id}?vacancy=${process.draft_id}`}
                className="inline-flex h-12 shrink-0 items-center justify-center rounded-field bg-dark-button px-6 font-semibold text-white hover:brightness-125"
              >
                {nextStepLabel(process)}
              </Link>
            </li>
          );
        })}
      </ul>
      {processes.data?.truncated && <p className="mt-6 text-center text-sm text-muted">Показаны последние 300 процессов.</p>}
    </>
  );
}

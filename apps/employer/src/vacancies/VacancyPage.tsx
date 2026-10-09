import type { AgentsDraftView, InterviewView, RecruitmentView, SavedCard } from "@comit/shared";
import { ACTION_ROW_BUTTON, ActionRow, Avatar, Button, CountBadge, MatchBadge, Panel, ProgressBar, StateMessage, cx, Reveal, StaggerItem, StaggerList } from "@comit/ui-kit";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, describeError, isUnauthorized } from "../api";
import { useSession } from "../auth/session";
import { HeaderAction, PageHeader } from "../layout/PageHeader";
import { GRADE_LABEL, matchPercent, requirementWeightLabel } from "../lib/explain";
import { SPECIALIZATION_LABEL, WORK_TYPE_LABEL, candidateLabel, formatInterviewTime, vacancyBadge, vacancyTitle } from "../lib/format";
import { useResource } from "../lib/use-resource";
import { FeedDeck } from "./FeedDeck";
import { PortraitGlow } from "./PortraitGlow";
import { VacancyFormDialog } from "./VacancyFormDialog";

/** Poll interval while a publication handoff is in flight. */
const PUBLICATION_POLL_MS = 3000;

/**
 * Vacancy workspace (Figma «СТРАНИЦА ВАКАНСИИ»): 948px left column of #191917 panels starting under the
 * title text, the swipe deck on the right.
 */
export function VacancyPage() {
  const { draftId = "" } = useParams();
  const navigate = useNavigate();
  const vacancy = useResource(() => api.getVacancy(draftId), [draftId]);
  const draft = vacancy.data;
  // Matching-side lists exist only after the first accepted publication.
  const everPublished = draft?.publication?.status === "accepted" || draft?.business_status === "closed";
  const saved = useResource(
    () => (everPublished ? api.getSaved(draftId, 30) : Promise.resolve({ items: [], limit: 30, offset: 0, has_more: false })),
    [draftId, everPublished],
  );
  const [editing, setEditing] = useState(false);
  const publishing = draft !== undefined && (draft.state === "creating" || draft.publication?.status === "pending");

  useEffect(() => {
    if (!publishing) return;
    const timer = window.setTimeout(vacancy.reload, PUBLICATION_POLL_MS);
    return () => window.clearTimeout(timer);
  }, [publishing, vacancy.reload, draft?.revision, draft?.publication?.status]);

  if (!draft) {
    return (
      <>
        <PageHeader title="К вакансиям" backTo="/vacancies" />
        {vacancy.status === "error" ? (
          <StateMessage
            kind="error"
            title="Вакансия недоступна"
            description={describeError(vacancy.error)}
            action={
              <Button variant="secondary" onClick={vacancy.reload}>
                Повторить
              </Button>
            }
          />
        ) : (
          <StateMessage kind="loading" title="Загружаем вакансию" />
        )}
      </>
    );
  }

  const open = draft.business_status === "open" && !draft.archived;
  return (
    <>
      <PageHeader
        title="К вакансиям"
        backTo="/vacancies"
        actions={
          !draft.archived && (
            <HeaderAction icon="pencil" onClick={() => setEditing(true)}>
              Редактировать
            </HeaderAction>
          )
        }
      />
      <div className="grid items-start gap-6 lg:grid-cols-[59.25rem_minmax(0,1fr)] lg:gap-x-[2.125rem] lg:pl-[5.375rem]">
        <div className="flex min-w-0 flex-col gap-6 lg:gap-[2rem]">
          <Reveal>
            <VacancyDataPanel draft={draft} onChanged={vacancy.reload} onEdit={() => setEditing(true)} />
          </Reveal>
          {everPublished && (
            <>
              <Reveal delay={0.08}>
                <SavedStrip draftId={draftId} saved={saved.data?.items ?? []} status={saved.status} />
              </Reveal>
              <Reveal delay={0.16}>
                <InterviewsPanel draftId={draftId} saved={saved.data?.items ?? []} />
              </Reveal>
            </>
          )}
        </div>
        <Reveal delay={0.12} className="lg:sticky lg:top-[2rem] lg:pt-[1rem]">
          {open && draft.publication?.status === "accepted" ? (
            <FeedDeck draftId={draftId} onLiked={saved.reload} />
          ) : (
            <PublicationState draft={draft} publishing={publishing} onEdit={() => setEditing(true)} />
          )}
        </Reveal>
      </div>
      {editing && (
        <VacancyFormDialog
          initial={draft}
          onChanged={vacancy.reload}
          onPublished={() => {
            setEditing(false);
            vacancy.reload();
          }}
          onClose={() => {
            setEditing(false);
            vacancy.reload();
            if (draft.archived) navigate("/vacancies");
          }}
        />
      )}
    </>
  );
}

/** Right column when the feed is not available yet: draft, publishing, closed or failed. */
function PublicationState({ draft, publishing, onEdit }: { draft: AgentsDraftView; publishing: boolean; onEdit: () => void }) {
  if (publishing) {
    return <StateMessage kind="loading" title="Публикуем вакансию" description="Matching принимает снимок вакансии. Лента откроется, когда подбор будет готов." />;
  }
  if (draft.business_status === "closed" || draft.archived) {
    return <StateMessage kind="empty" title="Вакансия закрыта" description="Лента и новый подбор для неё не ведутся. Процессы с приглашёнными кандидатами сохранены." />;
  }
  const failed = draft.publication?.status === "failed" || draft.state === "failed";
  return (
    <StateMessage
      kind={failed ? "error" : "empty"}
      title={failed ? "Публикация не прошла" : "Вакансия ещё не опубликована"}
      description={failed ? "Проверьте поля и опубликуйте снова — черновик сохранён." : "Подбор и лента кандидатов начнутся после публикации."}
      action={<Button onClick={onEdit}>{failed ? "Проверить и опубликовать" : "Заполнить и опубликовать"}</Button>}
    />
  );
}

/** «Данные вакансии» (design 948 wide, #191917, radius 30): terms in 67px fields and weighted requirements. */
function VacancyDataPanel({ draft, onChanged, onEdit }: { draft: AgentsDraftView; onChanged: () => void; onEdit: () => void }) {
  const { expire } = useSession();
  const navigate = useNavigate();
  const [confirm, setConfirm] = useState<"close" | "archive" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { fields } = draft;
  const badge = vacancyBadge(draft);
  const requirements = useMemo(
    () =>
      [...Object.entries(fields.stacks ?? {}), ...Object.entries(fields.domens ?? {}), ...Object.entries(fields.soft_skills ?? {})].sort(
        (left, right) => right[1] - left[1],
      ),
    [fields.stacks, fields.domens, fields.soft_skills],
  );
  const subtitle = [vacancyTitle(draft), fields.class ? SPECIALIZATION_LABEL[fields.class] : null].filter(Boolean).join(" · ");

  async function run(action: "close" | "archive") {
    setBusy(true);
    setError(null);
    try {
      if (action === "close") await api.closeVacancy(draft.draft_id, draft.revision);
      else await api.archiveVacancy(draft.draft_id, draft.revision);
      setConfirm(null);
      if (action === "archive") navigate("/vacancies");
      else onChanged();
    } catch (failure) {
      if (isUnauthorized(failure)) expire();
      setError(describeError(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel tone="dialog" className="flex flex-col p-5 lg:px-[1.875rem] lg:pt-[2.5rem] lg:pb-[2rem]">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-2xl leading-none lg:text-[2rem]">Данные вакансии</h2>
          <p className="mt-3 text-lg font-semibold tracking-[-0.0255rem] text-white/60 lg:mt-[1.25rem] lg:text-[1.5rem]">{subtitle}</p>
        </div>
        <span className="rounded-chip bg-field px-3 py-1.5 text-sm lg:text-[1.125rem]">{badge.label}</span>
      </header>
      <dl className="mt-6 grid grid-cols-2 gap-x-[1.625rem] gap-y-4 lg:mt-[2rem] lg:gap-y-[1.6875rem]">
        <ReadonlyValue label="Вилка З/П от, тыс ₽" value={fields.zp_min == null ? "—" : String(Math.round(fields.zp_min / 1000))} muted />
        <ReadonlyValue label="до, тыс ₽" value={fields.zp_max == null ? "—" : String(Math.round(fields.zp_max / 1000))} muted />
        <ReadonlyValue value={fields.type ? WORK_TYPE_LABEL[fields.type] : "Формат не указан"} />
        <ReadonlyValue value={fields.city ?? "Город не указан"} />
        {fields.daily && <ReadonlyValue className="col-span-2" value={fields.daily} />}
      </dl>
      {requirements.length > 0 ? (
        <ul className="mt-6 flex flex-col gap-5 lg:mt-[2.5rem] lg:gap-[1.5rem]">
          {requirements.map(([key, level]) => (
            <li key={key} className="flex flex-col gap-3 lg:gap-[1.25rem]">
              <div className="flex justify-between gap-3 text-lg font-medium tracking-[-0.045rem] lg:text-[1.5rem]">
                <span>{key}</span>
                <span>{requirementWeightLabel(level)}</span>
              </div>
              <ProgressBar value={level / 7} label={`${key}: вес ${level} из 7`} className="h-3 bg-field lg:h-[1rem]" />
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-6 text-base text-white/60 lg:mt-[2.5rem] lg:text-[1.25rem]">
          Требования к навыкам не заданы: подбор идёт по направлению и рейтингу кандидатов в категории.
        </p>
      )}
      {fields.bio && <p className="mt-6 text-base whitespace-pre-line text-subtle lg:text-[1.25rem]">{fields.bio}</p>}
      {draft.missing_requirements && draft.missing_requirements.length > 0 && (
        <p className="mt-4 text-sm text-danger-soft">
          Для публикации не хватает полей.{" "}
          <button type="button" className="cursor-pointer underline" onClick={onEdit}>
            Заполнить
          </button>
        </p>
      )}
      {!draft.archived && (
        <footer className="mt-6 flex flex-wrap gap-3 border-t border-white/10 pt-4 lg:mt-[2rem]">
          {confirm === null ? (
            <>
              {draft.business_status === "open" && (
                <Button variant="secondary" size="sm" onClick={() => setConfirm("close")}>
                  Закрыть вакансию
                </Button>
              )}
              <Button variant="ghost" size="sm" onClick={() => setConfirm("archive")}>
                В архив
              </Button>
            </>
          ) : (
            <>
              <span className="self-center text-sm text-subtle">
                {confirm === "close" ? "Закрыть вакансию? Подбор остановится." : "Убрать вакансию в архив?"}
              </span>
              <Button variant="danger" size="sm" loading={busy} onClick={() => void run(confirm)}>
                Подтвердить
              </Button>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirm(null)}>
                Отмена
              </Button>
            </>
          )}
          {error && (
            <p role="alert" className="w-full text-sm text-danger-soft">
              {error}
            </p>
          )}
        </footer>
      )}
    </Panel>
  );
}

function ReadonlyValue({ label, value, muted, className }: { label?: string; value: string; muted?: boolean; className?: string }) {
  return (
    <div className={cx("flex flex-col gap-3 lg:gap-[1rem]", className)}>
      {label && <dt className="text-lg font-semibold tracking-[-0.0255rem] lg:text-[1.5rem]">{label}</dt>}
      <dd
        className={cx(
          "flex h-12 items-center rounded-chip bg-field px-4 text-base font-medium tracking-[-0.0375rem] lg:h-[4.1875rem] lg:px-[1.25rem] lg:text-[1.25rem]",
          muted ? "text-subtle" : "text-text",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

/** «Понравились в ленте»: saved shortlist tiles (design 308×229, radius 20) with the server match percent. */
function SavedStrip({ draftId, saved, status }: { draftId: string; saved: SavedCard[]; status: "loading" | "ready" | "error" }) {
  return (
    <Panel tone="dialog" className="flex flex-col gap-5 p-5 lg:gap-[1.875rem] lg:px-[1.875rem] lg:pt-[2.5rem] lg:pb-[2.5rem]">
      <h2 className="flex items-center gap-3 font-display text-2xl leading-none lg:text-[2rem]">
        Понравились в ленте
        {saved.length > 0 && <CountBadge value={saved.length} />}
      </h2>
      {status === "loading" && saved.length === 0 && <p className="text-base text-white/60">Загружаем…</p>}
      {status === "error" && <p className="text-base text-danger-soft">Не удалось загрузить список.</p>}
      {status === "ready" && saved.length === 0 && <p className="text-base text-white/60 lg:text-[1.25rem]">Лайкнутые в ленте кандидаты появятся здесь.</p>}
      {saved.length > 0 && (
        <StaggerList step={0.05} className="-mx-1 flex snap-x gap-[1.625rem] overflow-x-auto px-1 pb-1">
          {saved.map((entry) => {
            const name = entry.card ? candidateLabel(entry.card.display_name, entry.candidate_id) : "Кандидат";
            return (
              <StaggerItem key={entry.candidate_id} className="shrink-0 snap-start">
                <Link
                  to={`/candidates/${entry.candidate_id}?vacancy=${draftId}`}
                  className="relative flex h-[10rem] w-[13rem] flex-col justify-end overflow-hidden rounded-[1.25rem] bg-surface-dialog p-4 transition-transform duration-150 hover:-translate-y-0.5 lg:h-[14.3125rem] lg:w-[19.25rem] lg:px-[1.625rem] lg:pb-[1.75rem]"
                >
                  <PortraitGlow name={name} />
                  {entry.card && <MatchBadge percent={matchPercent(entry.card.explanation)} className="absolute top-0 left-0" />}
                  <span className="relative font-display text-xl leading-none tracking-[0.035rem] lg:text-[1.75rem]">{name}</span>
                  {entry.card && (
                    <span className="relative mt-2 text-sm font-extrabold tracking-[0.0225rem] text-white/70 lg:mt-[0.75rem] lg:text-[1.125rem]">
                      {SPECIALIZATION_LABEL[entry.card.category.specialization].toLowerCase()} {GRADE_LABEL[entry.card.category.grade].toLowerCase()}
                    </span>
                  )}
                </Link>
              </StaggerItem>
            );
          })}
        </StaggerList>
      )}
    </Panel>
  );
}

type UpcomingInterview = { process: RecruitmentView; interview: InterviewView };

/** «Назначенные собеседования»: proposed/scheduled interviews as light 120px rows (design). */
function InterviewsPanel({ draftId, saved }: { draftId: string; saved: SavedCard[] }) {
  const processes = useResource(() => api.getEmployerRecruitment(draftId, 50), [draftId]);
  const names = useMemo(
    () => new Map(saved.flatMap((entry) => (entry.card ? [[entry.candidate_id, candidateLabel(entry.card.display_name, entry.candidate_id)] as const] : []))),
    [saved],
  );
  const upcoming: UpcomingInterview[] = (processes.data?.items ?? [])
    .flatMap((process) =>
      process.interviews.filter((interview) => interview.state === "scheduled" || interview.state === "proposed").map((interview) => ({ process, interview })),
    )
    .sort((left, right) => Date.parse(left.interview.starts_at) - Date.parse(right.interview.starts_at));

  return (
    <Panel tone="dialog" className="flex flex-col gap-5 p-5 lg:gap-[1.875rem] lg:px-[1.875rem] lg:pt-[2.5rem] lg:pb-[2.5rem]">
      <h2 className="flex items-center gap-3 font-display text-2xl leading-none lg:text-[2rem]">
        Назначенные собеседования
        {upcoming.length > 0 && <CountBadge value={upcoming.length} />}
      </h2>
      {processes.status === "error" && <p className="text-base text-danger-soft">Не удалось загрузить собеседования.</p>}
      {processes.status === "ready" && upcoming.length === 0 && (
        <p className="text-base text-white/60 lg:text-[1.25rem]">Пока нет. Пригласите кандидата из «Понравились в ленте» и предложите время.</p>
      )}
      <StaggerList className="flex flex-col gap-4 lg:gap-[2rem]">
        {upcoming.map(({ process, interview }) => {
          const name = names.get(process.candidate_id) ?? "Кандидат";
          return (
            <StaggerItem key={interview.id}>
              <ActionRow
                leading={<Avatar name={name} size={70} />}
                title={name}
                subtitle={
                  <span className="first-letter:uppercase">
                    {formatInterviewTime(interview)}
                    {interview.state === "proposed" && " · ждёт подтверждения"}
                  </span>
                }
                action={
                  <Link to={process.chat_available ? `/chats/${process.id}` : `/candidates/${process.candidate_id}?vacancy=${draftId}`} className={ACTION_ROW_BUTTON}>
                    {process.chat_available ? "Перейти в чат" : "К процессу"}
                  </Link>
                }
              />
            </StaggerItem>
          );
        })}
      </StaggerList>
    </Panel>
  );
}

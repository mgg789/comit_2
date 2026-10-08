import type { AgentsDraftView, InterviewView, RecruitmentView, SavedCard } from "@comit/shared";
import { Avatar, Button, MatchBadge, Panel, ProgressBar, StateMessage, cx } from "@comit/ui-kit";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, describeError, isUnauthorized } from "../api";
import { useSession } from "../auth/session";
import { PageHeader } from "../layout/PageHeader";
import { GRADE_LABEL, matchPercent, requirementWeightLabel } from "../lib/explain";
import { SPECIALIZATION_LABEL, WORK_TYPE_LABEL, formatInterviewTime, vacancyBadge, vacancyTitle } from "../lib/format";
import { useResource } from "../lib/use-resource";
import { FeedDeck } from "./FeedDeck";
import { VacancyFormDialog } from "./VacancyFormDialog";

/** Poll interval while a publication handoff is in flight. */
const PUBLICATION_POLL_MS = 3000;

/** Vacancy workspace (Figma «СТРАНИЦА ВАКАНСИИ»): data and requirements, feed, liked candidates, interviews. */
export function VacancyPage() {
  const { draftId = "" } = useParams();
  const navigate = useNavigate();
  const vacancy = useResource(() => api.getVacancy(draftId), [draftId]);
  const vacancyData = vacancy.data;
  // Matching-side lists exist only after the first accepted publication.
  const everPublished = vacancyData?.publication?.status === "accepted" || vacancyData?.business_status === "closed";
  const saved = useResource(
    () => (everPublished ? api.getSaved(draftId, 30) : Promise.resolve({ items: [], limit: 30, offset: 0, has_more: false })),
    [draftId, everPublished],
  );
  const [editing, setEditing] = useState(false);
  const draft = vacancy.data;
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
            <Button variant="accent" size="sm" shape="pill" onClick={() => setEditing(true)}>
              Редактировать
            </Button>
          )
        }
      />
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,680px)_minmax(0,1fr)] lg:gap-10">
        <div className="flex min-w-0 flex-col gap-6">
          <VacancyDataPanel draft={draft} onChanged={vacancy.reload} onEdit={() => setEditing(true)} />
          {everPublished && (
            <>
              <SavedStrip draftId={draftId} saved={saved.data?.items ?? []} status={saved.status} />
              <InterviewsPanel draftId={draftId} saved={saved.data?.items ?? []} />
            </>
          )}
        </div>
        <div className="lg:sticky lg:top-40">
          {open && draft.publication?.status === "accepted" ? (
            <FeedDeck draftId={draftId} onLiked={saved.reload} />
          ) : (
            <PublicationState draft={draft} publishing={publishing} onEdit={() => setEditing(true)} />
          )}
        </div>
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
      description={
        failed
          ? "Проверьте поля и опубликуйте снова — черновик сохранён."
          : "Подбор и лента кандидатов начнутся после публикации."
      }
      action={<Button onClick={onEdit}>{failed ? "Проверить и опубликовать" : "Заполнить и опубликовать"}</Button>}
    />
  );
}

/** «Данные вакансии»: mandatory terms and weighted requirements, plus close/archive controls. */
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
    <Panel tone="solid" className="flex flex-col gap-5 p-5 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-xl sm:text-2xl">Данные вакансии</h2>
          <p className="mt-1 text-lg text-subtle">{subtitle}</p>
        </div>
        <span className="rounded-chip bg-surface-raised px-3 py-1.5 text-sm">{badge.label}</span>
      </header>
      <dl className="grid grid-cols-2 gap-x-5 gap-y-3">
        <ReadonlyValue label="Вилка З/П от, тыс ₽" value={fields.zp_min == null ? "—" : String(Math.round(fields.zp_min / 1000))} />
        <ReadonlyValue label="до, тыс ₽" value={fields.zp_max == null ? "—" : String(Math.round(fields.zp_max / 1000))} />
        <ReadonlyValue value={fields.type ? WORK_TYPE_LABEL[fields.type] : "Формат не указан"} />
        <ReadonlyValue value={fields.city ?? "Город не указан"} />
        {fields.daily && <ReadonlyValue className="col-span-2" value={fields.daily} />}
      </dl>
      {requirements.length > 0 ? (
        <ul className="flex flex-col gap-4">
          {requirements.map(([key, level]) => (
            <li key={key} className="flex flex-col gap-2">
              <div className="flex justify-between gap-3 text-lg">
                <span>{key}</span>
                <span>{requirementWeightLabel(level)}</span>
              </div>
              <ProgressBar value={level / 7} label={`${key}: вес ${level} из 7`} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted">
          Требования к навыкам не заданы: подбор идёт по направлению и рейтингу кандидатов в категории.
        </p>
      )}
      {fields.bio && <p className="text-base whitespace-pre-line text-subtle">{fields.bio}</p>}
      {draft.missing_requirements && draft.missing_requirements.length > 0 && (
        <p className="text-sm text-danger-soft">
          Для публикации не хватает полей.{" "}
          <button type="button" className="cursor-pointer underline" onClick={onEdit}>
            Заполнить
          </button>
        </p>
      )}
      {!draft.archived && (
        <footer className="flex flex-wrap gap-3 border-t border-white/10 pt-4">
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

function ReadonlyValue({ label, value, className }: { label?: string; value: string; className?: string }) {
  return (
    <div className={cx("flex flex-col gap-2", className)}>
      {label && <dt className="text-base font-semibold">{label}</dt>}
      <dd className="rounded-field bg-field px-4 py-3 text-base">{value}</dd>
    </div>
  );
}

/** «Понравились в ленте»: the vacancy's saved shortlist with server match percent. */
function SavedStrip({ draftId, saved, status }: { draftId: string; saved: SavedCard[]; status: "loading" | "ready" | "error" }) {
  return (
    <Panel tone="solid" className="flex flex-col gap-4 p-5 sm:p-6">
      <h2 className="flex items-center gap-2 font-display text-xl sm:text-2xl">
        Понравились в ленте
        {saved.length > 0 && <span className="inline-flex size-7 items-center justify-center rounded-full bg-light font-sans text-sm text-on-light">{saved.length}</span>}
      </h2>
      {status === "loading" && saved.length === 0 && <p className="text-sm text-muted">Загружаем…</p>}
      {status === "error" && <p className="text-sm text-danger-soft">Не удалось загрузить список.</p>}
      {status === "ready" && saved.length === 0 && <p className="text-sm text-muted">Лайкнутые в ленте кандидаты появятся здесь.</p>}
      {saved.length > 0 && (
        <ul className="-mx-1 flex snap-x gap-4 overflow-x-auto px-1 pb-2">
          {saved.map((entry) => (
            <li key={entry.candidate_id} className="shrink-0 snap-start">
              <Link
                to={`/candidates/${entry.candidate_id}?vacancy=${draftId}`}
                className="relative flex h-[164px] w-[220px] flex-col justify-end overflow-hidden rounded-[20px] bg-surface-raised p-4 transition-transform duration-150 hover:-translate-y-0.5"
              >
                <Avatar name={entry.card?.display_name ?? entry.candidate_id} size={220} shape="square" className="absolute inset-0 opacity-60" />
                {entry.card && <MatchBadge percent={matchPercent(entry.card.explanation)} className="absolute top-0 left-0" />}
                <span className="relative font-display text-xl leading-tight">{entry.card?.display_name ?? "Кандидат"}</span>
                {entry.card && (
                  <span className="relative text-sm font-semibold text-subtle">
                    {SPECIALIZATION_LABEL[entry.card.category.specialization]} {GRADE_LABEL[entry.card.category.grade]}
                  </span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

type UpcomingInterview = { process: RecruitmentView; interview: InterviewView };

/** «Назначенные собеседования»: proposed/scheduled interviews of this vacancy's processes. */
function InterviewsPanel({ draftId, saved }: { draftId: string; saved: SavedCard[] }) {
  const processes = useResource(() => api.getEmployerRecruitment(draftId, 50), [draftId]);
  const names = useMemo(() => new Map(saved.flatMap((entry) => (entry.card ? [[entry.candidate_id, entry.card.display_name] as const] : []))), [saved]);
  const upcoming: UpcomingInterview[] = (processes.data?.items ?? [])
    .flatMap((process) =>
      process.interviews.filter((interview) => interview.state === "scheduled" || interview.state === "proposed").map((interview) => ({ process, interview })),
    )
    .sort((left, right) => Date.parse(left.interview.starts_at) - Date.parse(right.interview.starts_at));

  return (
    <Panel tone="solid" className="flex flex-col gap-4 p-5 sm:p-6">
      <h2 className="flex items-center gap-2 font-display text-xl sm:text-2xl">
        Назначенные собеседования
        {upcoming.length > 0 && <span className="inline-flex size-7 items-center justify-center rounded-full bg-light font-sans text-sm text-on-light">{upcoming.length}</span>}
      </h2>
      {processes.status === "error" && <p className="text-sm text-danger-soft">Не удалось загрузить собеседования.</p>}
      {processes.status === "ready" && upcoming.length === 0 && (
        <p className="text-sm text-muted">Пока нет. Пригласите кандидата из «Понравились в ленте» и предложите время.</p>
      )}
      <ul className="flex flex-col gap-4">
        {upcoming.map(({ process, interview }) => {
          const name = names.get(process.candidate_id) ?? "Кандидат";
          return (
            <li key={interview.id} className="flex flex-wrap items-center gap-4 rounded-[20px] bg-light p-4 text-on-light">
              <Avatar name={name} size={52} />
              <div className="min-w-0 flex-1">
                <p className="font-display text-lg leading-tight">{name}</p>
                <p className="text-base first-letter:uppercase">
                  {formatInterviewTime(interview)}
                  {interview.state === "proposed" && " · ждёт подтверждения"}
                </p>
              </div>
              {process.chat_available ? (
                <Link to={`/chats/${process.id}`} className="inline-flex h-12 items-center rounded-field bg-dark-button px-6 font-semibold text-white hover:brightness-125">
                  Перейти в чат
                </Link>
              ) : (
                <Link to={`/resumes`} className="inline-flex h-12 items-center rounded-field bg-dark-button px-6 font-semibold text-white hover:brightness-125">
                  К процессу
                </Link>
              )}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

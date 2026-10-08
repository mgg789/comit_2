import type { AgentsDraftView, Grade, PublicCandidateCard, SearchFilters, SearchPage, ShowcaseMismatchWarning, Specialization } from "@comit/shared";
import { GRADES, SPECIALIZATIONS } from "@comit/shared";
import { ApiError } from "@comit/api-client";
import { Avatar, Button, Dialog, Icon, SelectField, StateMessage } from "@comit/ui-kit";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, describeError, isUnauthorized, newIdempotencyKey } from "../api";
import { useSession } from "../auth/session";
import { PageHeader } from "../layout/PageHeader";
import { GRADE_LABEL } from "../lib/explain";
import { SPECIALIZATION_LABEL, formatSalaryRange, vacancyTitle } from "../lib/format";
import { useResource } from "../lib/use-resource";
import { loadAllVacancies } from "../vacancies/VacanciesPage";

const PAGE_SIZE = 20;

const SEARCH_MODE_LABEL: Record<SearchPage["mode"], string> = {
  browse: "Весь пул",
  lexical: "Поиск по тексту",
  hybrid: "Текст + смысловой поиск",
};

/** Parse the structured 409 that asks to confirm a like despite unmet strict filters. */
function mismatchWarning(error: unknown): ShowcaseMismatchWarning | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const body = error.body;
  if (!body || typeof body !== "object" || !("detail" in body)) return null;
  const detail = body.detail;
  if (!detail || typeof detail !== "object") return null;
  const record: Record<string, unknown> = { ...detail };
  const { code, candidate_id, profile_revision, vacancy_revision, warnings, mismatch_token } = record;
  if (
    code !== "strict_filter_confirmation_required" ||
    typeof candidate_id !== "string" ||
    typeof profile_revision !== "number" ||
    typeof vacancy_revision !== "number" ||
    typeof mismatch_token !== "string" ||
    !Array.isArray(warnings)
  ) {
    return null;
  }
  return {
    code,
    candidate_id,
    profile_revision,
    vacancy_revision,
    mismatch_token,
    warnings: warnings.filter((item): item is string => typeof item === "string"),
  };
}

const isSpecialization = (value: string): value is Specialization => SPECIALIZATIONS.some((item) => item === value);
const isGrade = (value: string): value is Grade => GRADES.some((item) => item === value);

interface Query {
  q: string;
  specialization: Specialization | "";
  grade: Grade | "";
  vacancyId: string;
}

/**
 * «Соискатели» showcase (Figma «ВИТРИНА»): employer-initiated discovery over the ready pool —
 * the inverse LHH mechanic. A like puts the candidate into the selected vacancy's shortlist.
 */
export function ShowcasePage() {
  const { expire } = useSession();
  const vacancies = useResource(loadAllVacancies, []);
  const openVacancies = (vacancies.data?.items ?? []).filter((vacancy) => vacancy.business_status === "open");
  const [draftQuery, setDraftQuery] = useState<Query>({ q: "", specialization: "", grade: "", vacancyId: "" });
  const [query, setQuery] = useState<Query>(draftQuery);
  const [page, setPage] = useState<SearchPage | null>(null);
  const [items, setItems] = useState<PublicCandidateCard[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);

  const filters = (value: Query): SearchFilters => ({
    ...(value.specialization ? { classes: [value.specialization] } : {}),
    ...(value.grade ? { grades: [value.grade] } : {}),
    ...(value.vacancyId ? { vacancy_id: vacancyIdOf(openVacancies, value.vacancyId) } : {}),
  });

  async function search(value: Query, cursor: string | null) {
    const current = ++generation.current;
    setStatus("loading");
    setError(null);
    try {
      const result = await api.searchCandidates({ q: value.q.trim() || undefined, filters: filters(value), limit: PAGE_SIZE, cursor });
      if (current !== generation.current) return;
      setPage(result);
      setItems((previous) => (cursor ? [...previous, ...result.items] : result.items));
      setStatus("ready");
    } catch (failure) {
      if (current !== generation.current) return;
      if (isUnauthorized(failure)) expire();
      // A changed pool invalidates the cursor (409): restart from the first page.
      if (cursor && failure instanceof ApiError && failure.status === 409) return void search(value, null);
      setError(describeError(failure));
      setStatus("error");
    }
  }

  // Search runs for each submitted query (not on every keystroke in the draft fields).
  useEffect(() => {
    void search(query, null);
  }, [query]);

  function submit(event: FormEvent) {
    event.preventDefault();
    setQuery(draftQuery);
  }

  const selectedVacancy = openVacancies.find((vacancy) => vacancy.draft_id === query.vacancyId) ?? null;

  return (
    <>
      <PageHeader
        title="Соискатели"
        actions={
          <form onSubmit={submit} role="search" className="bg-accent-gradient flex h-[3.1875rem] items-center gap-2 rounded-pill pr-1 pl-5">
            <label className="sr-only" htmlFor="showcase-q">
              Поиск: имя, роль, стек
            </label>
            <input
              id="showcase-q"
              value={draftQuery.q}
              onChange={(event) => setDraftQuery((previous) => ({ ...previous, q: event.target.value }))}
              placeholder="Поиск: имя, роль, стек"
              className="w-48 bg-transparent text-lg text-white outline-none placeholder:text-white/80 sm:w-64"
            />
            <button type="submit" aria-label="Искать" className="inline-flex size-[2.6875rem] cursor-pointer items-center justify-center rounded-full bg-light text-on-light">
              <Icon name="magnifier" size={22} />
            </button>
          </form>
        }
      />
      <form onSubmit={submit} className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1fr_2fr_auto]">
        <SelectField
          tone="light"
          aria-label="Специализация"
          placeholder="Специализация"
          value={draftQuery.specialization}
          onChange={(value) => setDraftQuery((previous) => ({ ...previous, specialization: isSpecialization(value) ? value : "" }))}
          options={SPECIALIZATIONS.map((value) => ({ value, label: SPECIALIZATION_LABEL[value] }))}
        />
        <SelectField
          tone="light"
          aria-label="Грейд"
          placeholder="Грейд"
          value={draftQuery.grade}
          onChange={(value) => setDraftQuery((previous) => ({ ...previous, grade: isGrade(value) ? value : "" }))}
          options={GRADES.map((value) => ({ value, label: GRADE_LABEL[value] }))}
        />
        <div className="hidden lg:block" />
        <SelectField
          tone="light"
          aria-label="Вакансия"
          placeholder={openVacancies.length ? "Вакансия (фильтры и лайк)" : "Нет открытых вакансий"}
          value={draftQuery.vacancyId}
          onChange={(value) => setDraftQuery((previous) => ({ ...previous, vacancyId: value }))}
          options={openVacancies.map((vacancy) => ({ value: vacancy.draft_id, label: vacancyTitle(vacancy) }))}
        />
        <Button type="submit" size="md">
          Применить
        </Button>
      </form>
      {page && (
        <p className="mb-4 text-sm text-muted">
          {SEARCH_MODE_LABEL[page.mode]} · найдено {page.total}
          {page.fallback_reason && " · смысловой поиск недоступен, показан текстовый"}
          {selectedVacancy && ` · с учётом требований «${vacancyTitle(selectedVacancy)}»`}
        </p>
      )}

      {status === "loading" && items.length === 0 && <StateMessage kind="loading" title="Ищем кандидатов" />}
      {status === "error" && (
        <StateMessage
          kind="error"
          title="Поиск недоступен"
          description={error}
          action={
            <Button variant="secondary" onClick={() => void search(query, null)}>
              Повторить
            </Button>
          }
        />
      )}
      {status === "ready" && items.length === 0 && <StateMessage kind="empty" title="Никого не нашли" description="Ослабьте фильтры или измените запрос." />}

      <ul className="flex flex-col gap-4">
        {items.map((candidate) => (
          <li key={candidate.candidate_id}>
            <CandidateRow candidate={candidate} vacancy={selectedVacancy} />
          </li>
        ))}
      </ul>
      {page?.has_more && page.next_cursor && (
        <div className="mt-6 flex justify-center">
          <Button variant="secondary" loading={status === "loading"} onClick={() => void search(query, page.next_cursor)}>
            Показать ещё
          </Button>
        </div>
      )}
    </>
  );
}

/** Canonical vacancy UUID for the showcase filter (draft and vacancy IDs coincide by contract). */
function vacancyIdOf(vacancies: AgentsDraftView[], draftId: string): string {
  return vacancies.find((vacancy) => vacancy.draft_id === draftId)?.vacancy_id ?? draftId;
}

/** One showcase row: identity, public facts, PDF and «в вакансию» like with strict-filter confirmation. */
function CandidateRow({ candidate, vacancy }: { candidate: PublicCandidateCard; vacancy: AgentsDraftView | null }) {
  const { expire } = useSession();
  const [liked, setLiked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [warning, setWarning] = useState<ShowcaseMismatchWarning | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = useRef(newIdempotencyKey());

  async function like(mismatchToken: string | null) {
    if (!vacancy) return;
    setBusy(true);
    setError(null);
    try {
      await api.likeShowcaseCandidate(vacancy.draft_id, {
        candidate_id: candidate.candidate_id,
        profile_revision: candidate.profile_revision,
        idempotency_key: key.current,
        mismatch_token: mismatchToken,
      });
      setLiked(true);
      setWarning(null);
    } catch (failure) {
      if (isUnauthorized(failure)) expire();
      const mismatch = mismatchWarning(failure);
      if (mismatch) setWarning(mismatch);
      else setError(describeError(failure));
    } finally {
      setBusy(false);
    }
  }

  const facts = [`${SPECIALIZATION_LABEL[candidate.category.specialization]} ${GRADE_LABEL[candidate.category.grade]}`, candidate.city, formatSalaryRange(candidate.salary.from_rub, candidate.salary.to_rub)];
  const profileLink = `/candidates/${candidate.candidate_id}${vacancy ? `?vacancy=${vacancy.draft_id}` : ""}`;
  return (
    <div className="flex flex-col gap-4 rounded-[1.25rem] border border-glass-border bg-surface p-4 sm:flex-row sm:items-center">
      <Link to={profileLink} className="flex min-w-0 items-center gap-4 sm:w-[22.5rem] sm:shrink-0">
        <Avatar name={candidate.display_name} size={52} />
        <div className="min-w-0">
          <p className="truncate font-display text-lg leading-tight sm:text-xl">{candidate.display_name}</p>
          <p className="truncate text-sm font-semibold text-muted">
            {SPECIALIZATION_LABEL[candidate.category.specialization]} {GRADE_LABEL[candidate.category.grade]}
            {candidate.category.grade_status === "self_reported" && " · грейд заявлен"}
          </p>
        </div>
      </Link>
      <ul className="flex flex-1 flex-wrap gap-2">
        {facts.map((fact) => (
          <li key={fact} className="rounded-chip bg-surface-raised px-4 py-2 text-base">
            {fact}
          </li>
        ))}
      </ul>
      <div className="flex shrink-0 items-center gap-3">
        <a
          href={candidate.resume_url}
          target="_blank"
          rel="noreferrer"
          aria-label={`PDF резюме: ${candidate.display_name}`}
          className="inline-flex size-[3.25rem] items-center justify-center rounded-field border-2 border-white/40 text-text hover:border-white/80"
        >
          <Icon name="resumes" size={24} />
        </a>
        <button
          type="button"
          disabled={!vacancy || busy || liked}
          title={vacancy ? (liked ? "Уже в шортлисте вакансии" : `В шортлист «${vacancyTitle(vacancy)}»`) : "Выберите вакансию в фильтре"}
          aria-label={liked ? "В шортлисте" : "Добавить в шортлист вакансии"}
          onClick={() => void like(null)}
          className="bg-accent-gradient inline-flex size-[3.25rem] cursor-pointer items-center justify-center rounded-field text-3xl text-white transition-[filter] duration-150 hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {liked ? "♥" : "+"}
        </button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-danger-soft sm:basis-full">
          {error}
        </p>
      )}
      {warning && (
        <Dialog title="Кандидат не проходит фильтры вакансии" showTitle onClose={() => setWarning(null)}>
          <p className="text-sm text-subtle">Строгие условия вакансии не выполнены:</p>
          <ul className="my-3 list-disc pl-5 text-sm">
            {warning.warnings.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <p className="mb-4 text-sm text-muted">Можно всё равно добавить в шортлист — отметка о несоответствии сохранится.</p>
          <div className="flex gap-3">
            <Button loading={busy} onClick={() => void like(warning.mismatch_token)} className="flex-1">
              Всё равно добавить
            </Button>
            <Button variant="ghost" onClick={() => setWarning(null)}>
              Отмена
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

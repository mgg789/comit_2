import { testingPresenceLabel, testingSubjectLabel } from "./testing-evidence";
import { createPortal } from "react-dom";
import { Dialog } from "@comit/ui-kit/app-shell";
import { ProcessCommand } from "./communications";
import { recruitmentStage, recruitmentTileAction } from "./recruitment-stage";
import { rejectionFeedbackError } from "@comit/ui-kit/rejection-feedback";
import { RejectionFeedback } from "@comit/ui-kit/design";
import { rejectionReasonCategories, type RejectionReasonCategory } from "@comit/shared";
import { InlineSearch } from "@comit/ui-kit/design";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ApiError } from "@comit/api-client";
import {
  GRADES,
  SPECIALIZATIONS,
  type PublicCandidateCard,
  type RecruitmentView,
  type ShowcaseMismatchWarning,
  type ShowcaseLikeRequest,
  type EmployerStatisticStage,
} from "@comit/shared";
import { api, asset, errorText, salary, useResource } from "./runtime";
import { Action, Field, Icon, PageTitle, Reveal, Select, State } from "./ui";
import { stackChoices } from "./labels";
import { candidateName } from "./candidate-name";
import { SHOWCASE_SORTS, candidateCode, isShowcaseSort, orderCandidates } from "./showcase-order";
import { usePersistentState, useClearPersistentState } from "@comit/ui-kit/ui-state";
import "./discovery.css";

const evidenceLabels: Record<string, string> = {
  declared: "Заявлено",
  confirmed: "Подтверждено",
  verified: "Проверено",
  unconfirmed: "Не подтверждено",
  rejected: "Отклонено",
  pending: "Ожидает проверки",
  self_reported: "Указано кандидатом",
  approved: "Подтверждено",
};
const roleLabels: Record<string, string> = {
  backend: "Backend-разработчик",
  frontend: "Frontend-разработчик",
  fullstack: "Fullstack-разработчик",
  mobile: "Mobile-разработчик",
  devops: "DevOps",
  data: "Data-специалист",
  ml: "ML-специалист",
  qa: "Тестировщик",
};
const gradeLabels: Record<string, string> = {
  intern: "Стажёр",
  junior: "Junior",
  middle: "Middle",
  senior: "Senior",
};
const statusLabels: Record<string, string> = {
  sent: "Приглашение",
  viewed: "Просмотрено",
  accepted: "Принято",
  declined: "Отклонено кандидатом",
  rejected: "Отклонено",
  offer_sent: "Оффер",
  hired: "Приём",
  offer_declined: "Оффер отклонён",
  withdrawn: "Отозвано",
  expired: "Предложение отозвано по сроку",
};
const stageLabels: Record<EmployerStatisticStage, string> = {
  delivered: "Выдано в подбор",
  viewed: "Просмотрено",
  liked: "Лайки",
  disliked: "Пропущено",
  invited: "Приглашено",
  accepted: "Принято приглашений",
  interviewed: "Собеседования",
  offered: "Офферы",
  hired: "Наняты",
};
type CandidateIdentityView = Pick<
  PublicCandidateCard,
  "candidate_id" | "display_name" | "category" | "city" | "salary"
>;
type Navigation = { navigate: (path: string) => void };

/** Only the owner-authenticated same-origin API may deliver a public resume PDF. */
export function protectedResumeUrl(value: string): string | null {
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin &&
      url.pathname.startsWith("/api/") &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function AnonymousAvatar({
  name,
  large = false,
}: {
  name: string;
  large?: boolean;
}) {
  return (
    <div
      className={`discovery-avatar ${large ? "large" : ""}`}
      aria-label="Анонимный профиль"
    >
      <svg viewBox="0 0 100 100" aria-hidden="true">
        <circle cx="50" cy="33" r="18" />
        <path d="M15 92c0-28 15-40 35-40s35 12 35 40" />
      </svg>
      <span className="sr-only">{name}</span>
    </div>
  );
}
function CandidateIdentity({
  candidate,
}: {
  candidate: CandidateIdentityView;
}) {
  return (
    <>
      <AnonymousAvatar name={candidateName(candidate.display_name)} />
      <div className="discovery-identity">
        <h3 title={candidateName(candidate.display_name)}>{candidateName(candidate.display_name)}</h3>
        <p>
          {roleLabels[candidate.category.specialization]}{" "}
          {gradeLabels[candidate.category.grade]}
        </p>
      </div>
    </>
  );
}

function mismatchFrom(error: unknown): ShowcaseMismatchWarning | null {
  if (
    !(error instanceof ApiError) ||
    error.status !== 409 ||
    !error.body ||
    typeof error.body !== "object" ||
    !("detail" in error.body)
  )
    return null;
  const detail = error.body.detail;
  if (
    !detail ||
    typeof detail !== "object" ||
    !("code" in detail) ||
    detail.code !== "strict_filter_confirmation_required" ||
    !("mismatch_token" in detail) ||
    typeof detail.mismatch_token !== "string" ||
    !("warnings" in detail) ||
    !Array.isArray(detail.warnings) ||
    !detail.warnings.every((item) => typeof item === "string") ||
    !("candidate_id" in detail) ||
    typeof detail.candidate_id !== "string" ||
    !("profile_revision" in detail) ||
    typeof detail.profile_revision !== "number" ||
    !("vacancy_revision" in detail) ||
    typeof detail.vacancy_revision !== "number"
  )
    return null;
  return {
    code: detail.code,
    mismatch_token: detail.mismatch_token,
    warnings: detail.warnings,
    candidate_id: detail.candidate_id,
    profile_revision: detail.profile_revision,
    vacancy_revision: detail.vacancy_revision,
  };
}

/** Real public pool with canonical filters and revision-bound shortlist confirmation. */
export function Showcase({
  navigate,
  focusSearch = false,
}: Navigation & { focusSearch?: boolean }) {
  const searchInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const focus = () => searchInput.current?.focus();
    if (focusSearch) focus();
    window.addEventListener("employer:focus-candidate-search", focus);
    return () =>
      window.removeEventListener("employer:focus-candidate-search", focus);
  }, [focusSearch]);
  const [q, setQ] = usePersistentState("showcase.q", "");
  const [query, setQuery] = usePersistentState("showcase.query", "");
  const [specialization, setSpecialization] = usePersistentState("showcase.specialization", "",
    (value): value is string => typeof value === "string" && (value === "" || SPECIALIZATIONS.some(item => item === value)));
  const [grade, setGrade] = usePersistentState("showcase.grade", "",
    (value): value is string => typeof value === "string" && (value === "" || GRADES.some(item => item === value)));
  const [stack, setStack] = usePersistentState("showcase.stack", "", (value): value is string => typeof value === "string" && (value === "" || stackChoices.some(([key]) => key === value)));
  const [draftId, setDraftId] = usePersistentState("showcase.draftId", "");
  const [sort, setSort] = usePersistentState("showcase.sort", "relevance", isShowcaseSort);
  // An anonymous code ("0A40") is matched by id prefix; the server's text search cannot see it.
  const code = candidateCode(query);
  // Client-side ordering and code lookup need the whole result set, not the first page.
  const loadAll = sort !== "relevance" || !!code;
  const vacancies = useResource(async () => {
    const items: Awaited<ReturnType<typeof api.getVacancyDrafts>> = [];
    for (let offset = 0; ; offset += 50) {
      const page = await api.getVacancyDrafts(50, offset);
      items.push(...page);
      if (page.length < 50) return items;
    }
  });
  const selected = vacancies.data?.find((item) => item.draft_id === draftId && !item.archived && item.business_status === "open");
  useEffect(() => {
    if (vacancies.data && draftId && !selected) setDraftId("");
  }, [vacancies.data, draftId, selected, setDraftId]);
  const filters = {
    ...(specialization
      ? { classes: SPECIALIZATIONS.filter((item) => item === specialization) }
      : {}),
    ...(grade ? { grades: GRADES.filter((item) => item === grade) } : {}),
    ...(stack.trim()
      ? { groups: { stacks: [{ key: stack.trim().toLowerCase() }] } }
      : {}),
    ...(selected ? { vacancy_id: selected.vacancy_id } : {}),
  };
  const pool = useResource(
    async () => {
      const request = { ...(code ? {} : { q: query }), filters, limit: loadAll ? 100 : 30 };
      let page = await api.searchCandidates(request);
      if (!loadAll) return page;
      const unique = new Map(page.items.map((item) => [item.candidate_id, item]));
      // Bounded: at most 1000 cards are ordered locally; a larger pool keeps "Показать ещё".
      for (let step = 1; step < 10 && page.has_more && page.next_cursor; step++) {
        page = await api.searchCandidates({ ...request, cursor: page.next_cursor });
        page.items.forEach((item) => unique.set(item.candidate_id, item));
      }
      return { ...page, items: [...unique.values()] };
    },
    [query, specialization, grade, stack, selected?.vacancy_id, loadAll],
  );
  const filterIdentity = JSON.stringify([
    loadAll,
    query,
    specialization,
    grade,
    stack.trim().toLowerCase(),
    selected?.vacancy_id,
  ]);
  const pageScope = useRef({ identity: filterIdentity, generation: 0 });
  if (pageScope.current.identity !== filterIdentity)
    pageScope.current = {
      identity: filterIdentity,
      generation: pageScope.current.generation + 1,
    };
  const pageInFlight = useRef(false);
  const [pageBusy, setPageBusy] = useState(false);
  useEffect(
    () => () => {
      pageScope.current.generation += 1;
    },
    [],
  );
  async function loadMore() {
    if (
      pageInFlight.current ||
      pool.loading ||
      !pool.data?.has_more ||
      !pool.data.next_cursor
    )
      return;
    pageInFlight.current = true;
    setPageBusy(true);
    const generation = pageScope.current.generation;
    const cursor = pool.data.next_cursor;
    try {
      const next = await api.searchCandidates({
        ...(code ? {} : { q: query }),
        filters,
        cursor,
        limit: loadAll ? 100 : 30,
      });
      if (pageScope.current.generation !== generation) return;
      pool.setData((current) => {
        if (!current || current.next_cursor !== cursor) return current;
        const unique = new Map(
          current.items.map((item) => [item.candidate_id, item]),
        );
        next.items.forEach((item) => unique.set(item.candidate_id, item));
        return { ...next, items: [...unique.values()] };
      });
    } catch (reason) {
      if (pageScope.current.generation === generation)
        setNotice(errorText(reason));
    } finally {
      pageInFlight.current = false;
      setPageBusy(false);
    }
  }
  const likeKeys = useRef(new Map<string, string>());
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [saved, setSaved] = useState<string[]>([]);
  const [choosing, setChoosing] = useState<PublicCandidateCard | null>(null);
  const saveLock = useRef(false);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saveLock.current) setChoosing(null);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  const open = vacancies.data?.filter(
    (item) => !item.archived && item.business_status === "open",
  ) || [];
  /** The explicit plus/target choice authorizes force-add; the server still validates its signed, source-bound mismatch receipt. */
  async function save(candidate: PublicCandidateCard, target: string) {
    if (saveLock.current) return;
    if (!target) { setChoosing(candidate); setNotice(""); return; }
    if (!open.some(item => item.draft_id === target)) {
      setNotice("Эта вакансия больше не открыта. Обновите список и выберите другую.");
      return;
    }
    const keyIdentity = `${target}:${candidate.candidate_id}:${candidate.profile_revision}`;
    if (!likeKeys.current.has(keyIdentity)) likeKeys.current.set(keyIdentity, crypto.randomUUID());
    const request: ShowcaseLikeRequest = {
      candidate_id: candidate.candidate_id,
      profile_revision: candidate.profile_revision,
      idempotency_key: likeKeys.current.get(keyIdentity)!,
    };
    saveLock.current = true;
    setBusy(candidate.candidate_id);
    setNotice("");
    try {
      try { await api.likeShowcaseCandidate(target, request); }
      catch (reason) {
        const mismatch = mismatchFrom(reason);
        if (!mismatch || mismatch.candidate_id !== request.candidate_id ||
          mismatch.profile_revision !== request.profile_revision) throw reason;
        await api.likeShowcaseCandidate(target, { ...request, mismatch_token: mismatch.mismatch_token });
      }
      setSaved(previous => [...previous, `${target}:${candidate.candidate_id}`]);
      setChoosing(null);
      setNotice("Кандидат добавлен в избранное вакансии.");
    } catch (reason) { setNotice(errorText(reason)); }
    finally { saveLock.current = false; setBusy(null); }
  }
  const visible = orderCandidates(
    code ? (pool.data?.items ?? []).filter((item) => item.candidate_id.toLowerCase().startsWith(code)) : (pool.data?.items ?? []),
    sort,
  );
  return (
    <Reveal className="discovery-showcase">
      <PageTitle title="Соискатели">
        <InlineSearch inputRef={searchInput} label="Поиск по имени, роли или стеку" placeholder="Поиск: Имя, роль, стек" value={q} onChange={setQ} onSubmit={() => setQuery(q)} />
      </PageTitle>
      <div className="discovery-filters">
        <Select
          aria-label="Специализация"
          value={specialization}
          onChange={(event) => setSpecialization(event.target.value)}
        >
          <option value="">Специализация</option>
          {SPECIALIZATIONS.map((item) => (
            <option key={item} value={item}>
              {roleLabels[item]}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Грейд"
          value={grade}
          onChange={(event) => setGrade(event.target.value)}
        >
          <option value="">Грейд</option>
          {GRADES.map((item) => (
            <option key={item} value={item}>
              {gradeLabels[item]}
            </option>
          ))}
        </Select>
        <Select aria-label="Стек" value={stack} onChange={event => setStack(event.target.value)}>
          <option value="">Любой стек</option>
          {stackChoices.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </Select>
        <Select
          aria-label="Вакансия"
          value={draftId}
          onChange={(event) => {
            setDraftId(event.target.value);
            setSaved([]);
          }}
        >
          <option value="">Вакансия</option>
          {open.map((item) => (
            <option key={item.draft_id} value={item.draft_id}>
              {item.fields.title || "Без названия"}
            </option>
          ))}
        </Select>
        <Select
          className="discovery-sort"
          aria-label="Сортировка"
          value={sort}
          onChange={(event) => { if (isShowcaseSort(event.target.value)) setSort(event.target.value); }}
        >
          {SHOWCASE_SORTS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </Select>
      </div>
      {notice && (
        <p className="inline-notice" role="status">
          {notice}
        </p>
      )}
      {vacancies.error && <p className="form-error">{vacancies.error}</p>}
      {pool.data?.fallback_reason && (
        <p className="inline-notice">
          Семантический поиск недоступен. Показаны результаты текстового поиска.
        </p>
      )}
      {pool.loading || pool.error ? (
        <State {...pool} retry={pool.refresh} />
      ) : !visible.length ? (
        <State empty={code ? `Кандидат с кодом ${code.toUpperCase()} не найден` : "Подходящие соискатели не найдены"} />
      ) : (
        <div className="discovery-list">
          {visible.map((candidate) => (
            <article
              className="discovery-row"
              key={candidate.candidate_id}
            >
              <CandidateIdentity candidate={candidate} />
              <div className="discovery-row-tags">
                <span>{gradeLabels[candidate.category.grade]}</span>
                <span>
                  {salary(candidate.salary.from_rub, candidate.salary.to_rub)}
                </span>
                <span>{candidate.city}</span>
              </div>
              <div className="discovery-row-actions">
                <button
                  className="discovery-profile-button"
                  aria-label={`Открыть резюме: ${candidateName(candidate.display_name)}`}
                  onClick={() =>
                    navigate(
                      `/resume/${candidate.candidate_id}${draftId ? `?draft=${draftId}` : ""}`,
                    )
                  }
                >
                  <Icon name="resumes" />
                </button>
                <button
                  className={`discovery-like ${saved.includes(`${draftId}:${candidate.candidate_id}`) ? "saved" : ""}`}
                  aria-label={`Добавить в вакансию: ${candidateName(candidate.display_name)}`}
                  disabled={
                    busy === candidate.candidate_id ||
                    saved.includes(`${draftId}:${candidate.candidate_id}`)
                  }
                  onClick={() => void save(candidate, draftId)}
                >
                  <Icon name={saved.includes(`${draftId}:${candidate.candidate_id}`) ? "heart-filled" : "plus"} />
                </button>
              </div>
            </article>
          ))}
          {pool.data?.has_more && (
            <div className="pagination">
              <Action disabled={pageBusy} onClick={() => void loadMore()}>
                {pageBusy ? "Загружаем…" : "Показать ещё"}
              </Action>
            </div>
          )}
        </div>
      )}
      <AnimatePresence>
        {choosing && (
          <motion.div className="modal-shade" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <section className="modal discovery-confirm" role="dialog" aria-modal="true" aria-label="Выберите вакансию">
              <h2>Добавить в вакансию</h2>
              <p>{candidateName(choosing.display_name)} будет добавлен независимо от требований вакансии.</p>
              {vacancies.loading || vacancies.error ? <State {...vacancies} retry={vacancies.refresh} /> : !open.length ? (
                <p>Сначала опубликуйте вакансию, чтобы добавлять кандидатов.</p>
              ) : <div className="discovery-targets">
                {open.map(item => <Action key={item.draft_id} disabled={busy !== null}
                  onClick={() => void save(choosing, item.draft_id)}>{item.fields.title || "Без названия"}</Action>)}
              </div>}
              {notice && <p role="alert" className="form-error">{notice}</p>}
              <Action disabled={busy !== null} onClick={() => setChoosing(null)}>Отмена</Action>
            </section>
          </motion.div>
        )}
      </AnimatePresence>
    </Reveal>
  );
}

/** Anonymous public profile with actual evidence and participant-owned recruitment actions. */
export function Resume({
  candidateId,
  draftId,
  navigate,
  fromVacancy = false,
}: Navigation & { candidateId: string; draftId?: string; fromVacancy?: boolean }) {
  const backTitle = fromVacancy && draftId ? "К вакансии" : "К людям";
  const backPath = draftId ? `/vacancy/${draftId}${fromVacancy ? "" : "/resumes"}` : "/showcase";
  const profile = useResource(
    () => api.getPublicCandidate(candidateId),
    [candidateId],
  );
  const vacancies = useResource(() => api.getVacancyDrafts());
  const recruitment = useResource(
    () =>
      draftId
        ? api.getEmployerRecruitment(draftId)
        : Promise.resolve({ items: [], limit: 0, offset: 0, has_more: false }),
    [draftId],
  );
  const process = recruitment.data?.items.find(
    (item) => item.candidate_id === candidateId,
  );
  const [invite, setInvite] = useState(false);
  const clearDraft = useClearPersistentState();
  const formKey = `resume.${candidateId}.${draftId || "all"}`;
  const [selected, setSelected] = usePersistentState(`${formKey}.selected`, draftId || "");
  const organization = useResource(() => api.getEmployerProfile());
  const company = organization.data?.company_name || "";
  const [description, setDescription] = usePersistentState(`${formKey}.description`, "");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const mutationKeys = useRef(new Map<string, string>());
  function mutationKey(identity: string) {
    const previous = mutationKeys.current.get(identity);
    if (previous) return previous;
    const key = crypto.randomUUID();
    mutationKeys.current.set(identity, key);
    return key;
  }
  const [reject, setReject] = useState(false);
  const [reason, setReason] = usePersistentState(`${formKey}.reason`, "");
  const [categories, setCategories] = usePersistentState<RejectionReasonCategory[]>(`${formKey}.categories`, [], (value): value is RejectionReasonCategory[] => Array.isArray(value) && value.every(item => rejectionReasonCategories.includes(item)));
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        setInvite(false);
        setReject(false);
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [busy]);
  async function sendInvitation(event: FormEvent) {
    event.preventDefault();
    if (!profile.data || !selected) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await api.inviteCandidate(selected, {
        candidate_id: candidateId,
        description: description.trim(),
        idempotency_key: mutationKey(
          JSON.stringify([
            "invite",
            selected,
            candidateId,
            company.trim(),
            description.trim(),
          ]),
        ),
      });
      clearDraft(`${formKey}.description`);
      setInvite(false);
      navigate(`/chat/${result.id}`);
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  async function rejectCandidate(event: FormEvent) {
    event.preventDefault();
    if (!draftId) return;
    const feedbackError = rejectionFeedbackError(reason, categories);
    if (feedbackError) { setNotice(feedbackError); return; }
    setBusy(true);
    try {
      if (process)
        await api.actOnRecruitment("employer", process.id, {
          action: "reject",
          reason: reason.trim(),
          categories,
          expected_revision: process.revision,
          idempotency_key: mutationKey(
            JSON.stringify([
              "reject",
              process.id,
              process.revision,
              reason.trim(), categories,
            ]),
          ),
        });
      else
        await api.rejectSavedCandidate(draftId, {
          candidate_id: candidateId,
          reason: reason.trim(),
          categories,
          idempotency_key: mutationKey(
            JSON.stringify([
              "reject_saved",
              draftId,
              candidateId,
              reason.trim(), categories,
            ]),
          ),
        });
      clearDraft(`${formKey}.reason`);
      clearDraft(`${formKey}.categories`);
      setReject(false);
      recruitment.refresh();
      setNotice("Решение сохранено.");
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  if (profile.loading || profile.error || !profile.data)
    return (
      <>
        <PageTitle
          title={backTitle}
          back={() => navigate(backPath)}
        />
        <State {...profile} retry={profile.refresh} />
      </>
    );
  const candidate = profile.data;
  const detail = candidate.public_profile;
  const pdf = protectedResumeUrl(candidate.resume_url);
  const vacancy = vacancies.data?.find((item) => item.draft_id === selected);
  const sections = [
    { title: "Достижения — внешний контур ФСП", entries: detail.achievements },
    { title: "Опыт работы", entries: detail.experience },
    { title: "Проекты и документы", entries: detail.projects },
  ];
  return (
    <Reveal className="discovery-resume">
      <PageTitle
        title={backTitle}
        back={() => navigate(backPath)}
      />
      <header className="discovery-resume-hero">
        <h1>{candidateName(candidate.display_name)}</h1>
        <AnonymousAvatar name={candidateName(candidate.display_name)} large />
        <div className="tags">
          <span className="tag">
            {roleLabels[candidate.category.specialization]}
          </span>
          <span className="tag">{gradeLabels[candidate.category.grade]}</span>
          <span className="tag">{candidate.city}</span>
          {detail.work_types?.map((item) => (
            <span className="tag" key={item}>
              {{ remote: "Удалённо", onsite: "Офис", hybrid: "Гибрид" }[item]}
            </span>
          ))}
        </div>
      </header>
      <div className="discovery-resume-body">
        <h2>Информация о соискателе</h2>
        {detail.description && (
          <p className="discovery-bio">{detail.description}</p>
        )}
        {sections.map((section) => (
          <details className="discovery-accordion" key={section.title}>
            <summary>
              {section.title}
              <span>+</span>
            </summary>
            <div className="discovery-accordion-body">
              {section.entries.length ? (
                section.entries.map((item, index) => (
                  <article key={`${item.title}-${index}`}>
                    <h3>{item.title}</h3>
                    <p>{item.description}</p>
                    <small>
                      Статус: {evidenceLabels[item.status] || item.status}
                    </small>
                  </article>
                ))
              ) : (
                <p>Опубликованных данных пока нет.</p>
              )}
            </div>
          </details>
        ))}
        <details className="discovery-accordion">
          <summary>
            Тесты — переносимые<span>+</span>
          </summary>
          <div className="discovery-accordion-body">
            {candidate.testing_results?.map(result => <article key={result.subject} className="employer-testing-result"><h3>{testingSubjectLabel(result.subject)} · {gradeLabels[result.grade]}</h3><p>Подтверждено тестированием</p><p className="muted">{testingPresenceLabel(result)}</p><p className="muted">Автоматическая отметка присутствия, не заключение о честности.</p></article>)}
            <p>Общий грейд: {gradeLabels[candidate.category.grade]}</p>
            <p>
              {candidate.category.grade_status === "approved"
                ? "Подтверждён тестированием"
                : "Указан кандидатом; подтверждение отсутствует"}
            </p>
          </div>
        </details>
        <details className="discovery-accordion">
          <summary>
            Вектор навыков<span>+</span>
          </summary>
          <div className="discovery-accordion-body discovery-skills">
            {[...detail.domains, ...detail.stacks, ...detail.soft_skills].map(
              (skill, index) => (
                <div className="skill-row" key={`${skill.key}-${index}`}>
                  <header>
                    <span>{skill.key}</span>
                    <span>
                      {skill.level}/7 ·{" "}
                      {evidenceLabels[skill.status] || skill.status}
                    </span>
                  </header>
                  <div className="skill-track">
                    <span
                      style={{
                        width: `${Math.min(100, Math.max(0, (skill.level / 7) * 100))}%`,
                      }}
                    />
                  </div>
                </div>
              ),
            )}
            {!detail.domains.length &&
              !detail.stacks.length &&
              !detail.soft_skills.length && (
                <p>Опубликованных навыков пока нет.</p>
              )}
          </div>
        </details>
        {notice && (
          <p className="inline-notice" role="status">
            {notice}
          </p>
        )}
        {recruitment.error && <p className="form-error">{recruitment.error}</p>}
        <div className="discovery-resume-actions">
          {pdf ? (
            <a
              className="action discovery-dark"
              href={pdf}
              target="_blank"
              rel="noreferrer"
            >
              PDF Резюме
            </a>
          ) : (
            <Action disabled>PDF недоступен</Action>
          )}
          <Action
            className="discovery-dark"
            onClick={() =>
              process ? navigate(`/chat/${process.id}`) : setInvite(true)
            }
          >
            {process ? "Собеседование" : "Пригласить"}
          </Action>
          <Action
            className="discovery-reject"
            disabled={
              !draftId ||
              recruitment.loading ||
              !!recruitment.error ||
              (!!process && !process.allowed_actions.includes("reject"))
            }
            onClick={() => setReject(true)}
          >
            Отклонить
          </Action>
          <Action
            variant="primary"
            disabled={
              !process || !process.allowed_actions.includes("offer_send")
            }
            onClick={() => process && navigate(`/chat/${process.id}`)}
          >
            Отправить оффер
          </Action>
        </div>
        {process && (
          <p className="discovery-process-note">
            Статус: {statusLabels[process.state]}. Приём фиксируется после
            принятия оффера кандидатом.
          </p>
        )}
      </div>
      <AnimatePresence>
        {(invite || reject) && (
          <motion.div
            className="modal-shade"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <section
              className="modal discovery-confirm"
              role="dialog"
              aria-modal="true"
              aria-label={invite ? "Приглашение" : "Отклонить кандидата"}
            >
              <h2>{invite ? "Приглашение" : "Отклонить кандидата"}</h2>
              <form
                onSubmit={invite ? sendInvitation : rejectCandidate}
                className="form-grid"
              >
                {invite ? (
                  <>
                    <Field label="Открытая вакансия">
                      <Select
                        value={selected}
                        required
                        onChange={(event) => setSelected(event.target.value)}
                      >
                        <option value="" hidden>Выберите вакансию</option>
                        {vacancies.data
                          ?.filter(
                            (item) =>
                              !item.archived && item.business_status === "open",
                          )
                          .map((item) => (
                            <option key={item.draft_id} value={item.draft_id}>
                              {item.fields.title}
                            </option>
                          ))}
                      </Select>
                    </Field>
                    {vacancy && (
                      <p>
                        {salary(vacancy.fields.zp_min, vacancy.fields.zp_max)}
                      </p>
                    )}
                    <Field label="Компания"><p>{company || "Название компании пока не указано"}</p></Field>{organization.error && <State error={organization.error} retry={organization.refresh} />}{!company && <Action type="button" onClick={() => navigate("/settings")}>Указать компанию в настройках</Action>}
                    <Field label="Текст приглашения">
                      <textarea
                        required
                        value={description}
                        onChange={(event) => setDescription(event.target.value)}
                      />
                    </Field>
                    <p className="muted">
                      Приглашение доступно для кандидатов в избранном вакансии.
                      Добавьте профиль через витрину, если ещё не сделали этого.
                    </p>
                  </>
                ) : (
                  <RejectionFeedback reason={reason} setReason={setReason} categories={categories} setCategories={setCategories} disabled={busy} />
                )}
                {notice && <p className="form-error">{notice}</p>}
                <div className="form-actions">
                  <Action
                    type="button"
                    onClick={() => {
                      setInvite(false);
                      setReject(false);
                    }}
                  >
                    Отмена
                  </Action>
                  <Action
                    variant="primary"
                    type="submit"
                    disabled={
                      busy ||
                      (invite
                        ? !selected || !company.trim() || !description.trim()
                        : !reason.trim())
                    }
                  >
                    {busy ? "Сохраняем…" : invite ? "Отправить" : "Отклонить"}
                  </Action>
                </div>
              </form>
            </section>
          </motion.div>
        )}
      </AnimatePresence>
    </Reveal>
  );
}

function Milestones({ process }: { process?: RecruitmentView }) {
  const stage = recruitmentStage(process);
  return (
    <div className="discovery-milestones">
      {["Лайк", "Приглашение", "Собеседование", "Оффер", "Приём"].map(
        (item, index) => (
          <span
            key={item}
            className={
              index < stage ? "done" : index === stage ? "current" : ""
            }
          >
            {item}
          </span>
        ),
      )}
    </div>
  );
}

type ResumeVacancyPage = {
  vacancy: Awaited<ReturnType<typeof api.getVacancyDrafts>>[number];
  saved: Awaited<ReturnType<typeof api.getSaved>>;
  processes: Awaited<ReturnType<typeof api.getEmployerRecruitment>>;
};

/** Owner-wide saved resumes retain vacancy-specific recruitment states and public identities. */
export function Resumes({ navigate }: Navigation) {
  const [selection, setSelection] = usePersistentState<string[] | null>("resumes.selection", null,
    (value): value is string[] | null => value === null || (Array.isArray(value) && value.every(item => typeof item === "string")));
  const [moreBusy, setMoreBusy] = useState(false);
  const [moreError, setMoreError] = useState("");
  const drafts = useResource(async () => {
    const items: Awaited<ReturnType<typeof api.getVacancyDrafts>> = [];
    for (let offset = 0; ;) {
      const page = await api.getVacancyDrafts(50, offset);
      items.push(...page);
      if (page.length < 50) break;
      offset += page.length;
    }
    return items;
  });
  const selectedDrafts =
    drafts.data?.filter(
      (item) => selection === null || selection.includes(item.draft_id),
    ) || [];
  const identity = JSON.stringify(selectedDrafts.map((item) => item.draft_id));
  const scope = useRef({ identity, generation: 0 });
  if (scope.current.identity !== identity)
    scope.current = { identity, generation: scope.current.generation + 1 };
  useEffect(
    () => () => {
      scope.current.generation += 1;
    },
    [],
  );
  useEffect(() => {
    setMoreError("");
  }, [identity]);
  const pages = useResource(async () => {
    const result: ResumeVacancyPage[] = [];
    // Keep concurrency bounded while reading every owner vacancy.
    for (let offset = 0; offset < selectedDrafts.length; offset += 4) {
      const batch = await Promise.all(
        selectedDrafts.slice(offset, offset + 4).map(async (vacancy) => {
          const [saved, processes] = await Promise.all([
            api.getSaved(vacancy.draft_id),
            api.getEmployerRecruitment(vacancy.draft_id),
          ]);
          return { vacancy, saved, processes };
        }),
      );
      result.push(...batch);
    }
    return result;
  }, [identity]);
  const moreInFlight = useRef(false);
  async function more() {
    if (!pages.data || moreInFlight.current || pages.loading) return;
    moreInFlight.current = true;
    setMoreBusy(true);
    setMoreError("");
    const generation = scope.current.generation;
    const snapshot = pages.data;
    try {
      const next: ResumeVacancyPage[] = [];
      for (const page of snapshot) {
        const id = page.vacancy.draft_id;
        const [saved, processes] = await Promise.all([
          page.saved.has_more
            ? api.getSaved(
                id,
                page.saved.limit,
                page.saved.offset + page.saved.items.length,
                page.saved.next_cursor || undefined,
              )
            : Promise.resolve(null),
          page.processes.has_more
            ? api.getEmployerRecruitment(
                id,
                page.processes.limit,
                page.processes.offset + page.processes.items.length,
                page.processes.next_cursor || undefined,
              )
            : Promise.resolve(null),
        ]);
        next.push({
          vacancy: page.vacancy,
          saved: saved
            ? {
                ...saved,
                offset: 0,
                items: [...page.saved.items, ...saved.items],
              }
            : page.saved,
          processes: processes
            ? {
                ...processes,
                offset: 0,
                items: [...page.processes.items, ...processes.items],
              }
            : page.processes,
        });
      }
      if (generation === scope.current.generation) pages.setData(next);
    } catch (error) {
      if (generation === scope.current.generation)
        setMoreError(errorText(error));
    } finally {
      moreInFlight.current = false;
      setMoreBusy(false);
    }
  }
  const rows = (pages.data || []).flatMap((page) => {
    const byCandidate = new Map<
      string,
      {
        candidate: CandidateIdentityView | null;
        process?: RecruitmentView;
        score?: number;
      }
    >();
    page.saved.items.forEach((item) =>
      byCandidate.set(item.candidate_id, {
        candidate: item.card,
        process: page.processes.items.find(
          (process) => process.candidate_id === item.candidate_id,
        ),
        score: item.card?.explanation.score,
      }),
    );
    return [...byCandidate].map(([id, row]) => ({
      ...row,
      id,
      vacancy: page.vacancy,
    }));
  });
  const loading = drafts.loading || pages.loading;
  const error = drafts.error || pages.error;
  return (
    <Reveal>
      <PageTitle title="Резюме">
        <details
          className="resume-vacancy-filter"
          key={drafts.data ? "ready" : "loading"}
        >
          <summary aria-label="Фильтр резюме по вакансиям">
            {selection === null
              ? "Все вакансии"
              : `Вакансии: ${selection.length}`}
            <span className="resume-filter-arrow">
              <Icon name="chevron" />
            </span>
          </summary>
          <div className="resume-vacancy-options">
            <label>
              <input
                type="checkbox" className="comit-checkbox"
                checked={selection === null}
                onChange={(event) =>
                  setSelection(event.target.checked ? null : [])
                }
              />
              Все вакансии
            </label>
            {drafts.data?.map((vacancy) => (
              <label key={vacancy.draft_id}>
                <input
                  type="checkbox" className="comit-checkbox"
                  checked={
                    selection === null || selection.includes(vacancy.draft_id)
                  }
                  onChange={(event) =>
                    setSelection((current) => {
                      const chosen =
                        current ||
                        drafts.data?.map((item) => item.draft_id) ||
                        [];
                      return event.target.checked
                        ? [
                            ...chosen.filter((id) => id !== vacancy.draft_id),
                            vacancy.draft_id,
                          ]
                        : chosen.filter((id) => id !== vacancy.draft_id);
                    })
                  }
                />
                {vacancy.fields.title || "Без названия"}
              </label>
            ))}
          </div>
        </details>
      </PageTitle>
      {loading || error ? (
        <State
          loading={loading}
          error={error}
          retry={() => {
            scope.current.generation += 1;
            drafts.refresh();
            pages.refresh();
          }}
        />
      ) : !rows.length ? (
        <State
          empty={
            !drafts.data?.length
              ? "Резюме появятся, когда вы опубликуете вакансию и сохраните кандидатов из ленты"
              : selection?.length === 0
                ? "Выберите вакансии для просмотра резюме"
                : "В выбранных вакансиях пока нет резюме"
          }
        />
      ) : (
        <div className="discovery-list vacancy-resume-list">
          {rows.map((row) => (
            <article
              className="discovery-row light-row"
              key={`${row.vacancy.draft_id}:${row.id}`}
            >
              {row.candidate && row.process && ["accepted", "offer_sent", "hired"].includes(row.process.state) && <a className="discovery-profile-link" href={`#/resume/${row.id}?draft=${row.vacancy.draft_id}`} aria-label={`Открыть резюме ${candidateName(row.candidate.display_name)}`} onClick={event => { event.preventDefault(); navigate(`/resume/${row.id}?draft=${row.vacancy.draft_id}`); }} />}
              {row.score != null && (
                <span
                  className="discovery-score"
                  title="Вычисленный score; не вероятность найма"
                >
                  {Math.round(Math.max(0, Math.min(1, row.score)) * 100)}%
                </span>
              )}
              {row.candidate ? (
                <CandidateIdentity candidate={row.candidate} />
              ) : (
                <>
                  <AnonymousAvatar name="Резюме недоступно" />
                  <div className="discovery-identity">
                    <h3>Резюме недоступно</h3>
                    <p>
                      {row.process
                        ? statusLabels[row.process.state]
                        : "Сохранён"}
                    </p>
                  </div>
                </>
              )}
              <RecruitmentTileButton candidateId={row.id} draftId={row.vacancy.draft_id} process={row.process} navigate={navigate} />
            </article>
          ))}
          {pages.data?.some(
            (page) => page.saved.has_more || page.processes.has_more,
          ) && (
            <div className="pagination">
              <Action disabled={moreBusy} onClick={() => void more()}>
                {moreBusy ? "Загружаем…" : "Показать ещё"}
              </Action>
            </div>
          )}
        </div>
      )}
      {moreError && (
        <p className="form-error" role="alert">
          {moreError}
        </p>
      )}
    </Reveal>
  );
}

/** Vacancy-scoped saved profiles and actual process milestones, with no implied cohort progression. */
export function VacancyResumes({
  draftId,
  navigate,
}: Navigation & { draftId: string }) {
  const [moreBusy, setMoreBusy] = useState(false);
  const [moreError, setMoreError] = useState("");
  const data = useResource(async () => {
    const [vacancy, saved, processes] = await Promise.all([
      api.getVacancy(draftId),
      api.getSaved(draftId),
      api.getEmployerRecruitment(draftId),
    ]);
    return { vacancy, saved, processes };
  }, [draftId]);
  async function more() {
    if (!data.data || moreBusy) return;
    setMoreBusy(true);
    setMoreError("");
    const snapshot = data.data;
    try {
      const [saved, processes] = await Promise.all([
        snapshot.saved.has_more
          ? api.getSaved(
              draftId,
              snapshot.saved.limit,
              snapshot.saved.offset + snapshot.saved.items.length,
              snapshot.saved.next_cursor || undefined,
            )
          : Promise.resolve(null),
        snapshot.processes.has_more
          ? api.getEmployerRecruitment(
              draftId,
              snapshot.processes.limit,
              snapshot.processes.offset + snapshot.processes.items.length,
              snapshot.processes.next_cursor || undefined,
            )
          : Promise.resolve(null),
      ]);
      data.setData((current) =>
        current
          ? {
              ...current,
              saved: saved
                ? {
                    ...saved,
                    offset: 0,
                    items: [...current.saved.items, ...saved.items],
                  }
                : current.saved,
              processes: processes
                ? {
                    ...processes,
                    offset: 0,
                    items: [...current.processes.items, ...processes.items],
                  }
                : current.processes,
            }
          : current,
      );
    } catch (error) {
      setMoreError(errorText(error));
    } finally {
      setMoreBusy(false);
    }
  }
  const rows = new Map<
    string,
    {
      candidate: CandidateIdentityView | null;
      name: string;
      process?: RecruitmentView;
      score?: number;
    }
  >();
  data.data?.saved.items.forEach((item) =>
    rows.set(item.candidate_id, {
      candidate: item.card,
      name: candidateName(item.card?.display_name, "Резюме недоступно"),
      process: data.data?.processes.items.find(
        (process) => process.candidate_id === item.candidate_id,
      ),
      score: item.card?.explanation.score,
    }),
  );
  return (
    <Reveal>
      <PageTitle
        title="Резюме по вакансии"
        back={() => navigate(`/vacancy/${draftId}`)}
      >
        {data.data && (
          <Action
            variant="primary"
            onClick={() => navigate(`/vacancy/${draftId}`)}
          >
            {data.data.vacancy.fields.title}
            <Icon name="arrow" />
          </Action>
        )}
      </PageTitle>
      {data.loading || data.error ? (
        <State {...data} retry={data.refresh} />
      ) : !rows.size ? (
        <State empty="В избранном этой вакансии пока нет резюме" />
      ) : (
        <div className="discovery-list vacancy-resume-list">
          {[...rows].map(([id, row]) => (
            <article className="discovery-row light-row" key={id}>
              {row.candidate && row.process && ["accepted", "offer_sent", "hired"].includes(row.process.state) && <a className="discovery-profile-link" href={`#/resume/${id}?draft=${draftId}`} aria-label={`Открыть резюме ${candidateName(row.candidate.display_name)}`} onClick={event => { event.preventDefault(); navigate(`/resume/${id}?draft=${draftId}`); }} />}
              {row.score != null && (
                <span
                  className="discovery-score"
                  title="Вычисленный score; не вероятность найма"
                >
                  {Math.round(Math.max(0, Math.min(1, row.score)) * 100)}%
                </span>
              )}
              {row.candidate ? (
                <CandidateIdentity candidate={row.candidate} />
              ) : (
                <>
                  <AnonymousAvatar name={row.name} />
                  <div className="discovery-identity">
                    <h3>{row.name}</h3>
                    <p>
                      {row.process
                        ? statusLabels[row.process.state]
                        : "Сохранён"}
                    </p>
                  </div>
                </>
              )}
              <RecruitmentTileButton candidateId={id} draftId={draftId} process={row.process} navigate={navigate} />
            </article>
          ))}
          {(data.data?.saved.has_more || data.data?.processes.has_more) && (
            <div className="pagination">
              <Action disabled={moreBusy} onClick={() => void more()}>
                {moreBusy ? "Загружаем…" : "Показать ещё"}
              </Action>
            </div>
          )}
        </div>
      )}
      {moreError && (
        <p className="form-error" role="alert">
          {moreError}
        </p>
      )}
    </Reveal>
  );
}

/** Chart-ready owner dashboard; milestone pairs and daily events remain separate measures. */
export function Statistics({ navigate }: Navigation) {
  const data = useResource(() => api.getEmployerStatistics());
  if (data.loading || data.error || !data.data)
    return (
      <>
        <PageTitle title="Статистика" />
        <State {...data} retry={data.refresh} />
      </>
    );
  const stats = data.data;
  const stages: EmployerStatisticStage[] = [
    "delivered",
    "viewed",
    "liked",
    "invited",
    "accepted",
    "interviewed",
    "offered",
    "hired",
  ];
  const daily = stats.daily.map((day) =>
    Object.values(day.counts).reduce((sum, value) => sum + value, 0),
  );
  const dailyMax = Math.max(1, ...daily);
  const stageMax = Math.max(
    1,
    ...stages.flatMap((stage) => [stats.current[stage], stats.previous[stage]]),
  );
  const inventory = [
    { label: "Открыты", value: stats.inventory.open, tone: "blue" },
    { label: "Черновики", value: stats.inventory.draft, tone: "light" },
    { label: "Закрыты", value: stats.inventory.closed, tone: "green" },
    { label: "Архив", value: stats.inventory.archived, tone: "muted" },
  ];
  const total = inventory.reduce((sum, item) => sum + item.value, 0);
  let offset = 0;
  const arcs = inventory.map((item) => {
    const length = total ? (item.value / total) * 100 : 0;
    const arc = { ...item, length, offset };
    offset += length;
    return arc;
  });
  const points = (values: number[], maximum: number) =>
    values
      .map(
        (value, index) =>
          `${values.length > 1 ? (index / (values.length - 1)) * 600 : 300},${150 - (value / maximum) * 135}`,
      )
      .join(" ");
  const activityPoints = points(daily, dailyMax);
  const topMax = Math.max(1, ...stats.top_vacancies.map((item) => item.events));
  return (
    <Reveal className="discovery-statistics">
      <PageTitle title="Статистика" />
      <div className="statistics-grid">
        <section className="panel statistics-kpis">
          <h3>Обзор подбора</h3>
          <div className="statistics-kpi-grid">
            {(["viewed", "liked", "interviewed"] as const).map((stage) => {
              const values = stats.daily.map((day) => day.counts[stage]);
              return (
                <div key={stage}>
                  <span className="statistics-caption">
                    {stageLabels[stage]}
                  </span>
                  <strong>{stats.current[stage]}</strong>
                  <small>События по дням</small>
                  <svg
                    viewBox="0 0 600 160"
                    preserveAspectRatio="none"
                    aria-hidden="true"
                    className="statistics-sparkline"
                  >
                    <polyline points={points(values, Math.max(1, ...values))} />
                  </svg>
                  <small title="За предыдущий период">
                    Ранее {stats.previous[stage]}
                  </small>
                </div>
              );
            })}
          </div>
        </section>
        <section className="panel statistics-daily">
          <div className="statistics-card-heading">
            <h3>Активность</h3>
            <span className="statistics-caption">События · UTC</span>
          </div>
          <svg
            className="statistics-area"
            viewBox="0 0 600 170"
            role="img"
            aria-label={`Событий за период: ${daily.reduce((sum, value) => sum + value, 0)}`}
          >
            {[15, 60, 105, 150].map((y) => (
              <line
                className="statistics-guide"
                key={y}
                x1="0"
                x2="600"
                y1={y}
                y2={y}
              />
            ))}
            {daily.length > 0 && (
              <>
                <polygon points={`0,150 ${activityPoints} 600,150`} />
                <polyline points={activityPoints} />
              </>
            )}
          </svg>
          <div className="statistics-axis">
            <span>{stats.period.from}</span>
            <span>{stats.period.to}</span>
          </div>
          {!daily.some(Boolean) && (
            <p className="statistics-empty">Событий за период пока нет</p>
          )}
          <details className="statistics-data-table">
            <summary>Данные по дням</summary>
            <table>
              <thead>
                <tr>
                  <th>Дата · UTC</th>
                  <th>События</th>
                </tr>
              </thead>
              <tbody>
                {stats.daily.map((day, index) => (
                  <tr key={day.date}>
                    <td>{day.date}</td>
                    <td>{daily[index]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </section>
        <section className="panel statistics-inventory">
          <h3>Ваши вакансии</h3>
          <div className="statistics-inventory-chart">
            <div className="statistics-donut">
              <svg
                viewBox="0 0 160 160"
                role="img"
                aria-label={inventory
                  .map((item) => `${item.label}: ${item.value}`)
                  .join(", ")}
              >
                <circle
                  className="statistics-donut-track"
                  cx="80"
                  cy="80"
                  r="61"
                />
                {arcs
                  .filter((arc) => arc.length > 0)
                  .map((arc) => (
                    <circle
                      key={arc.label}
                      className={`statistics-tone-${arc.tone}`}
                      cx="80"
                      cy="80"
                      r="61"
                      pathLength="100"
                      strokeDasharray={`${arc.length} ${100 - arc.length}`}
                      strokeDashoffset={-arc.offset}
                    />
                  ))}
              </svg>
              <div>
                <strong>{total}</strong>
                <span>всего</span>
              </div>
            </div>
            <div className="statistics-legend">
              {inventory.map((item) => (
                <div key={item.label}>
                  <i className={`statistics-tone-${item.tone}`} />
                  <span>{item.label}</span>
                  <strong>{item.value}</strong>
                </div>
              ))}
            </div>
          </div>
          <div className="statistics-summary-grid">
            <div>
              <div><strong>{stats.unique_candidates}</strong><Icon name="candidates" /></div>
              <span>кандидатов за период</span>
            </div>
            <div>
              <div><strong>{stats.inventory.saved_pairs}</strong><Icon name="resumes" /></div>
              <span>сохранено сейчас</span>
            </div>
            <div>
              <div><strong>{stats.inventory.active_processes}</strong><Icon name="clock" /></div>
              <span>активных процессов</span>
            </div>
          </div>
          {!total && (
            <p className="statistics-empty">
              Создайте первую вакансию, чтобы начать подбор
            </p>
          )}
        </section>
        <section className="panel statistics-metrics">
          <div className="statistics-card-heading">
            <h3>Этапы подбора</h3>
            <span className="statistics-comparison-key">
              <i />
              Сейчас <i />
              Ранее
            </span>
          </div>
          <div className="statistics-stage-bars">
            {stages.map((stage) => (
              <div key={stage}>
                <span>{stageLabels[stage]}</span>
                <div
                  className="statistics-stage-track"
                  title={`Сейчас ${stats.current[stage]}, ранее ${stats.previous[stage]}`}
                >
                  <span
                    className="statistics-stage-previous"
                    style={{
                      width: `${(stats.previous[stage] / stageMax) * 100}%`,
                    }}
                  />
                  <span
                    className="statistics-stage-current"
                    style={{
                      width: `${(stats.current[stage] / stageMax) * 100}%`,
                    }}
                  />
                </div>
                <strong>{stats.current[stage]}</strong>
              </div>
            ))}
          </div>
          <p className="statistics-footnote">
            Уникальные пары «вакансия — кандидат» на каждом этапе
          </p>
        </section>
        <section className="panel statistics-vacancies">
          <div className="statistics-card-heading">
            <h3>Активность вакансий</h3>
            <button
              className="statistics-all-link"
              onClick={() => navigate("/vacancies")}
            >
              Все вакансии <span>↗</span>
            </button>
          </div>
          {stats.top_vacancies.length ? (
            stats.top_vacancies.map((item) => (
              <button
                className="statistics-vacancy-row"
                key={item.vacancy_id}
                onClick={() => navigate(`/vacancy/${item.draft_id}`)}
              >
                <span>
                  <b>{item.title}</b>
                  <small>{item.candidates} кандидатов</small>
                </span>
                <span className="statistics-vacancy-meter">
                  <span style={{ width: `${(item.events / topMax) * 100}%` }} />
                </span>
                <strong title="Количество событий">{item.events}</strong>
              </button>
            ))
          ) : (
            <div className="statistics-vacancy-empty">
              <Icon name="vacancies" />
              <p>Активности по вакансиям пока нет</p>
            </div>
          )}
        </section>
        <section className="panel statistics-expenses">
          <div className="statistics-card-heading"><h3>Расходы</h3><span className="statistics-caption">{stats.period.from} — {stats.period.to}</span></div>
          {stats.expenses ? <>
            <div className="statistics-expense-total"><strong>{formatExpense(stats.expenses.total_kopeks)}</strong><span>за выбранный период</span></div>
            <div className="statistics-expense-categories">{stats.expenses.categories.map(item => {
              const meta = { publication: { label: "Размещение вакансий", icon: "vacancies" as const }, invitation: { label: "Приглашения", icon: "candidates" as const }, ai: { label: "ИИ-агент", icon: "ai" as const }, penalty: { label: "Неустойки", icon: "clock" as const } }[item.category];
              return <div key={item.category}><div><Icon name={meta.icon} /><span>{meta.label}</span></div><strong>{formatExpense(item.amount_kopeks)}</strong><small>{item.category === "ai" ? `Сверх ${formatExpense(stats.expenses.tariffs.ai_daily_allowance_kopeks)} в день` : item.category === "publication" ? `${formatExpense(stats.expenses.tariffs.publication_kopeks)} за размещение` : item.category === "invitation" ? `${formatExpense(stats.expenses.tariffs.invitation_kopeks)} за приглашение` : "Просрочки и решения поддержки"}</small></div>;
            })}</div>
            <div className="statistics-expense-daily" role="img" aria-label="Расходы по дням">{stats.expenses.daily.map(day => <div key={day.date} title={`${day.date}: ${formatExpense(day.amount_kopeks)}`}><span style={{ height: `${Math.max(day.amount_kopeks ? 4 : 0, day.amount_kopeks / Math.max(1, ...stats.expenses.daily.map(item => item.amount_kopeks)) * 100)}%` }} /></div>)}</div>
            <div className="statistics-axis"><span>{stats.period.from}</span><span>{stats.period.to}</span></div>
            {!stats.expenses.total_kopeks && <p className="statistics-empty">За этот период расходов пока нет</p>}
          </> : <State error="Расходы пока не удалось загрузить" retry={data.refresh} />}
        </section>
      </div>
      <p className="statistics-period">
        {stats.period.from} — {stats.period.to} · UTC{" "}
        <span>
          Сравнение: {stats.period.previous_from} — {stats.period.previous_to}
        </span>
      </p>
    </Reveal>
  );
}

/** Reuse recruitment command forms directly from both saved-resume lists. */
function RecruitmentTileButton({ candidateId, draftId, process: initial, navigate }: Navigation & { candidateId: string; draftId: string; process?: RecruitmentView }) {
  const [updated, setUpdated] = useState<RecruitmentView | undefined>();
  const process = updated && (!initial || Number(updated.revision) >= Number(initial.revision)) ? updated : initial;
  const [open, setOpen] = useState(false), [reject, setReject] = useState(false);
  const action = recruitmentTileAction(process);
  const done = (value: RecruitmentView) => { setUpdated(value); setOpen(false); setReject(false); };
  return <><Milestones process={process} /><div className="discovery-tile-actions">
    <Action className="discovery-reject" aria-label="Отклонить кандидата" title="Отклонить кандидата" disabled={!!process && !process.allowed_actions.includes("reject")} onClick={() => setReject(true)}><Icon name="close" /></Action>
    <Action className="discovery-dark" disabled={action.disabled} title={action.disabled && ["sent", "viewed"].includes(process?.state || "") ? "Собеседование можно назначить после принятия приглашения кандидатом" : undefined} onClick={() => action.command === "chat" && process ? navigate(`/chat/${process.id}`) : setOpen(true)}>{action.label}</Action>
  </div>{createPortal(<AnimatePresence>
    {reject && (process ? <ProcessCommand process={process} command="reject" onClose={() => setReject(false)} onDone={done} /> : <SavedCandidateRejection candidateId={candidateId} draftId={draftId} onClose={() => setReject(false)} onDone={done} />)}
    {open && (action.command === "invite" ? <SavedCandidateInvitation candidateId={candidateId} draftId={draftId} onClose={() => setOpen(false)} onDone={done} navigate={navigate} /> : process && (action.command === "interview_propose" || action.command === "offer_send") ? <ProcessCommand process={process} command={action.command} onClose={() => setOpen(false)} onDone={done} /> : null)}
  </AnimatePresence>, document.querySelector(".employer-app") || document.body)}</>;
}

/** A saved like can be rejected before an invitation, with the same required feedback. */
function SavedCandidateRejection({ candidateId, draftId, onClose, onDone }: { candidateId: string; draftId: string; onClose: () => void; onDone: (value: RecruitmentView) => void }) {
  const prefix = `resume.${candidateId}.${draftId}`;
  const [reason, setReason] = usePersistentState(`${prefix}.reason`, "");
  const [categories, setCategories] = usePersistentState<RejectionReasonCategory[]>(`${prefix}.categories`, [], (value): value is RejectionReasonCategory[] => Array.isArray(value) && value.every(item => rejectionReasonCategories.includes(item)));
  const clear = useClearPersistentState();
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const key = useRef({ identity: "", value: crypto.randomUUID() });
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return;
    const problem = rejectionFeedbackError(reason, categories); if (problem) { setError(problem); return; }
    const identity = JSON.stringify([reason.trim(), categories]);
    if (key.current.identity !== identity) key.current = { identity, value: crypto.randomUUID() };
    setBusy(true); setError("");
    try { const result = await api.rejectSavedCandidate(draftId, { candidate_id: candidateId, reason: reason.trim(), categories, idempotency_key: key.current.value }); clear(`${prefix}.reason`); clear(`${prefix}.categories`); onDone(result); }
    catch (cause) { setError(errorText(cause)); } finally { setBusy(false); }
  }
  return <Dialog title="Отказ кандидату" close={() => { if (!busy) onClose(); }}><form className="form-grid" onSubmit={event => void submit(event)}><RejectionFeedback reason={reason} setReason={setReason} categories={categories} setCategories={setCategories} disabled={busy} />{error && <p className="form-error" role="alert">{error}</p>}<div className="form-actions"><Action type="button" disabled={busy} onClick={onClose}>Отмена</Action><Action type="submit" className="discovery-reject" disabled={busy}>{busy ? "Сохраняем…" : "Отклонить кандидата"}</Action></div></form></Dialog>;
}

/** Invitation from an already saved candidate uses the existing dialog, fields and invitation API. */
function SavedCandidateInvitation({ candidateId, draftId, onClose, onDone, navigate }: Navigation & { candidateId: string; draftId: string; onClose: () => void; onDone: (value: RecruitmentView) => void }) {
  const vacancy = useResource(() => api.getVacancy(draftId), [draftId]);
  const organization = useResource(() => api.getEmployerProfile());
  const [description, setDescription] = usePersistentState(`vacancy.${draftId}.invitation.description`, "");
  const clearDraft = useClearPersistentState();
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const key = useRef(crypto.randomUUID());
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return; setBusy(true); setError("");
    try { const value = await api.inviteCandidate(draftId, { candidate_id: candidateId, description: description.trim(), idempotency_key: key.current }); clearDraft(`vacancy.${draftId}.invitation.description`); onDone(value); }
    catch (reason) { setError(errorText(reason)); } finally { setBusy(false); }
  }
  const company = organization.data?.company_name;
  return <Dialog title="Пригласить кандидата" close={() => { if (!busy) onClose(); }}><form className="form-grid" onSubmit={event => void submit(event)}><fieldset disabled={busy}><Field label="Вакансия"><p>{vacancy.data?.fields.title}</p></Field><Field label="Зарплата"><p>{salary(vacancy.data?.fields.zp_min, vacancy.data?.fields.zp_max)}</p></Field><Field label="Компания"><p>{company || "Название компании пока не указано"}</p></Field>{!company && <Action type="button" onClick={() => navigate("/settings")}>Указать компанию в настройках</Action>}<Field label="Сообщение кандидату"><textarea required maxLength={10000} value={description} onChange={event => { setDescription(event.target.value); key.current = crypto.randomUUID(); }} /></Field></fieldset>{(organization.loading || organization.error || vacancy.loading || vacancy.error) && <State loading={organization.loading || vacancy.loading} error={organization.error || vacancy.error} retry={() => { organization.refresh(); vacancy.refresh(); }} />}{error && <p className="form-error" role="alert">{error}</p>}<div className="form-actions"><Action type="button" disabled={busy} onClick={onClose}>Отмена</Action><Action type="submit" variant="light" disabled={busy || !company || vacancy.loading || !!vacancy.error}>{busy ? "Отправляем…" : "Отправить приглашение"}</Action></div></form></Dialog>;
}

/** Format integer RUB kopeks without mixing provider USD units into the UI. */
function formatExpense(kopeks: number): string { return new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 }).format(kopeks / 100); }

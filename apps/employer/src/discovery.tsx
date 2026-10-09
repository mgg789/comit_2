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
import { candidateName } from "./candidate-name";
import { filterReasonLabel } from "./labels";
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
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [specialization, setSpecialization] = useState("");
  const [grade, setGrade] = useState("");
  const [stack, setStack] = useState("");
  const [draftId, setDraftId] = useState("");
  const vacancies = useResource(() => api.getVacancyDrafts());
  const selected = vacancies.data?.find((item) => item.draft_id === draftId);
  const pool = useResource(
    () =>
      api.searchCandidates({
        q: query,
        limit: 30,
        filters: {
          ...(specialization
            ? {
                classes: SPECIALIZATIONS.filter(
                  (item) => item === specialization,
                ),
              }
            : {}),
          ...(grade ? { grades: GRADES.filter((item) => item === grade) } : {}),
          ...(stack.trim()
            ? { groups: { stacks: [{ key: stack.trim().toLowerCase() }] } }
            : {}),
          ...(selected ? { vacancy_id: selected.vacancy_id } : {}),
        },
      }),
    [query, specialization, grade, stack, selected?.vacancy_id],
  );
  const filterIdentity = JSON.stringify([
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
        q: query,
        filters: {
          ...(specialization
            ? {
                classes: SPECIALIZATIONS.filter(
                  (item) => item === specialization,
                ),
              }
            : {}),
          ...(grade ? { grades: GRADES.filter((item) => item === grade) } : {}),
          ...(stack.trim()
            ? { groups: { stacks: [{ key: stack.trim().toLowerCase() }] } }
            : {}),
          ...(selected ? { vacancy_id: selected.vacancy_id } : {}),
        },
        cursor,
        limit: 30,
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
  const [warning, setWarning] = useState<{
    draftId: string;
    request: ShowcaseLikeRequest;
    candidate: PublicCandidateCard;
    warning: ShowcaseMismatchWarning;
  } | null>(null);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) setWarning(null);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [busy]);
  const open =
    vacancies.data?.filter(
      (item) => !item.archived && item.business_status === "open",
    ) || [];
  async function save(
    candidate: PublicCandidateCard,
    confirm?: typeof warning,
  ) {
    // With a single open vacancy the «+» needs no extra choice; otherwise the filter picks the target.
    const target = draftId || (open.length === 1 ? open[0]!.draft_id : "");
    if (!target && !confirm) {
      setNotice(
        open.length
          ? "Выберите вакансию в фильтре «Вакансия», чтобы добавить кандидата в подбор."
          : "Опубликуйте вакансию, чтобы добавлять кандидатов в подбор.",
      );
      return;
    }
    const keyIdentity = `${target}:${candidate.candidate_id}:${candidate.profile_revision}`;
    if (!likeKeys.current.has(keyIdentity))
      likeKeys.current.set(keyIdentity, crypto.randomUUID());
    const request = confirm
      ? { ...confirm.request, mismatch_token: confirm.warning.mismatch_token }
      : {
          candidate_id: candidate.candidate_id,
          profile_revision: candidate.profile_revision,
          idempotency_key: likeKeys.current.get(keyIdentity)!,
        };
    setBusy(candidate.candidate_id);
    setNotice("");
    try {
      await api.likeShowcaseCandidate(confirm?.draftId || target, request);
      setSaved((previous) => [...previous, candidate.candidate_id]);
      setWarning(null);
      setNotice("Кандидат добавлен в избранное вакансии.");
    } catch (reason) {
      const mismatch = mismatchFrom(reason);
      if (
        mismatch &&
        mismatch.candidate_id === request.candidate_id &&
        mismatch.profile_revision === request.profile_revision
      )
        setWarning({
          draftId: confirm?.draftId || target,
          request,
          candidate,
          warning: mismatch,
        });
      else {
        setWarning(null);
        setNotice(errorText(reason));
      }
    } finally {
      setBusy(null);
    }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    setQuery(q);
  }
  return (
    <Reveal className="discovery-showcase">
      <PageTitle title="Соискатели">
        <form onSubmit={submit} className="discovery-search">
          <input
            ref={searchInput}
            aria-label="Поиск по имени, роли или стеку"
            placeholder="Поиск: Имя, роль, стек"
            value={q}
            onChange={(event) => setQ(event.target.value)}
          />
          <button aria-label="Найти" type="submit">
            <Icon name="search" />
          </button>
        </form>
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
        <input
          aria-label="Стек"
          placeholder="Стек"
          value={stack}
          onChange={(event) => setStack(event.target.value)}
        />
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
        <span className="discovery-sort">
          Сортировка: по релевантности <Icon name="chevron" />
        </span>
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
      ) : !pool.data?.items.length ? (
        <State empty="Подходящие соискатели не найдены" />
      ) : (
        <div className="discovery-list">
          {pool.data.items.map((candidate) => (
            <motion.article
              layout
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
                  className={`discovery-like ${saved.includes(candidate.candidate_id) ? "saved" : ""}`}
                  aria-label={`Добавить в вакансию: ${candidateName(candidate.display_name)}`}
                  disabled={
                    busy === candidate.candidate_id ||
                    saved.includes(candidate.candidate_id)
                  }
                  onClick={() => void save(candidate)}
                >
                  <Icon name={saved.includes(candidate.candidate_id) ? "heart-filled" : "plus"} />
                </button>
              </div>
            </motion.article>
          ))}
          {pool.data.has_more && (
            <div className="pagination">
              <Action disabled={pageBusy} onClick={() => void loadMore()}>
                {pageBusy ? "Загружаем…" : "Показать ещё"}
              </Action>
            </div>
          )}
        </div>
      )}
      <AnimatePresence>
        {warning && (
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
              aria-label="Несоответствие требованиям"
            >
              <h2>Проверьте требования</h2>
              <p>Кандидат не соответствует обязательным фильтрам вакансии:</p>
              <ul>
                {warning.warning.warnings.map((item) => (
                  <li key={item}>{filterReasonLabel(item)}</li>
                ))}
              </ul>
              <div className="form-actions">
                <Action onClick={() => setWarning(null)}>Отмена</Action>
                <Action
                  variant="primary"
                  disabled={busy !== null}
                  onClick={() => {
                    void save(warning.candidate, warning);
                  }}
                >
                  Всё равно добавить
                </Action>
              </div>
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
  const [selected, setSelected] = useState(draftId || "");
  const [company, setCompany] = useState("");
  const [description, setDescription] = useState("");
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
  const [reason, setReason] = useState("");
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
        company_name: company.trim(),
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
    setBusy(true);
    try {
      if (process)
        await api.actOnRecruitment("employer", process.id, {
          action: "reject",
          reason: reason.trim(),
          expected_revision: process.revision,
          idempotency_key: mutationKey(
            JSON.stringify([
              "reject",
              process.id,
              process.revision,
              reason.trim(),
            ]),
          ),
        });
      else
        await api.rejectSavedCandidate(draftId, {
          candidate_id: candidateId,
          reason: reason.trim(),
          idempotency_key: mutationKey(
            JSON.stringify([
              "reject_saved",
              draftId,
              candidateId,
              reason.trim(),
            ]),
          ),
        });
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
            <p>Грейд: {gradeLabels[candidate.category.grade]}</p>
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
                    <Field label="Компания">
                      <input
                        required
                        value={company}
                        onChange={(event) => setCompany(event.target.value)}
                      />
                    </Field>
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
                  <Field label="Причина">
                    <textarea
                      required
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                    />
                  </Field>
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
  const interview = process?.interviews.some(
    (item) => item.state === "scheduled" || item.state === "completed",
  );
  const stage =
    process?.state === "hired"
      ? 4
      : process?.offers.length
        ? 3
        : interview
          ? 2
          : process
            ? 1
            : 0;
  return (
    <div className="discovery-milestones">
      {["Подбор", "Приглашение", "Собеседование", "Оффер", "Приём"].map(
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
  const [selection, setSelection] = useState<string[] | null>(null);
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
    page.processes.items.forEach((process) => {
      if (!byCandidate.has(process.candidate_id))
        byCandidate.set(process.candidate_id, { candidate: null, process });
    });
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
                type="checkbox"
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
                  type="checkbox"
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
                  <AnonymousAvatar name="Анонимный профиль" />
                  <div className="discovery-identity">
                    <h3>Анонимный профиль</h3>
                    <p>
                      {row.process
                        ? statusLabels[row.process.state]
                        : "Сохранён"}
                    </p>
                  </div>
                </>
              )}
              <Milestones process={row.process} />
              <Action
                className="discovery-dark"
                title={row.vacancy.fields.title || "Без названия"}
                onClick={() =>
                  navigate(
                    row.process
                      ? `/chat/${row.process.id}`
                      : `/resume/${row.id}?draft=${row.vacancy.draft_id}`,
                  )
                }
              >
                {row.process ? "Обратная связь" : "Открыть профиль"}
              </Action>
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
      name: candidateName(item.card?.display_name, "Анонимный профиль"),
      process: data.data?.processes.items.find(
        (process) => process.candidate_id === item.candidate_id,
      ),
      score: item.card?.explanation.score,
    }),
  );
  data.data?.processes.items.forEach((process) => {
    if (!rows.has(process.candidate_id))
      rows.set(process.candidate_id, {
        candidate: null,
        name: "Анонимный профиль",
        process,
      });
  });
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
              <Milestones process={row.process} />
              <Action
                className="discovery-dark"
                onClick={() =>
                  navigate(
                    row.process
                      ? `/chat/${row.process.id}`
                      : `/resume/${id}?draft=${draftId}`,
                  )
                }
              >
                {row.process ? "Обратная связь" : "Профиль кандидата"}
              </Action>
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
        <section className="panel statistics-summary">
          <h3>В работе</h3>
          <div className="statistics-summary-grid">
            <div>
              <Icon name="candidates" />
              <strong>{stats.unique_candidates}</strong>
              <span>кандидатов за период</span>
            </div>
            <div>
              <Icon name="resumes" />
              <strong>{stats.inventory.saved_pairs}</strong>
              <span>сохранено сейчас</span>
            </div>
            <div>
              <Icon name="clock" />
              <strong>{stats.inventory.active_processes}</strong>
              <span>активных процессов</span>
            </div>
          </div>
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

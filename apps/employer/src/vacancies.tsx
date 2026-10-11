import { InlineSearch, Tags, VacancyCardArrow } from "@comit/ui-kit/design";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { AnimatePresence, motion, useAnimationControls, useReducedMotion } from "framer-motion";
import { ApiError } from "@comit/api-client";
import type {
  AgentsDraftFields_Input,
  AgentsDraftView,
  FeedCard,
  FeedPage,
  SavedCard,
} from "@comit/shared";
import { api, asset, errorText, salary, useResource } from "./runtime";
import { Action, Field, Icon, PageTitle, Reveal, Select, State } from "./ui";
import { candidateName } from "./candidate-name";
import { domainChoices, featureLabel, softChoices, specializationLabel, stackChoices } from "./labels";
import "./vacancies.css";
import "./vacancy-suggestions.css";
import { changedRequirements, descriptionLength, suggestVacancyRequirements } from "./vacancy-suggestions";
import { SkillRadar, gradeLabel } from "./skill-radar";
import { useEmployerPreferences } from "./preferences";
import { usePersistentState, useClearPersistentState } from "@comit/ui-kit/ui-state";

type Navigate = (path: string) => void;
const formats = { onsite: "Офис", remote: "Удалённо", hybrid: "Гибрид" };
const classes = [
  "backend",
  "frontend",
  "fullstack",
  "mobile",
  "devops",
  "data",
  "qa",
  "ml",
] as const;
const status = (draft: AgentsDraftView) =>
  draft.archived
    ? "В архиве"
    : draft.business_status === "closed"
      ? "Приостановлена"
      : draft.business_status === "open"
        ? "Активная"
        : "Черновик";

/** Format the genuine lifecycle date for the card's current business status. */
function vacancyDate(draft: AgentsDraftView): string {
  const [label, timestamp] =
    draft.business_status === "open"
      ? ["Опубликована", draft.published_at]
      : draft.business_status === "closed"
        ? ["Закрыта", draft.closed_at]
        : ["Создана", draft.created_at];
  if (!timestamp) return `${label}: дата не указана`;
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime())
    ? `${label}: дата не указана`
    : `${label} ${new Intl.DateTimeFormat("ru-RU").format(date)}`;
}

/** Keep a circular badge for counts up to 99, widening only the capped value. */
function CountBadge({ count }: { count: number }) {
  return (
    <span className={`count vacancy-count${count > 99 ? " count-overflow" : ""}`}>
      {count > 99 ? "99+" : count}
    </span>
  );
}

/** Own vacancies with complete cursor pagination; status columns reflect all loaded pages. */
export function Vacancies({
  navigate,
  onCreate,
}: {
  navigate: Navigate;
  onCreate: () => void;
  onEdit?: (draft: AgentsDraftView) => void;
}) {
  const resource = useResource(async () => {
    const items: AgentsDraftView[] = [];
    let cursor: string | undefined;
    do {
      const page = await api.listVacancies(100, cursor);
      items.push(...page.items);
      cursor = page.has_more ? page.next_cursor || undefined : undefined;
    } while (cursor);
    return items;
  });
  const [query, setQuery] = usePersistentState("vacancies.query", "");
  const items = resource.data || [];
  const matches = (draft: AgentsDraftView, name: string) =>
    !draft.archived && draft.business_status === name;
  const filtered = items.filter(
    (draft) =>
      !draft.archived &&
      `${draft.fields.title || ""} ${draft.fields.city || ""} ${draft.fields.bio || ""}`
        .toLocaleLowerCase("ru")
        .includes(query.trim().toLocaleLowerCase("ru")),
  );
  return (
    <Reveal>
      <PageTitle title="Все вакансии">
        <Action variant="primary" onClick={onCreate}>
          Добавить вакансию
          <Icon name="arrow" />
        </Action>
        <InlineSearch className="vacancy-search" label="Поиск по вакансиям" value={query} onChange={setQuery} />
      </PageTitle>
      {resource.loading || resource.error ? (
        <State
          loading={resource.loading}
          error={resource.error}
          retry={resource.refresh}
        />
      ) : !items.some((draft) => !draft.archived) ? (
        <section className="state glass vacancy-first" role="status">
          <h2>Создайте первую вакансию</h2>
          <p>
            После публикации подбор сразу покажет подходящих кандидатов: листайте ленту, сохраняйте
            понравившихся и приглашайте их в чат.
          </p>
          <Action variant="primary" onClick={onCreate}>
            Добавить вакансию
            <Icon name="arrow" />
          </Action>
        </section>
      ) : (
        <div className="vacancy-columns">
          {[["draft", "Черновики"], ["open", "Открытые"], ["closed", "Закрытые"]].map(([key, label]) => (
            <section className="vacancy-column" key={key} aria-labelledby={`vacancies-${key}`}>
              <h2 className="filter-tab" id={`vacancies-${key}`}>
                {label} ({filtered.filter((draft) => matches(draft, key!)).length})
              </h2>
              <div className="vacancy-column-cards">
          {filtered.filter((draft) => matches(draft, key!)).map((draft) => (
            <motion.article
              layout
              key={draft.draft_id}
              className="vacancy-card glass"
            >
              <div className="status-pill">
                <span
                  className={`status-dot ${draft.business_status === "closed" ? "closed" : draft.business_status !== "open" ? "draft" : ""}`}
                />
                {status(draft)}
              </div>
              <h3>{draft.fields.title || "Без названия"}</h3>
              <Tags values={Object.keys(draft.fields.stacks || {}).slice(0, 3).map(skillLabel)} />
              <div className="salary-row">
                <strong>
                  {salary(draft.fields.zp_min, draft.fields.zp_max)}
                </strong>
                <small>за месяц, на руки</small>
              </div>
              <div className="vacancy-footer">
                <span className="date">
                  <Icon name="clock" />
                  {vacancyDate(draft)}
                </span>
                <VacancyCardArrow />
              </div>
              <button
                className="card-link"
                aria-label={`Открыть ${draft.fields.title || "вакансию"}`}
                onClick={() => navigate(`/vacancy/${draft.draft_id}`)}
              />
            </motion.article>
          ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </Reveal>
  );
}

type SkillLevel = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7;
const skillLevels: readonly SkillLevel[] = [1, 2, 3, 4, 5, 6, 7];
// Visual neighborhoods only: design/front-end, runtimes, storage and infrastructure.
// No matching coordinate indices or scoring weights change here.
const stackOrder = ["figma", "html_css_jquery", "tailwind", "react", "vue", "angular", "electron", "node", "php", "go", "python", "postgres", "mysql", "sqlite", "mongo", "docker"];
type SkillRequirements = NonNullable<AgentsDraftFields_Input["stacks"]>;
type ReviewTab = "info" | "stacks" | "domens" | "soft_skills";
const reviewTabs: readonly { id: ReviewTab; label: string }[] = [
  { id: "info", label: "Инфо" }, { id: "stacks", label: "Стек" },
  { id: "domens", label: "Хард" }, { id: "soft_skills", label: "Софт" },
];
/** Zero means absent in this UI; retaining an unused requirement would contradict removal. */
function selectedRequirements(values?: SkillRequirements): SkillRequirements {
  return Object.fromEntries(Object.entries(values || {}).filter(([, level]) => level > 0));
}
const skillLabel = (key: string) =>
  [...stackChoices, ...domainChoices, ...softChoices].find(([value]) => value === key)?.[1] || key;
const classLabels = {
  backend: "Backend",
  frontend: "Frontend",
  fullstack: "Fullstack",
  mobile: "Мобильная разработка",
  devops: "DevOps",
  data: "Аналитика данных",
  qa: "Тестирование",
  ml: "Машинное обучение",
};

/** Manual CAS editor followed by human confirmation of the exact approved summary. */
export function VacancyEditor({
  draft,
  onClose,
  onSaved,
  onAgent,
}: {
  draft?: AgentsDraftView;
  onClose: () => void;
  onSaved: (draft: AgentsDraftView) => void;
  onAgent: () => void;
}) {
  const systemReduceMotion = useReducedMotion();
  const { preferences } = useEmployerPreferences();
  const reduceMotion = systemReduceMotion || preferences.reducedMotion;
  const [current, setCurrent] = useState(draft);
  const clearDraft = useClearPersistentState();
  const formKey = `vacancy-editor.${draft?.draft_id || "new"}.${draft?.revision || 0}.`;
  const validRequirements = (value: unknown): value is SkillRequirements => value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.values(value).every(level => typeof level === "number" && Number.isInteger(level) && level >= 0 && level <= 7);
  const [title, setTitle] = usePersistentState(`${formKey}title`, draft?.fields.title || "");
  const [specialization, setSpecialization] = usePersistentState<(typeof classes)[number]>(`${formKey}specialization`,
    classes.find((value) => value === draft?.fields.class) || "frontend",
    (value): value is (typeof classes)[number] => classes.some(item => item === value),
  );
  const [skills, setSkills] = usePersistentState<SkillRequirements>(`${formKey}skills`, selectedRequirements(draft?.fields.stacks), validRequirements);
  const [domains, setDomains] = usePersistentState<SkillRequirements>(`${formKey}domains`, selectedRequirements(draft?.fields.domens), validRequirements);
  const [softSkills, setSoftSkills] = usePersistentState<SkillRequirements>(`${formKey}softSkills`, selectedRequirements(draft?.fields.soft_skills), validRequirements);
  const [reviewTab, setReviewTab] = useState<ReviewTab>("info");
  const [reviewDirty, setReviewDirty] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [from, setFrom] = usePersistentState(`${formKey}from`,
    draft?.fields.zp_min == null ? "" : String(draft.fields.zp_min / 1000),
  );
  const [to, setTo] = usePersistentState(`${formKey}to`,
    draft?.fields.zp_max == null ? "" : String(draft.fields.zp_max / 1000),
  );
  const [city, setCity] = usePersistentState(`${formKey}city`, draft?.fields.city || "");
  const [work, setWork] = usePersistentState<"onsite" | "remote" | "hybrid">(`${formKey}work`,
    draft?.fields.type || "hybrid",
    (value): value is "onsite" | "remote" | "hybrid" => value === "onsite" || value === "remote" || value === "hybrid",
  );
  const [daily, setDaily] = usePersistentState(`${formKey}daily`, draft?.fields.daily || "");
  const [bio, setBio] = usePersistentState(`${formKey}bio`, draft?.fields.bio || "");
  const [busy, setBusy] = useState(false);
  const [creationUncertain, setCreationUncertain] = useState(false);
  const [suggestionNote, setSuggestionNote] = useState<string | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const description = descriptionLength(bio);
  const saveLock = useRef(false);
  const manuallyEditedRequirements = useRef(new Set<string>());
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [published, setPublished] = useState(false);
  const completion = Math.round(
    ([title, from, to, city, daily, bio].filter((value) => value.trim())
      .length /
      6) *
      100,
  );
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [busy, onClose]);
  useEffect(() => {
    if (!published || !current) return;
    let active = true;
    const poll = async () => {
      try {
        await api.getVacancyReady(current.draft_id);
        if (active) {
          onSaved(current);
          onClose();
          clearDraft(formKey);
        }
      } catch {
        if (active) {
          try {
            const next = await api.getVacancy(current.draft_id);
            if (active) {
              setCurrent(next);
              if (next.state === "failed") {
                setPublished(false);
                setError(
                  "Публикация не завершилась. Обновите вакансию и повторите подтверждение.",
                );
              }
            }
          } catch (reason) {
            if (active) setError(errorText(reason));
          }
        }
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), 2500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [published, current?.draft_id, onSaved, onClose]);
  /** Persist the actual three skill maps with the current CAS revision; approval is separate. */
  async function persistFields(): Promise<AgentsDraftView> {
    const min = Number(from) * 1000, max = Number(to) * 1000;
    if (!from.trim() || !to.trim() || !Number.isSafeInteger(min) ||
      !Number.isSafeInteger(max) || min < 0 || max < min)
      throw new Error("Проверьте зарплатную вилку: от и до в тысячах рублей.");
    const fields: AgentsDraftFields_Input = {
      title: title.trim(), class: specialization,
      ...changedRequirements(current, { stacks: skills, domens: domains, soft_skills: softSkills }, manuallyEditedRequirements.current),
      zp_min: min, zp_max: max, city: city.trim(), daily: daily.trim(),
      type: work, bio: bio.trim(),
    };
    let saved: AgentsDraftView;
    if (current) {
      saved = await api.patchVacancy(current.draft_id, {
        expected_revision: current.revision, fields,
      });
    } else {
      try {
        saved = await api.createVacancy({ fields });
      } catch (reason) {
        // Creation has no idempotency contract: an ambiguous response must never create a duplicate.
        if (!(reason instanceof ApiError) || reason.status >= 500) {
          setCreationUncertain(true);
          throw new Error("Не удалось подтвердить создание. Черновик мог сохраниться. Закройте окно и проверьте список вакансий перед новой попыткой.");
        }
        throw reason;
      }
    }
    setCurrent(saved);
    return saved;
  }
  function showSaveError(reason: unknown) {
    setError(reason instanceof Error && !("status" in reason) ? reason.message : errorText(reason));
  }
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    // In-app validation instead of native browser bubbles: outline the gaps and focus the first one.
    const form = event.currentTarget;
    if (!form.checkValidity()) {
      form.classList.add("show-invalid");
      form.querySelector<HTMLElement>(":invalid")?.focus();
      setError("Заполните выделенные поля.");
      return;
    }
    form.classList.remove("show-invalid");
    if (saveLock.current || creationUncertain) return;
    saveLock.current = true;
    setBusy(true);
    setError(null);
    try {
      const saved = await persistFields();
      setSuggesting(true);
      setSuggestionNote(null);
      const result = await suggestVacancyRequirements(api, saved);
      setCurrent(result.draft);
      setSkills(selectedRequirements(result.draft.fields.stacks));
      setDomains(selectedRequirements(result.draft.fields.domens));
      setSoftSkills(selectedRequirements(result.draft.fields.soft_skills));
      setSuggestionNote(result.suggested
        ? "Требования предложены по описанию. Проверьте уровни во вкладках Стек, Хард и Софт. Ваши правки сохранены."
        : "Автоподбор требований пока недоступен. Укажите навыки и уровни во вкладках Стек, Хард и Софт вручную.");
      const approved = await api.approveVacancy(result.draft.draft_id, result.draft.revision);
      setCurrent(approved);
      setReviewDirty(false);
      setReviewTab("info");
      setConfirm(true);
    } catch (reason) {
      showSaveError(reason);
    } finally {
      saveLock.current = false;
      setBusy(false);
      setSuggesting(false);
    }
  };
  async function keepDraft() {
    if (!current || saveLock.current) return;
    saveLock.current = true;
    setBusy(true);
    setError(null);
    try {
      const saved = reviewDirty ? await persistFields() : current;
      onSaved(saved);
      clearDraft(formKey);
    } catch (reason) {
      showSaveError(reason);
    } finally {
      saveLock.current = false;
      setBusy(false);
    }
  }
  const publish = async () => {
    if (!current || saveLock.current) return;
    saveLock.current = true;
    setBusy(true);
    setError(null);
    try {
      let approved = current;
      if (reviewDirty) {
        const saved = await persistFields();
        approved = await api.approveVacancy(saved.draft_id, saved.revision);
        setCurrent(approved);
        setReviewDirty(false);
      }
      if (!approved.summary) throw new Error("Вернитесь к форме и проверьте вакансию ещё раз.");
      const result = await api.publishVacancy(approved.draft_id, {
        expected_revision: approved.revision,
        summary_hash: approved.summary.summary_hash,
      });
      setCurrent(result);
      setPublished(true);
    } catch (reason) {
      showSaveError(reason);
    } finally {
      saveLock.current = false;
      setBusy(false);
    }
  };
  const requirementPanels = [
    { id: "stacks", label: "Стек", values: skills, setValues: setSkills, choices: stackChoices, order: stackOrder },
    { id: "domens", label: "Хард", values: domains, setValues: setDomains, choices: domainChoices, order: domainChoices.map(([key]) => key) },
    { id: "soft_skills", label: "Софт", values: softSkills, setValues: setSoftSkills, choices: softChoices, order: softChoices.map(([key]) => key) },
  ] as const;
  return (
    <motion.div
      className="modal-shade"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onPointerDown={(event) => {
        if (event.target === event.currentTarget && !busy && !saveLock.current) onClose();
      }}
    >
      <motion.section
        className={`modal vacancy-editor${confirm ? " is-review" : ""}`}
        layout
        transition={{ layout: { duration: 0.32, ease: [0.22, 1, 0.36, 1] } }}
        data-reduced-motion={reduceMotion ? "true" : "false"}
        role="dialog"
        aria-modal="true"
        aria-label={confirm ? "Подтверждение публикации" : "Новая вакансия"}
        initial={{ scale: 0.94, y: 25 }}
        animate={{ scale: 1, y: 0 }}
      >
        {confirm ? (
          <>
            <div className="vacancy-review-tabs" role="tablist" aria-label="Проверка вакансии"
              onKeyDown={(event) => {
                const index = reviewTabs.findIndex((tab) => tab.id === reviewTab);
                const next = event.key === "ArrowRight" ? (index + 1) % 4
                  : event.key === "ArrowLeft" ? (index + 3) % 4
                    : event.key === "Home" ? 0 : event.key === "End" ? 3 : null;
                if (next == null || busy || published) return;
                event.preventDefault();
                const tab = reviewTabs[next];
                if (!tab) return;
                setReviewTab(tab.id);
                event.currentTarget.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
              }}>
              {reviewTabs.map((tab) => (
                <button key={tab.id} role="tab" type="button"
                  id={`vacancy-tab-${tab.id}`} aria-controls={`vacancy-panel-${tab.id}`}
                  aria-selected={reviewTab === tab.id} tabIndex={reviewTab === tab.id ? 0 : -1}
                  className={reviewTab === tab.id ? "active" : ""}
                  disabled={busy || published} onClick={() => setReviewTab(tab.id)}>
                  {tab.label}
                </button>
              ))}
            </div>
            {suggestionNote && <p className="vacancy-suggestion-note" role="status">{suggestionNote}</p>}
            <div className="vacancy-review-panels">
              <motion.div className="vacancy-review-panel vacancy-info"
                id="vacancy-panel-info" role="tabpanel" aria-labelledby="vacancy-tab-info"
                aria-hidden={reviewTab !== "info"} inert={reviewTab !== "info"}
                initial={false} animate={{ opacity: reviewTab === "info" ? 1 : 0, x: reviewTab === "info" ? 0 : -12 }}
                transition={{ duration: reduceMotion ? 0 : 0.35, ease: [0.22, 1, 0.36, 1] }}>
                <h3>{current?.summary?.fields.title}</h3>
                <dl className="vacancy-info-fields">
                  <div><dt>Зарплата</dt><dd>{salary(current?.summary?.fields.zp_min, current?.summary?.fields.zp_max)}</dd></div>
                  <div><dt>Специализация</dt><dd>{classLabels[specialization]}</dd></div>
                  <div><dt>Город</dt><dd>{current?.summary?.fields.city}</dd></div>
                  <div><dt>График</dt><dd>{current?.summary?.fields.daily}</dd></div>
                  <div><dt>Тип</dt><dd>{formats[work]}</dd></div>
                  <div><dt>Описание проекта</dt><dd>{current?.summary?.fields.bio || "Не указано"}</dd></div>
                </dl>
                {error && <p role="alert" className="form-error">{error}</p>}
            {published ? (
              <div role="status" className="inline-notice">
                Подготавливаем подбор кандидатов. После готовности окно закроется.
              </div>
            ) : (
              <div className="vacancy-review-actions">
                <Action className="glass-surface" onClick={() => setConfirm(false)} disabled={busy}>Назад</Action>
                <Action className="glass-surface" onClick={() => void keepDraft()} disabled={busy}>Черновик</Action>
                <Action className="review-publish" variant="light" disabled={busy || !current || (!current.summary && !reviewDirty)} onClick={() => void publish()}>
                  {busy ? "Сохраняем…" : "Подтвердить и опубликовать"}
                </Action>
              </div>
            )}
              </motion.div>
              {requirementPanels.map((panel) => {
                const label = (key: string) => panel.choices.find(([value]) => value === key)?.[1] || key;
                const ordered = Object.entries(panel.values).sort(([a], [b]) => {
                  const ai = panel.order.findIndex((key) => key === a), bi = panel.order.findIndex((key) => key === b);
                  return (ai < 0 ? 1000 : ai) - (bi < 0 ? 1000 : bi) || a.localeCompare(b);
                });
                return (
                  <motion.div key={panel.id} className="vacancy-review-panel vacancy-review-skills"
                    id={`vacancy-panel-${panel.id}`} role="tabpanel" aria-labelledby={`vacancy-tab-${panel.id}`}
                    aria-hidden={reviewTab !== panel.id} inert={reviewTab !== panel.id}
                    initial={false} animate={{ opacity: reviewTab === panel.id ? 1 : 0, x: reviewTab === panel.id ? 0 : 12 }}
                    transition={{ duration: reduceMotion ? 0 : 0.35, ease: [0.22, 1, 0.36, 1] }}>
                    <SkillRadar removeIcon={<span className="radar-remove-mark"><Icon name="radar-remove" /></span>} ariaLabel={`Требования: ${panel.label}`} disabled={busy || published}
                      axes={ordered.map(([key, value]) => ({ key, label: label(key), value }))}
                      onChange={(key, value) => {
                        const chosen = skillLevels.find((level) => level === value);
                        if (chosen === undefined) return;
                        manuallyEditedRequirements.current.add(panel.id);
                        panel.setValues((values) => ({ ...values, [key]: chosen }));
                        setReviewDirty(true);
                      }}
                      onRemove={(key) => {
                        manuallyEditedRequirements.current.add(panel.id);
                        panel.setValues((values) => { const next = { ...values }; delete next[key]; return next; });
                        setReviewDirty(true);
                      }}>
                    <Select className="radar-add" triggerIcon="plus" placement="right" aria-label={`Добавить требование: ${panel.label}`} value="" disabled={busy || published}
                      onChange={(event) => {
                        const choice = panel.choices.find(([key]) => key === event.target.value);
                        if (!choice) return;
                        manuallyEditedRequirements.current.add(panel.id);
                        panel.setValues((values) => ({ ...values, [choice[0]]: 4 }));
                        setReviewDirty(true);
                      }}>
                      <option value="" hidden>Добавить {panel.id === "stacks" ? "стек" : panel.id === "domens" ? "хард-навык" : "софт-навык"}</option>
                      {panel.choices.filter(([key]) => panel.values[key] === undefined).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
                    </Select>
                    </SkillRadar>
                  </motion.div>
                );
              })}
            </div>

          </>
        ) : (
          <>
            <div className="modal-tabs">
              <button className="active" type="button">
                Вручную
              </button>
              <button
                type="button"
                disabled={busy || creationUncertain}
                onClick={onAgent}
              >
                С AI-агентом
              </button>
            </div>
            <div className="progress-line">
              <span>Уже заполнено</span>
              <strong>{completion}%</strong>
            </div>
            <div className="progress-track">
              <span style={{ width: `${completion}%` }} />
            </div>
            <form className="form-grid" onSubmit={save} noValidate>
              <fieldset
                className="invite-fields"
                disabled={busy || creationUncertain}
              >
                <input
                  aria-label="Название должности"
                  placeholder="Название должности"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  required
                  maxLength={300}
                />
                <div className="form-row">
                  <Select
                    aria-label="Специализация"
                    value={specialization}
                    onChange={(event) => {
                      const value = classes.find(
                        (key) => key === event.target.value,
                      );
                      if (value) setSpecialization(value);
                    }}
                  >
                    {classes.map((key) => (
                      <option key={key} value={key}>
                        {classLabels[key]}
                      </option>
                    ))}
                  </Select>
                  <Action
                    type="button"
                    className="glass-surface stack-toggle"
                    aria-expanded={skillsOpen}
                    onClick={() => setSkillsOpen((value) => !value)}
                  >
                    Стек
                    {Object.keys(skills).length > 0
                      ? ` (${Object.keys(skills).length})`
                      : ""}
                    <Icon name="chevron" />
                  </Action>
                </div>
                <AnimatePresence initial={false}>
                {skillsOpen && (
                  <motion.div className="vacancy-skill-section" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.3 }}>
                  <div className="form-grid vacancy-skill-fields">
                    <Select
                      aria-label="Добавить навык"
                      triggerIcon="plus"
                      value=""
                      onChange={(event) => {
                        const choice = stackChoices.find(
                          ([key]) => key === event.target.value,
                        );
                        if (choice) {
                          manuallyEditedRequirements.current.add("stacks");
                          setSkills((value) => ({ ...value, [choice[0]]: 4 }));
                        }
                      }}
                    >
                      <option value="" hidden>Добавить навык</option>
                      {stackChoices
                        .filter(([key]) => skills[key] === undefined)
                        .map(([key, label]) => (
                          <option key={key} value={key}>
                            {label}
                          </option>
                        ))}
                    </Select>
                    {Object.entries(skills).map(([key, level]) => (
                      <div className="form-row skill-row" key={key}>
                        <Field label={skillLabel(key)}>
                          <Select
                            aria-label={`Требуемый уровень: ${skillLabel(key)}`}
                            value={level}
                            onChange={(event) => {
                              const chosen = skillLevels.find(
                                (value) => String(value) === event.target.value,
                              );
                              if (chosen !== undefined) {
                                manuallyEditedRequirements.current.add("stacks");
                                setSkills((value) => ({
                                  ...value,
                                  [key]: chosen,
                                }));
                              }
                            }}
                          >
                            {skillLevels.map((value) => (
                              <option key={value} value={value}>
                                {gradeLabel(value)}
                              </option>
                            ))}
                          </Select>
                        </Field>
                        <Action
                          type="button"
                          className="remove-skill glass-surface"
                          aria-label={`Удалить навык ${skillLabel(key)}`}
                          onClick={() =>
                            setSkills((value) => {
                              manuallyEditedRequirements.current.add("stacks");
                              const next = { ...value };
                              delete next[key];
                              return next;
                            })
                          }
                        >
                          Убрать
                        </Action>
                      </div>
                    ))}
                  </div>
                  </motion.div>
                )}
                </AnimatePresence>
                <div className="form-row">
                  <Field label="Вилка З/П от, тыс ₽">
                    <input
                      aria-label="Зарплата от"
                      type="number"
                      min="0"
                      step="1"
                      value={from}
                      onChange={(event) => setFrom(event.target.value)}
                      required
                    />
                  </Field>
                  <Field label="до, тыс ₽">
                    <input
                      aria-label="Зарплата до"
                      type="number"
                      min={from || "0"}
                      step="1"
                      value={to}
                      onChange={(event) => setTo(event.target.value)}
                      required
                    />
                  </Field>
                </div>
                <input
                  aria-label="Город"
                  placeholder="Город"
                  value={city}
                  onChange={(event) => setCity(event.target.value)}
                  required
                />
                <div className="form-row">
                  <Select
                    aria-label="Формат работы"
                    value={work}
                    onChange={(event) =>
                      setWork(event.target.value as typeof work)
                    }
                  >
                    {Object.entries(formats).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </Select>
                  <input
                    aria-label="График работы"
                    placeholder="График работы"
                    value={daily}
                    onChange={(event) => setDaily(event.target.value)}
                    required
                  />
                </div>
                <textarea
                  aria-label="Описание проекта"
                  aria-describedby="vacancy-description-length"
                  maxLength={12000}
                  placeholder="Описание проекта"
                  value={bio}
                  onChange={(event) => setBio(event.target.value)}
                  required
                />
                <p id="vacancy-description-length" className="vacancy-description-hint" data-tone={description.tone}>
                  {description.label} · {description.length} символов
                </p>
              </fieldset>
              <Action
                variant="light"
                type="submit"
                disabled={busy || creationUncertain}
                className="full-width"
              >
                {suggesting ? "Подбираем требования…" : busy ? "Сохраняем…" : "Проверить вакансию"}
              </Action>
            </form>
          </>
        )}
        {error && !confirm && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        {creationUncertain && (
          <Action onClick={onClose} className="full-width glass-surface">
            Проверить список вакансий
          </Action>
        )}
      </motion.section>
    </motion.div>
  );
}

/** Read actual pipeline states; unavailable services become the neutral empty state. */
async function feedPreparation(draftId: string, retry = false): Promise<boolean> {
  const pipeline = await api.getVacancyPipeline(draftId);
  if (pipeline.state !== "ready") {
    if (retry && pipeline.state === "failed") {
      await api.retryVacancyPreparation(draftId);
      return true;
    }
    return ["queued", "processing", "retrying"].includes(pipeline.state);
  }
  let main;
  try { main = await api.getSelectionStatus(draftId); }
  catch (reason) {
    if (!(reason instanceof ApiError) || reason.status !== 404) throw reason;
    if (retry) await api.requestSelection(draftId, { idempotency_key: crypto.randomUUID() });
    return true;
  }
  if (main.state === "failed") {
    if (retry) { await api.retrySelection(draftId); return true; }
    return false;
  }
  if (["queued", "processing", "retrying"].includes(main.state)) return true;
  if (main.state !== "ready" || !main.selected_count) return false;
  try {
    const rerank = await api.getRerankStatus(draftId);
    if (retry && rerank.state === "failed") { await api.retryRerank(draftId); return true; }
    return ["queued", "processing", "retrying"].includes(rerank.state);
  } catch (reason) {
    if (reason instanceof ApiError && reason.status === 404) return true;
    throw reason;
  }
}

/** Per-browser memory of the company name used in invitations (not personal data). */

/** Minimum drag distance (CSS px) or horizontal speed (px/s) that counts as a swipe decision. */
const SWIPE_DISTANCE = 90;
const SWIPE_VELOCITY = 600;

function CandidateDeck({
  draftId,
  navigate,
  onLiked,
}: {
  draftId: string;
  navigate: Navigate;
  onLiked: () => void;
}) {
  const resource = useResource(async () => {
    await api.getVacancyReady(draftId);
    return api.openFeed(draftId, { limit: 24 });
  }, [draftId]);
  const [index, setIndex] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controls = useAnimationControls();
  const systemReducedMotion = useReducedMotion();
  const { preferences } = useEmployerPreferences();
  const reducedMotion = systemReducedMotion || preferences.reducedMotion;
  const [feedState, setFeedState] = useState<"updating" | "empty">("updating");
  const [feedTick, setFeedTick] = useState(0);
  const refreshLock = useRef(false);
  const mounted = useRef(true);
  const waitStarted = useRef(Date.now());
  const decisions = useRef(
    new Map<string, { action: "like" | "dislike"; key: string }>(),
  );
  const impressions = useRef(new Map<string, string>());
  const visible = useRef<HTMLDivElement>(null);
  const lock = useRef(false);
  const card = resource.data?.items[index];
  useEffect(() => {
    setIndex(0);
  }, [resource.data?.session_id]);
  useEffect(() => {
    if (!card || !visible.current) return;
    const element = visible.current;
    let inFlight = false;
    const acknowledge = async () => {
      if (inFlight || document.visibilityState !== "visible") return;
      inFlight = true;
      let key = impressions.current.get(card.exposure_id);
      if (key === "done") return;
      if (!key) {
        key = crypto.randomUUID();
        impressions.current.set(card.exposure_id, key);
      }
      try {
        await api.acknowledgeFeedImpression(draftId, {
          exposure_id: card.exposure_id,
          idempotency_key: key,
        });
        impressions.current.set(card.exposure_id, "done");
      } catch {
        inFlight = false;
      }
    };
    const observer = new IntersectionObserver(
      (entries) => {
        if (
          entries.some(
            (entry) => entry.isIntersecting && entry.intersectionRatio >= 0.5,
          )
        )
          void acknowledge();
      },
      { threshold: 0.5 },
    );
    observer.observe(element);
    const onVisibility = () => {
      const rect = element.getBoundingClientRect();
      if (rect.top >= 0 && rect.bottom <= window.innerHeight)
        void acknowledge();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      observer.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [card?.exposure_id, draftId]);
  useEffect(() => {
    if (!card) return;
    const angle = index % 2 ? -12 : 9;
    controls.set({ x: 0, y: reducedMotion ? 0 : 24, opacity: 1, rotate: reducedMotion ? 0 : angle, scale: reducedMotion ? 1 : 0.96 });
    void controls.start({ y: reducedMotion ? 0 : [24, -24, 0], rotate: reducedMotion ? 0 : [angle, angle / 2, 0],
      scale: reducedMotion ? 1 : [0.96, 1.025, 1], transition: { duration: reducedMotion ? 0 : 0.5, ease: "easeInOut" } });
  }, [card?.exposure_id, controls, reducedMotion]);
  const decide = async (action: "like" | "dislike") => {
    if (!card || lock.current) return;
    lock.current = true;
    setPending(true);
    setError(null);
    const prior = decisions.current.get(card.exposure_id);
    if (prior && prior.action !== action) {
      setError(
        "Повторите прежнее действие: результат предыдущего запроса пока не подтверждён.",
      );
      await controls.start({ x: 0, y: 0, opacity: 1, rotate: 0, scale: 1, transition: { duration: reducedMotion ? 0 : 0.2 } });
      setPending(false);
      lock.current = false;
      return;
    }
    const identity = prior || { action, key: crypto.randomUUID() };
    decisions.current.set(card.exposure_id, identity);
    try {
      // Fly out immediately; the card comes back below if the decision is rejected.
      await Promise.all([
        api.decideFeed(draftId, {
          exposure_id: card.exposure_id,
          action,
          idempotency_key: identity.key,
        }),
        controls.start({
          x: action === "like" ? window.innerWidth : -window.innerWidth,
          y: 0,
          opacity: 0,
          rotate: action === "like" ? 18 : -18,
          transition: { duration: reducedMotion ? 0 : 0.32, ease: [0.4, 0, 1, 1] },
        }),
      ]);
      if (action === "like") onLiked();
      const page = resource.data;
      if (
        page &&
        index + 1 >= page.items.length &&
        page.has_more &&
        page.next_cursor
      ) {
        const next: FeedPage = await api.getFeed(
          draftId,
          page.session_id,
          page.next_cursor,
          24,
        );
        resource.setData({ ...next, items: [...page.items, ...next.items] });
      }
      setIndex((value) => value + 1);
    } catch (reason) {
      setError(errorText(reason));
      await controls.start({ x: 0, y: 0, opacity: 1, rotate: 0, scale: 1, transition: { duration: reducedMotion ? 0 : 0.2 } });
    } finally {
      setPending(false);
      lock.current = false;
    }
  };
  async function updateFeed(force = false) {
    if (refreshLock.current) return;
    refreshLock.current = true;
    if (force) { waitStarted.current = Date.now(); setFeedState("updating"); }
    try {
      const page = resource.data;
      if (force && page) await api.refillFeed(draftId, page.session_id);
      else if (force) await feedPreparation(draftId, true);
      const next = page
        ? await api.getFeed(draftId, page.session_id, page.next_cursor || page.cursor || undefined, 24)
        : await api.openFeed(draftId, { limit: 24 });
      if (!mounted.current) return;
      resource.setData(next);
      setIndex(0);
      const preparing = next.refill_state === "queued" || next.refill_state === "running";
      setFeedState(preparing && Date.now() - waitStarted.current < 90000 ? "updating" : "empty");
    } catch (reason) {
      if (mounted.current && reason instanceof ApiError && [409, 410].includes(reason.status) && resource.data) {
        resource.setData(null);
        setIndex(0);
      }
      let preparing = false;
      try { preparing = await feedPreparation(draftId); } catch { /* Dependencies degrade to the neutral empty state. */ }
      if (mounted.current) setFeedState(preparing && Date.now() - waitStarted.current < 90000 ? "updating" : "empty");
    } finally {
      refreshLock.current = false;
      if (mounted.current) setFeedTick((value) => value + 1);
    }
  }
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (resource.loading || card) return;
    // Preserve the immutable session and poll its tail instead of opening new sessions.
    const timer = window.setTimeout(() => void updateFeed(), feedState === "updating" ? 2000 : 15000);
    return () => window.clearTimeout(timer);
  }, [resource.loading, resource.data, resource.error, card, feedState, feedTick, draftId]);
  if (resource.loading || !card)
    return (
      <div className="deck-state">
        <section className="panel deck-feedback" role="status">
          <p>{resource.loading || feedState === "updating" ? "Обновляем ленту" : "Не можем найти подходящих кандидатов"}</p>
          {!resource.loading && feedState === "empty" && <Action className="glass-surface" onClick={() => void updateFeed(true)}>Обновить</Action>}
        </section>
      </div>
    );
  return (
    <div>
      <div className="candidate-deck" ref={visible}>
        <div className={`candidate-stack-card deck-tone-${(index + 2) % 3} stack-far`} style={{ transform: `rotate(${(index + 2) % 2 ? -12 : 9}deg)` }} aria-hidden="true" />
        <div className={`candidate-stack-card deck-tone-${(index + 1) % 3} stack-near`} style={{ transform: `rotate(${(index + 1) % 2 ? -12 : 9}deg)` }} aria-hidden="true" />
        <motion.div
          key={card.exposure_id}
          className={`candidate-swipe deck-tone-${index % 3}`}
          animate={controls}
          drag={!pending}
          dragMomentum={false}
          onDragStart={() => controls.stop()}
          onDragEnd={(_, info) => {
            // A short or slow drag is not a decision: return the card to the stack.
            if (Math.hypot(info.offset.x, info.offset.y) < SWIPE_DISTANCE && Math.abs(info.velocity.x) < SWIPE_VELOCITY) {
              void controls.start({ x: 0, y: 0, rotate: 0, transition: { type: "spring", stiffness: 420, damping: 30 } });
              return;
            }
            const bounds = visible.current?.closest(".selection-stack")?.getBoundingClientRect() || visible.current?.getBoundingClientRect();
            if (bounds) void decide(info.point.x >= bounds.left + bounds.width / 2 ? "like" : "dislike");
          }}
        >
          <h2>{candidateName(card.display_name)}</h2>
          <div className="tags">
            <span className="tag">{specializationLabel(card.category.specialization)}</span>
            {card.explanation.top_matched_features
              .slice(0, 2)
              .map((feature) => (
                <span className="tag" key={feature.key}>
                  {featureLabel(feature.key)}
                </span>
              ))}
          </div>
          <div className="candidate-description main">
            <p>Почему подходит</p>
            {card.explanation.top_matched_features
              .slice(0, 3)
              .map((feature) => (
                <p
                  className="candidate-factor"
                  key={`${feature.group}:${feature.key}`}
                >
                  {featureLabel(feature.key)}: {feature.candidate_level}/7 · нужно{" "}
                  {feature.target_level}/7
                </p>
              ))}
            {!card.explanation.top_matched_features.length && (
              <p className="candidate-factor">
                {card.filter_factors.join(" · ") ||
                  "Соответствует фильтрам текущего подбора"}
              </p>
            )}
          </div>
          <div className="candidate-description">
            {card.city} · {salary(card.salary.from_rub, card.salary.to_rub)}
          </div>
          <div className="candidate-description">
            Рейтинг подбора: {card.explanation.score.toFixed(3)} · ранг{" "}
            {card.rank}
          </div>
          <div className="candidate-controls" onPointerDown={(event) => event.stopPropagation()}>
            <Action aria-label="Пропустить кандидата" disabled={pending} onClick={() => void decide("dislike")}>
              <Icon name="close" className="deck-icon" />
            </Action>
            <Action aria-label="Открыть резюме" disabled={pending}
              onClick={() => navigate(`/resume/${card.candidate_id}?draft=${draftId}&from=vacancy`)}>
              <Icon name="resumes" className="deck-icon" />
            </Action>
            <Action aria-label="Сохранить кандидата" disabled={pending} onClick={() => void decide("like")}>
              <Icon name="heart" className="deck-icon" />
            </Action>
          </div>
        </motion.div>
      </div>
      <p className="deck-note">
        Потяните вправо, чтобы сохранить, влево — пропустить.
        <br />
        Контакты открываются после принятия приглашения.
      </p>
      {error && (
        <p role="status" className="deck-note">
          Не удалось сохранить действие. Попробуйте ещё раз
        </p>
      )}
    </div>
  );
}

/** Vacancy terms, anonymous live feed, shortlist and actually scheduled interviews. */
export function VacancyDetail({
  draftId,
  navigate,
  onEdit,
}: {
  draftId: string;
  navigate: Navigate;
  onEdit: (draft: AgentsDraftView) => void;
}) {
  const draft = useResource(() => api.getVacancy(draftId), [draftId]);
  const saved = useResource(() => api.getSaved(draftId, 100), [draftId]);
  const processes = useResource(
    () => api.getEmployerRecruitment(draftId, 100),
    [draftId],
  );
  const [invite, setInvite] = useState<SavedCard | null>(null);
  const clearDraft = useClearPersistentState();
  const organization = useResource(() => api.getEmployerProfile());
  const company = organization.data?.company_name || "";
  const [description, setDescription] = usePersistentState(`vacancy.${draftId}.invitation.description`, "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inviteKey = useRef(crypto.randomUUID());
  const [lifecycle, setLifecycle] = useState<"publish" | "withdraw" | "close" | "delete" | null>(null);
  const lifecycleLock = useRef(false);
  const status = draft.data?.business_status;
  useEffect(() => {
    if (draft.data?.state !== "creating") return;
    let active = true;
    let timer: number;
    const poll = async () => {
      try {
        const latest = await api.getVacancy(draftId);
        if (active) draft.setData(latest);
      } catch {
        if (active) timer = window.setTimeout(() => void poll(), 3000);
      }
    };
    timer = window.setTimeout(() => void poll(), 1500);
    return () => { active = false; window.clearTimeout(timer); };
  }, [draft.data, draft.setData, draftId]);
  const lifecyclePending = busy || draft.data?.state === "creating" || draft.data?.archived;
  const lifecycleLabels = { publish: "Опубликовать", withdraw: "Отозвать", close: "Закрыть вакансию", delete: "Удалить вакансию" };
  useEffect(() => {
    if (!lifecycle) return;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !lifecycleLock.current) setLifecycle(null);
    };
    document.addEventListener("keydown", dismiss);
    return () => document.removeEventListener("keydown", dismiss);
  }, [lifecycle]);
  async function confirmLifecycle() {
    if (!lifecycle || !draft.data || lifecycleLock.current) return;
    lifecycleLock.current = true;
    setBusy(true);
    setError(null);
    try {
      const snapshot = draft.data;
      if (lifecycle === "publish") {
        const approved = await api.approveVacancy(draftId, snapshot.revision);
        if (!approved.summary) throw new Error("Не удалось получить подтверждённые данные вакансии");
        await api.publishVacancy(draftId, {
          expected_revision: approved.revision,
          summary_hash: approved.summary.summary_hash,
        });
      } else if (lifecycle === "withdraw") {
        await api.withdrawVacancy(draftId, snapshot.revision);
      } else if (lifecycle === "close") {
        await api.closeVacancy(draftId, snapshot.revision);
      } else {
        await api.archiveVacancy(draftId, snapshot.revision);
        navigate("/vacancies");
      }
      setLifecycle(null);
      saved.refresh();
      processes.refresh();
    } catch (reason) {
      setError(reason instanceof Error && !("status" in reason) ? reason.message : errorText(reason));
    } finally {
      draft.refresh();
      lifecycleLock.current = false;
      setBusy(false);
    }
  }
  // Republishing restores saved cards asynchronously; poll briefly until they come back.
  const missingCards = !!saved.data?.items.some((item) => !item.card);
  const cardPolls = useRef(0);
  useEffect(() => {
    if (!missingCards || draft.data?.business_status !== "open") {
      cardPolls.current = 0;
      return;
    }
    if (cardPolls.current >= 20) return;
    const timer = window.setTimeout(() => {
      cardPolls.current += 1;
      saved.refresh();
    }, 3000);
    return () => window.clearTimeout(timer);
  }, [missingCards, draft.data?.business_status, saved.data]);
  const fields = draft.data?.fields;
  const interviews = (processes.data?.items || []).flatMap((process) =>
    process.interviews
      .filter(
        (interview) =>
          interview.state === "scheduled" || interview.state === "proposed",
      )
      .map((interview) => ({ process, interview })),
  );
  const sendInvite = async (event: FormEvent) => {
    event.preventDefault();
    if (!invite || !company.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const process = await api.inviteCandidate(draftId, {
        candidate_id: invite.candidate_id,
        description: description.trim(),
        idempotency_key: inviteKey.current,
      });
      clearDraft(`vacancy.${draftId}.invitation.description`);
      setInvite(null);
      processes.refresh();
      navigate(`/chat/${process.id}`);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  };
  if (draft.loading || draft.error || !fields)
    return (
      <State
        loading={draft.loading}
        error={draft.error}
        retry={draft.refresh}
      />
    );
  return (
    <Reveal>
      <PageTitle title="К вакансиям" back={() => navigate("/vacancies")}>
        <Action
          variant="primary"
          disabled={lifecyclePending || status === "closed"}
          onClick={() => draft.data && onEdit(draft.data)}
        >
          Редактировать
          <span className="edit-badge">
            <Icon name="edit" />
          </span>
        </Action>
        {(status === "draft" || status === "open") && (
          <Action variant="primary" className="vacancy-publication" disabled={lifecyclePending}
            onClick={() => { setError(null); setLifecycle(status === "open" ? "withdraw" : "publish"); }}>
            {status === "open" ? "Отозвать" : "Опубликовать"}
          </Action>
        )}
      </PageTitle>
      <div className="detail-columns">
        <div className="detail-stack">
          <section className="panel">
            <h2>Данные вакансии</h2>
            <p className="muted vacancy-subtitle">{fields.title}</p>
            <div className="detail-fields">
              <div className="form-row">
                <Field label="Вилка З/П от, тыс ₽">
                  <input
                    readOnly
                    value={fields.zp_min == null ? "" : fields.zp_min / 1000}
                  />
                </Field>
                <Field label="до, тыс ₽">
                  <input
                    readOnly
                    value={fields.zp_max == null ? "" : fields.zp_max / 1000}
                  />
                </Field>
              </div>
              <div className="form-row">
                <input
                  aria-label="Формат"
                  readOnly
                  value={fields.type ? formats[fields.type] : "Не указан"}
                />
                <input
                  aria-label="Город"
                  readOnly
                  value={fields.city || "Не указан"}
                />
              </div>
              {[
                ...Object.entries(fields.stacks || {}),
                ...Object.entries(fields.domens || {}),
                ...Object.entries(fields.soft_skills || {}),
              ].filter(([, value]) => value > 0).map(([key, value]) => (
                <div className="skill-row" key={key}>
                  <header>
                    <span>{skillLabel(key)}</span>
                    <span>{gradeLabel(value)}</span>
                  </header>
                  <div className="skill-track">
                    <span style={{ width: `${(value / 7) * 100}%` }} />
                  </div>
                </div>
              ))}
              {!Object.keys(fields.stacks || {}).length && (
                <p className="muted">
                  Специализация: {fields.class ? specializationLabel(fields.class) : "Не указана"}
                </p>
              )}
            </div>
          </section>
          <section className="panel">
            <div className="small-heading">
              <h2>Понравились в ленте</h2>
              <CountBadge count={saved.data?.items.length || 0} />
              <button
                className="shortlist-all"
                onClick={() => navigate(`/vacancy/${draftId}/resumes`)}
              >
                Все →
              </button>
            </div>
            {saved.error && draft.data?.business_status !== "open" ? (
              // Unpublished drafts have no selection yet; the API answers 404 rather than an empty page.
              <p className="empty-note">
                Кандидаты появятся после публикации вакансии
              </p>
            ) : saved.loading || saved.error ? (
              <State
                loading={saved.loading}
                error={saved.error}
                retry={saved.refresh}
              />
            ) : !saved.data?.items.length ? (
              <p className="empty-note">
                Сохраните понравившихся кандидатов из подборки
              </p>
            ) : (
              <div className="saved-row">
                {saved.data.items.map((item) => (
                  <div className="saved-card" key={item.candidate_id}>
                    <button
                      className="saved-resume"
                      aria-label={`Резюме: ${candidateName(item.card?.display_name)}`}
                      onClick={() =>
                        navigate(
                          `/resume/${item.candidate_id}?draft=${draftId}&from=vacancy`,
                        )
                      }
                    >
                      <span className="saved-initial">
                        {candidateName(item.card?.display_name).slice(0, 1)}
                      </span>
                      <h3>{candidateName(item.card?.display_name)}</h3>
                      <small>
                        {item.card
                          ? specializationLabel(item.card.category.specialization)
                          : "Профиль сейчас недоступен"}
                      </small>
                    </button>
                    {(() => {
                      // An invited candidate already has a process: open its chat instead of a second invite.
                      const process = processes.data?.items.find(
                        (entry) => entry.candidate_id === item.candidate_id,
                      );
                      return process ? (
                        <Action onClick={() => navigate(`/chat/${process.id}`)}>
                          Открыть чат
                        </Action>
                      ) : (
                        <Action
                          onClick={() => {
                            inviteKey.current = crypto.randomUUID();
                            setError(null);
                            setInvite(item);
                          }}
                          disabled={
                            !item.card || draft.data?.business_status !== "open"
                          }
                        >
                          Пригласить
                        </Action>
                      );
                    })()}
                  </div>
                ))}
              </div>
            )}
          </section>
          <section className="panel">
            <div className="small-heading">
              <h2>Назначенные собеседования</h2>
              <CountBadge count={interviews.length} />
            </div>
            {processes.loading || processes.error ? (
              <State
                loading={processes.loading}
                error={processes.error}
                retry={processes.refresh}
              />
            ) : !interviews.length ? (
              <p className="empty-note">Собеседований пока нет</p>
            ) : (
              interviews.map(({ process, interview }) => (
                <div className="interview-row" key={interview.id}>
                  <div>
                    <h3>
                      {candidateName(saved.data?.items.find(
                        (item) => item.candidate_id === process.candidate_id,
                      )?.card?.display_name)}
                    </h3>
                    <small>
                      {new Date(interview.starts_at).toLocaleString("ru-RU", {
                        timeZone: interview.time_zone,
                        weekday: "long",
                        day: "numeric",
                        month: "long",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                      {interview.state === "proposed" &&
                        " · Ожидает подтверждения"}
                    </small>
                  </div>
                  <Action
                    disabled={!process.chat_available}
                    onClick={() => navigate(`/chat/${process.id}`)}
                  >
                    Перейти в чат
                  </Action>
                </div>
              ))
            )}
          </section>
          <div className="vacancy-lifecycle-actions">
            {status !== "closed" && <Action className="glass-surface" disabled={lifecyclePending}
              onClick={() => { setError(null); setLifecycle("close"); }}>Закрыть вакансию</Action>}
            {(status === "draft" || status === "closed") && <Action className="glass-surface" disabled={lifecyclePending}
              onClick={() => { setError(null); setLifecycle("delete"); }}>Удалить</Action>}
          </div>
        </div>
        <aside className="selection-stack">
          {draft.data?.business_status === "open" ? (
            <CandidateDeck
              key={draftId}
              draftId={draftId}
              navigate={navigate}
              onLiked={saved.refresh}
            />
          ) : (
            <State empty="Подбор доступен после публикации вакансии" />
          )}
        </aside>
      </div>
      <AnimatePresence>
        {lifecycle && (
          <motion.div className="modal-shade" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onPointerDown={(event) => { if (event.target === event.currentTarget && !busy) setLifecycle(null); }}>
            <section className="modal account-confirmation glass" role="dialog" aria-modal="true" aria-labelledby="vacancy-lifecycle-title">
              <h2 id="vacancy-lifecycle-title">{lifecycleLabels[lifecycle]}?</h2>
              <p>{lifecycle === "publish"
                ? "Вакансия появится в подборе. Приглашения и переписка с кандидатами станут доступны."
                : lifecycle === "withdraw"
                  ? "Вакансия станет черновиком, подбор остановится. Лайки и история переписки сохранятся, чаты станут доступны только для чтения, назначенные собеседования отменятся. Возможна повторная публикация."
                  : lifecycle === "close"
                    ? "Вакансия закроется навсегда. Подбор и приглашения остановятся, чаты станут доступны только для чтения, собеседования отменятся. Лайки и история сохранятся. Повторная публикация невозможна."
                    : "Вакансия, её подбор, лайки, приглашения и связанная переписка будут полностью удалены. Восстановить их нельзя."}</p>
              {error && <p role="alert" className="form-error">{error}</p>}
              <div className="account-confirmation-actions">
                <Action disabled={busy} onClick={() => setLifecycle(null)}>Отмена</Action>
                <Action variant="primary" className={lifecycle === "close" || lifecycle === "delete" ? "action-danger" : ""} disabled={busy} onClick={() => void confirmLifecycle()}>{busy ? "Выполняем…" : lifecycleLabels[lifecycle]}</Action>
              </div>
            </section>
          </motion.div>
        )}
        {invite && (
          <motion.div
            className="modal-shade"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <section
              className="modal"
              role="dialog"
              aria-modal="true"
              aria-label="Пригласить кандидата"
            >
              <Action
                className="circle modal-close"
                disabled={busy}
                onClick={() => setInvite(null)}
                aria-label="Закрыть"
              >
                ×
              </Action>
              <h2>Приглашение</h2>
              <p>{candidateName(invite.card?.display_name)}</p>
              <p className="salary-row">
                {salary(fields.zp_min, fields.zp_max)}
              </p>
              <form className="form-grid" onSubmit={sendInvite}>
                <fieldset className="invite-fields" disabled={busy}>
                  <Field label="Компания"><p>{company || "Название компании пока не указано"}</p></Field>{organization.error && <State error={organization.error} retry={organization.refresh} />}{!company && <Action type="button" onClick={() => navigate("/settings")}>Указать компанию в настройках</Action>}
                  <Field label="Сообщение кандидату">
                    <textarea
                      value={description}
                      required
                      maxLength={10000}
                      onChange={(event) => {
                        setDescription(event.target.value);
                        inviteKey.current = crypto.randomUUID();
                      }}
                    />
                  </Field>
                </fieldset>
                <Action type="submit" variant="light" disabled={busy || !company.trim()}>
                  Отправить приглашение
                </Action>
                {error && (
                  <p role="alert" className="form-error">
                    {error}
                  </p>
                )}
              </form>
            </section>
          </motion.div>
        )}
      </AnimatePresence>
    </Reveal>
  );
}

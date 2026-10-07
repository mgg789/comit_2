import { useEffect, useRef, useState, type FormEvent } from "react";
import { AnimatePresence, motion, useAnimationControls } from "framer-motion";
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
import "./vacancies.css";

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

/** Own vacancies with complete cursor pagination; filter counts reflect all loaded pages. */
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
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const items = resource.data || [];
  const matches = (draft: AgentsDraftView, name: string) =>
    !draft.archived && (name === "all" || draft.business_status === name);
  const filtered = items.filter(
    (draft) =>
      matches(draft, filter) &&
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
        <label className="vacancy-search discovery-search">
          <input
            aria-label="Поиск по вакансиям"
            placeholder="Поиск по вакансиям"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <span className="search-glyph">
            <Icon name="search" />
          </span>
        </label>
      </PageTitle>
      <div
        className="vacancy-filters"
        role="tablist"
        aria-label="Статус вакансий"
      >
        {[
          ["all", "Все"],
          ["open", "Открытые"],
          ["closed", "Закрытые"],
        ].map(([key, label]) => (
          <button
            key={key}
            className="filter-tab"
            role="tab"
            aria-selected={filter === key}
            onClick={() => setFilter(key!)}
          >
            {label}({items.filter((draft) => matches(draft, key!)).length})
          </button>
        ))}
      </div>
      {resource.loading || resource.error ? (
        <State
          loading={resource.loading}
          error={resource.error}
          retry={resource.refresh}
        />
      ) : !filtered.length ? (
        <State empty="В этом разделе пока нет вакансий" />
      ) : (
        <div className="vacancy-grid">
          {filtered.map((draft) => (
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
              <div className="tags">
                {Object.keys(draft.fields.stacks || {})
                  .slice(0, 3)
                  .map((key) => (
                    <span className="tag" key={key}>
                      {skillLabel(key)}
                    </span>
                  ))}
              </div>
              <div className="salary-row">
                <strong>
                  {salary(draft.fields.zp_min, draft.fields.zp_max)}
                </strong>
                <small>за месяц, на руки</small>
              </div>
              <div className="vacancy-footer">
                <span className="date">
                  <Icon name="clock" />
                  Версия {draft.revision}
                </span>
                <span className="card-arrow">›</span>
              </div>
              <button
                className="card-link"
                aria-label={`Открыть ${draft.fields.title || "вакансию"}`}
                onClick={() => navigate(`/vacancy/${draft.draft_id}`)}
              />
            </motion.article>
          ))}
        </div>
      )}
    </Reveal>
  );
}

type SkillLevel = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7;
// Canonical stack keys from matching_engine/strict.py INITIAL_GROUPS; keep labels human-readable.
const stackChoices = [
  ["python", "Python"],
  ["node", "Node.js"],
  ["go", "Go"],
  ["php", "PHP"],
  ["react", "React"],
  ["vue", "Vue"],
  ["angular", "Angular"],
  ["html_css_jquery", "HTML / CSS / jQuery"],
  ["tailwind", "Tailwind CSS"],
  ["electron", "Electron"],
  ["figma", "Figma"],
  ["postgres", "PostgreSQL"],
  ["mongo", "MongoDB"],
  ["sqlite", "SQLite"],
  ["mysql", "MySQL"],
  ["docker", "Docker"],
] as const;
const skillLevels: readonly SkillLevel[] = [0, 1, 2, 3, 4, 5, 6, 7];
const skillLabel = (key: string) =>
  stackChoices.find(([value]) => value === key)?.[1] || key;
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
  const [current, setCurrent] = useState(draft);
  const [title, setTitle] = useState(draft?.fields.title || "");
  const [specialization, setSpecialization] = useState<
    NonNullable<AgentsDraftFields_Input["class"]>
  >(draft?.fields.class || "frontend");
  const [skills, setSkills] = useState<
    NonNullable<AgentsDraftFields_Input["stacks"]>
  >({ ...draft?.fields.stacks });
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [from, setFrom] = useState(
    draft?.fields.zp_min == null ? "" : String(draft.fields.zp_min / 1000),
  );
  const [to, setTo] = useState(
    draft?.fields.zp_max == null ? "" : String(draft.fields.zp_max / 1000),
  );
  const [city, setCity] = useState(draft?.fields.city || "");
  const [work, setWork] = useState<"onsite" | "remote" | "hybrid">(
    draft?.fields.type || "hybrid",
  );
  const [daily, setDaily] = useState(draft?.fields.daily || "");
  const [bio, setBio] = useState(draft?.fields.bio || "");
  const [busy, setBusy] = useState(false);
  const [creationUncertain, setCreationUncertain] = useState(false);
  const saveLock = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [published, setPublished] = useState(false);
  const completion = Math.round(
    ([title, from, to, city, daily, bio, specialization].filter(Boolean)
      .length /
      7) *
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
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (saveLock.current || creationUncertain) return;
    saveLock.current = true;
    setBusy(true);
    setError(null);
    try {
      const min = Number(from) * 1000,
        max = Number(to) * 1000;
      if (
        !Number.isSafeInteger(min) ||
        !Number.isSafeInteger(max) ||
        min < 0 ||
        max < min
      )
        throw new Error(
          "Проверьте зарплатную вилку: от и до в тысячах рублей.",
        );
      const fields: AgentsDraftFields_Input = {
        title: title.trim(),
        class: specialization,
        stacks: skills,
        zp_min: min,
        zp_max: max,
        city: city.trim(),
        daily: daily.trim(),
        type: work,
        bio: bio.trim(),
      };
      let saved: AgentsDraftView;
      if (current)
        saved = await api.patchVacancy(current.draft_id, {
          expected_revision: current.revision,
          fields,
        });
      else {
        try {
          saved = await api.createVacancy({ fields });
        } catch (reason) {
          // Creation has no idempotency contract: an ambiguous response must never create a duplicate.
          if (!(reason instanceof ApiError) || reason.status >= 500) {
            setCreationUncertain(true);
            throw new Error(
              "Не удалось подтвердить создание. Черновик мог сохраниться. Закройте окно и проверьте список вакансий перед новой попыткой.",
            );
          }
          throw reason;
        }
      }
      setCurrent(saved);
      const approved = await api.approveVacancy(saved.draft_id, saved.revision);
      setCurrent(approved);
      setConfirm(true);
    } catch (reason) {
      setError(
        reason instanceof Error && !("status" in reason)
          ? reason.message
          : errorText(reason),
      );
    } finally {
      saveLock.current = false;
      setBusy(false);
    }
  };
  const publish = async () => {
    if (!current?.summary || saveLock.current) return;
    saveLock.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await api.publishVacancy(current.draft_id, {
        expected_revision: current.revision,
        summary_hash: current.summary.summary_hash,
      });
      setCurrent(result);
      setPublished(true);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      saveLock.current = false;
      setBusy(false);
    }
  };
  return (
    <motion.div
      className="modal-shade"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <motion.section
        className="modal vacancy-editor"
        role="dialog"
        aria-modal="true"
        aria-label={confirm ? "Подтверждение публикации" : "Новая вакансия"}
        initial={{ scale: 0.94, y: 25 }}
        animate={{ scale: 1, y: 0 }}
      >
        <Action
          className="circle glass modal-close"
          disabled={busy}
          onClick={onClose}
          aria-label="Закрыть"
        >
          ×
        </Action>
        {confirm ? (
          <>
            <h2>{published ? "Публикуем вакансию" : "Проверьте вакансию"}</h2>
            <div className="summary-copy">
              <h3>{current?.summary?.fields.title}</h3>
              <p>
                {salary(
                  current?.summary?.fields.zp_min,
                  current?.summary?.fields.zp_max,
                )}
              </p>
              <p>
                {current?.summary?.fields.city} ·{" "}
                {current?.summary?.fields.daily}
              </p>
              <p>
                {current?.summary?.fields.type &&
                  formats[current.summary.fields.type]}
              </p>
              <p>{current?.summary?.fields.bio}</p>
              <div className="tags">
                {Object.entries(current?.summary?.fields.stacks || {}).map(
                  ([key, value]) => (
                    <span key={key} className="tag">
                      {skillLabel(key)} · {value}/7
                    </span>
                  ),
                )}
              </div>
            </div>
            {published ? (
              <div role="status" className="inline-notice">
                Подготавливаем подбор кандидатов. После готовности окно
                закроется.
              </div>
            ) : (
              <div className="form-actions">
                <Action onClick={() => setConfirm(false)} disabled={busy}>
                  Изменить
                </Action>
                <Action
                  variant="light"
                  disabled={busy || !current?.summary}
                  onClick={() => void publish()}
                >
                  Подтвердить и опубликовать
                </Action>
              </div>
            )}
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
            <form className="form-grid" onSubmit={save}>
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
                  maxLength={500}
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
                {skillsOpen && (
                  <div className="form-grid">
                    <Select
                      aria-label="Добавить навык"
                      value=""
                      onChange={(event) => {
                        const choice = stackChoices.find(
                          ([key]) => key === event.target.value,
                        );
                        if (choice)
                          setSkills((value) => ({ ...value, [choice[0]]: 4 }));
                      }}
                    >
                      <option value="">Добавить навык</option>
                      {stackChoices
                        .filter(([key]) => skills[key] === undefined)
                        .map(([key, label]) => (
                          <option key={key} value={key}>
                            {label}
                          </option>
                        ))}
                    </Select>
                    {Object.entries(skills).map(([key, level]) => (
                      <div className="form-row" key={key}>
                        <Field label={skillLabel(key)}>
                          <Select
                            aria-label={`Требуемый уровень: ${skillLabel(key)}`}
                            value={level}
                            onChange={(event) => {
                              const chosen = skillLevels.find(
                                (value) => String(value) === event.target.value,
                              );
                              if (chosen !== undefined)
                                setSkills((value) => ({
                                  ...value,
                                  [key]: chosen,
                                }));
                            }}
                          >
                            {skillLevels.map((value) => (
                              <option key={value} value={value}>
                                {value} / 7
                                {value === 0
                                  ? " — не требуется"
                                  : value === 1
                                    ? " — основы"
                                    : value === 4
                                      ? " — уверенно"
                                      : value === 7
                                        ? " — эксперт"
                                        : ""}
                              </option>
                            ))}
                          </Select>
                        </Field>
                        <Action
                          type="button"
                          aria-label={`Удалить навык ${skillLabel(key)}`}
                          onClick={() =>
                            setSkills((value) => {
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
                )}
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
                    placeholder="График: 5/2"
                    value={daily}
                    onChange={(event) => setDaily(event.target.value)}
                    required
                  />
                </div>
                <textarea
                  aria-label="Описание проекта"
                  placeholder="Описание проекта"
                  value={bio}
                  onChange={(event) => setBio(event.target.value)}
                  required
                />
              </fieldset>
              <small>
                Для каждого навыка можно выбрать требуемый уровень знания.
              </small>
              <Action
                variant="light"
                type="submit"
                disabled={busy || creationUncertain}
                className="full-width"
              >
                {busy ? "Проверяем…" : "Проверить вакансию"}
              </Action>
            </form>
          </>
        )}
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        {creationUncertain && (
          <Action onClick={onClose} className="full-width">
            Проверить список вакансий
          </Action>
        )}
      </motion.section>
    </motion.div>
  );
}

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
  const selectionKey = useRef(crypto.randomUUID());
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
      setPending(false);
      lock.current = false;
      return;
    }
    const identity = prior || { action, key: crypto.randomUUID() };
    decisions.current.set(card.exposure_id, identity);
    try {
      await api.decideFeed(draftId, {
        exposure_id: card.exposure_id,
        action,
        idempotency_key: identity.key,
      });
      await controls.start({
        x: action === "like" ? 550 : -550,
        opacity: 0,
        rotate: action === "like" ? 18 : -18,
        transition: { duration: 0.25 },
      });
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
    } finally {
      controls.set({ x: 0, opacity: 1, rotate: 0 });
      setPending(false);
      lock.current = false;
    }
  };
  const rerun = async () => {
    setPending(true);
    setError(null);
    try {
      await api.getVacancyReady(draftId);
      await api.requestSelection(draftId, {
        idempotency_key: selectionKey.current,
      });
      selectionKey.current = crypto.randomUUID();
      setError("Подбор запущен. Обновите ленту, когда обработка завершится.");
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setPending(false);
    }
  };
  if (resource.loading || resource.error)
    return (
      <div className="deck-state">
        <State
          loading={resource.loading}
          error={resource.error}
          retry={resource.refresh}
        />
        <Action disabled={pending} onClick={() => void rerun()}>
          Запустить подбор
        </Action>
        {error && <p className="form-error">{error}</p>}
      </div>
    );
  if (!card)
    return (
      <div className="deck-state">
        <State
          empty="Вы просмотрели текущую подборку"
          retry={resource.refresh}
        />
        <Action disabled={pending} onClick={() => void rerun()}>
          Обновить подбор
        </Action>
        {error && <p className="inline-notice">{error}</p>}
      </div>
    );
  return (
    <div>
      <div className="candidate-deck" ref={visible}>
        <motion.div
          key={card.exposure_id}
          className="candidate-swipe"
          animate={controls}
          drag={pending ? false : "x"}
          dragConstraints={{ left: 0, right: 0 }}
          dragElastic={0.7}
          onDragEnd={(_, info) => {
            if (Math.abs(info.offset.x) > 90 || Math.abs(info.velocity.x) > 600)
              void decide(info.offset.x > 0 ? "like" : "dislike");
          }}
        >
          <h2>{card.display_name}</h2>
          <div className="tags">
            <span className="tag">{card.category.specialization}</span>
            {card.explanation.top_matched_features
              .slice(0, 2)
              .map((feature) => (
                <span className="tag" key={feature.key}>
                  {feature.key}
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
                  {feature.key}: {feature.candidate_level}/7 · нужно{" "}
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
          <div className="candidate-controls">
            <Action
              aria-label="Пропустить кандидата"
              disabled={pending}
              onClick={() => void decide("dislike")}
            >
              ×
            </Action>
            <Action
              aria-label="Сохранить кандидата"
              disabled={pending}
              onClick={() => void decide("like")}
            >
              <img
                className="deck-icon"
                src={asset("b59c95d115df082750c73998ecdbaee2e504f7ae.svg")}
                alt=""
              />
            </Action>
            <Action
              aria-label="Открыть резюме"
              disabled={pending}
              onClick={() =>
                navigate(`/resume/${card.candidate_id}?draft=${draftId}`)
              }
            >
              <img
                className="deck-icon"
                src={asset("19847931f7f73800e0a10424c71729ddc8c55930.svg")}
                alt=""
              />
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
        <p role="alert" className="form-error deck-note">
          {error}
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
  const [company, setCompany] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inviteKey = useRef(crypto.randomUUID());
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
    if (!invite) return;
    setBusy(true);
    setError(null);
    try {
      const process = await api.inviteCandidate(draftId, {
        candidate_id: invite.candidate_id,
        company_name: company.trim(),
        description: description.trim(),
        idempotency_key: inviteKey.current,
      });
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
          disabled={draft.data?.archived}
          onClick={() => draft.data && onEdit(draft.data)}
        >
          Редактировать<span className="edit-badge">✎</span>
        </Action>
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
              ].map(([key, value]) => (
                <div className="skill-row" key={key}>
                  <header>
                    <span>{key}</span>
                    <span>Требуемый уровень {value}/7</span>
                  </header>
                  <div className="skill-track">
                    <span style={{ width: `${(value / 7) * 100}%` }} />
                  </div>
                </div>
              ))}
              {!Object.keys(fields.stacks || {}).length && (
                <p className="muted">
                  Специализация: {fields.class || "Не указана"}
                </p>
              )}
            </div>
          </section>
          <section className="panel">
            <div className="small-heading">
              <h2>Понравились в ленте</h2>
              <span className="count">{saved.data?.items.length || 0}</span>
              <button
                className="shortlist-all"
                onClick={() => navigate(`/vacancy/${draftId}/resumes`)}
              >
                Все →
              </button>
            </div>
            {saved.loading || saved.error ? (
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
                      onClick={() =>
                        navigate(
                          `/resume/${item.candidate_id}?draft=${draftId}`,
                        )
                      }
                    >
                      <span className="saved-initial">
                        {(item.card?.display_name || "К").slice(0, 1)}
                      </span>
                      <h3>{item.card?.display_name || "Кандидат"}</h3>
                      <small>
                        {item.card?.category.specialization ||
                          "Профиль обновлён"}
                      </small>
                    </button>
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
                  </div>
                ))}
              </div>
            )}
          </section>
          <section className="panel">
            <div className="small-heading">
              <h2>Назначенные собеседования</h2>
              <span className="count">{interviews.length}</span>
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
                      {saved.data?.items.find(
                        (item) => item.candidate_id === process.candidate_id,
                      )?.card?.display_name || "Кандидат"}
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
              <p>{invite.card?.display_name || "Кандидат"}</p>
              <p className="salary-row">
                {salary(fields.zp_min, fields.zp_max)}
              </p>
              <form className="form-grid" onSubmit={sendInvite}>
                <fieldset className="invite-fields" disabled={busy}>
                  <Field label="Компания">
                    <input
                      value={company}
                      required
                      maxLength={300}
                      onChange={(event) => {
                        setCompany(event.target.value);
                        inviteKey.current = crypto.randomUUID();
                      }}
                    />
                  </Field>
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
                <Action type="submit" variant="light" disabled={busy}>
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

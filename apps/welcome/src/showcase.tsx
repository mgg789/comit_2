import { useEffect, useRef, useState } from "react";
import { createApiClient, ApiError } from "@comit/api-client";
import { GRADES, SPECIALIZATIONS, type PublicShowcaseCandidate, type PublicShowcaseVacancy, type PublicShowcasePage, type PublicShowcaseQuery } from "@comit/shared";
import { PublicShellHeader, Dialog } from "@comit/ui-kit/app-shell";
import { Action, Backdrop, Icon, Select, State, Tags, VacancyCardArrow } from "@comit/ui-kit/design";
import { usePersistentState } from "@comit/ui-kit/ui-state";
import { WelcomeBackground, candidateUrl, employerUrl } from "./entry";

const api = createApiClient({ baseUrl: "/api" });
const roleLabels: Record<string, string> = { backend: "Backend-разработчик", frontend: "Frontend-разработчик", fullstack: "Fullstack-разработчик", mobile: "Мобильный разработчик", devops: "DevOps", data: "Аналитик данных", qa: "Тестировщик", ml: "ML-специалист" };
const gradeLabels: Record<string, string> = { intern: "Стажёр", junior: "Junior", middle: "Middle", senior: "Senior" };
const stackLabels: Record<string, string> = { python: "Python", node: "Node.js", go: "Go", php: "PHP", react: "React", vue: "Vue", angular: "Angular", html_css_jquery: "HTML / CSS / jQuery", tailwind: "Tailwind CSS", electron: "Electron", figma: "Figma", postgres: "PostgreSQL", mongo: "MongoDB", sqlite: "SQLite", mysql: "MySQL", docker: "Docker" };
const tabs = [{ id: "vacancies", label: "Вакансии", path: "#/showcase/vacancies", icon: "vacancies" }, { id: "candidates", label: "Соискатели", path: "#/showcase/candidates", icon: "candidates" }] as const;
type Kind = "vacancies" | "candidates";
type Card = PublicShowcaseVacancy | PublicShowcaseCandidate;
const currency = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
function salary(value: Card["salary"]) { return `${currency.format(value.from_rub / 1000)}–${currency.format(value.to_rub / 1000)}к ₽`; }
function failure(reason: unknown) { return reason instanceof ApiError && reason.status === 409 ? "Список обновился. Загрузите его заново." : "Не удалось загрузить витрину. Попробуйте ещё раз."; }
function cardKey(card: Card) { return "vacancy_id" in card ? card.vacancy_id : card.candidate_id; }
function PublicationDate({ value }: { value: string }) {
  const date = new Date(value);
  return <span className="date"><Icon name="clock" />{Number.isNaN(date.getTime()) ? "Дата не указана" : <time dateTime={date.toISOString()}>{new Intl.DateTimeFormat("ru-RU").format(date)}</time>}</span>;
}

/** Anonymous, cursor-paged overview; details and recruitment remain inside role-authenticated apps. */
export function PublicShowcase({ kind }: { kind: Kind }) {
  const [query, setQuery] = usePersistentState(`${kind}.query`, "", (value): value is string => typeof value === "string" && value.length <= 500);
  const [specialization, setSpecialization] = usePersistentState(`${kind}.specialization`, "");
  const [grade, setGrade] = usePersistentState(`${kind}.grade`, "");
  const [stack, setStack] = usePersistentState(`${kind}.stack`, "");
  const [sort, setSort] = usePersistentState<"newest" | "oldest">(`${kind}.sort`, "newest", (value): value is "newest" | "oldest" => value === "newest" || value === "oldest");
  const [data, setData] = useState<PublicShowcasePage<Card> | null>(null);
  const [loading, setLoading] = useState(true), [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null), [moreError, setMoreError] = useState<string | null>(null);
  const [login, setLogin] = useState<Kind | null>(null), [retry, setRetry] = useState(0);
  const generation = useRef(0), morePending = useRef(false);
  const read = (request: PublicShowcaseQuery): Promise<PublicShowcasePage<Card>> => kind === "vacancies" ? api.listPublicVacancies(request) : api.listPublicCandidates(request);
  const filters: PublicShowcaseQuery = { limit: 24, q: query.trim(), sort, specialization: SPECIALIZATIONS.find(value => value === specialization), grade: GRADES.find(value => value === grade), stack: Object.hasOwn(stackLabels, stack) ? stack : undefined };
  useEffect(() => {
    const current = ++generation.current;
    setLoading(true); setData(null); setError(null); setMoreError(null); setMore(false); morePending.current = false; setLogin(null);
    const timer = window.setTimeout(() => { void read(filters).then(value => { if (generation.current === current) setData(value); }).catch(reason => { if (generation.current === current) setError(failure(reason)); }).finally(() => { if (generation.current === current) setLoading(false); }); }, 180);
    return () => { window.clearTimeout(timer); generation.current++; };
  }, [kind, query, specialization, grade, stack, sort, retry]);
  async function loadMore() {
    if (!data?.has_more || !data.next_cursor || morePending.current) return;
    const current = generation.current; morePending.current = true; setMore(true); setMoreError(null);
    try { const next = await read({ ...filters, cursor: data.next_cursor }); if (current !== generation.current) return; setData(previous => previous ? { ...next, items: [...previous.items, ...next.items.filter(card => !previous.items.some(existing => cardKey(existing) === cardKey(card)))] } : next); }
    catch (reason) { if (current === generation.current) setMoreError(failure(reason)); }
    finally { if (current === generation.current) { morePending.current = false; setMore(false); } }
  }
  return <div className="employer-app comit-design public-showcase">
    <Backdrop imagery={false} /><WelcomeBackground />
    <PublicShellHeader brandHref="#/" loginHref="#/" navigationLabel="Разделы витрины" items={tabs} selected={kind} navigate={path => { window.location.hash = path; }} />
    <main className="main-content public-content">
      <div className="public-filters">
        <Select aria-label="Специализация" value={specialization} onChange={event => setSpecialization(event.target.value)}><option value="">Специализация</option>{SPECIALIZATIONS.map(value => <option key={value} value={value}>{roleLabels[value]}</option>)}</Select>
        <Select aria-label="Грейд" value={grade} onChange={event => setGrade(event.target.value)}><option value="">Грейд</option>{GRADES.map(value => <option key={value} value={value}>{gradeLabels[value]}</option>)}</Select>
        <Select aria-label="Стек" value={stack} onChange={event => setStack(event.target.value)}><option value="">Стек</option>{Object.entries(stackLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</Select>
        <input aria-label="Поиск по витрине" className="public-query" placeholder={kind === "vacancies" ? "Вакансия" : "Направление"} maxLength={500} value={query} onChange={event => setQuery(event.target.value)} />
        <Select className="public-sort" aria-label="Сортировка" value={sort} onChange={event => setSort(event.target.value === "oldest" ? "oldest" : "newest")}><option value="newest">Сначала новые</option><option value="oldest">Сначала старые</option></Select>
      </div>
      {loading || error ? <State loading={loading} error={error} retry={() => setRetry(value => value + 1)} /> : !data?.items.length ? <section className="panel public-empty" role="status"><h1>{query || specialization || grade || stack ? "Ничего не найдено" : kind === "vacancies" ? "Пока нет опубликованных вакансий" : "Пока нет опубликованных резюме"}</h1><p>Попробуйте изменить фильтры или зайдите позже.</p></section> : <div className={kind === "vacancies" ? "vacancy-grid public-vacancies" : "public-candidates"} aria-label={kind === "vacancies" ? "Публичные вакансии" : "Публичные соискатели"}>
        {data.items.map(card => "vacancy_id" in card ? <article className="vacancy-card glass" key={card.vacancy_id}>
          <span className="status-pill"><span className="status-dot" />Активная</span><h3>{card.title}</h3><Tags shape="pill" values={card.stacks.map(value => stackLabels[value] || value)} /><div className="salary-row"><strong>{salary(card.salary)}</strong><small>за месяц</small></div>
          <div className="vacancy-footer"><PublicationDate value={card.published_at} /><VacancyCardArrow /></div><button className="public-card-link" aria-label={`Открыть вакансию ${card.title}`} onClick={() => setLogin("vacancies")} />
        </article> : <article className="panel public-candidate-row" key={card.candidate_id}>
          <div className="public-avatar" aria-hidden="true"><Icon name="candidates" /></div><div className="public-candidate-name"><h2>{roleLabels[card.category.specialization] || "Специалист"}</h2><p>{gradeLabels[card.category.grade] || card.category.grade}</p></div>
          <Tags variant="muted" shape="pill" values={[...card.stacks.map(value => stackLabels[value] || value), salary(card.salary), card.city].filter(Boolean)} /><VacancyCardArrow /><button className="public-card-link" aria-label={`Открыть профиль ${roleLabels[card.category.specialization] || "специалиста"}`} onClick={() => setLogin("candidates")} />
        </article>)}
      </div>}
      {data?.has_more && !loading && <div className="public-pagination">{moreError && <p role="alert">{moreError}</p>}<Action disabled={more} onClick={() => void loadMore()}>{more ? "Загружаем…" : "Показать ещё"}</Action>{moreError && <Action onClick={() => setRetry(value => value + 1)}>Обновить список</Action>}</div>}
    </main>
    {login && <Dialog title="Для этого действия требуется войти" close={() => setLogin(null)}><div className="public-login-options">{(login === "vacancies" ? ["candidate", "employer"] : ["employer", "candidate"]).map(role => <a key={role} className="action primary" href={role === "candidate" ? candidateUrl : employerUrl}>{role === "candidate" ? "Войти как кандидат" : "Войти как работодатель"}</a>)}</div><Action onClick={() => setLogin(null)}>Отмена</Action></Dialog>}
  </div>;
}

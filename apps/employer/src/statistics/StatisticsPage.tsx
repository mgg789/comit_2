import type { EmployerStatistics, EmployerStatisticStage } from "@comit/shared";
import { Button, SelectField, StateMessage, cx, Icon, REVEAL, Reveal, motion } from "@comit/ui-kit";
import { useState } from "react";
import { Link } from "react-router";
import { api, describeError } from "../api";
import { PageHeader } from "../layout/PageHeader";
import { vacancyTitle } from "../lib/format";
import { useResource } from "../lib/use-resource";
import { loadAllVacancies } from "../vacancies/VacanciesPage";

/** Funnel milestones in lifecycle order with the employer-facing names. */
export const STAGES: ReadonlyArray<{ key: EmployerStatisticStage; label: string }> = [
  { key: "delivered", label: "Показаны в подборе" },
  { key: "viewed", label: "Просмотрены" },
  { key: "liked", label: "Лайки" },
  { key: "disliked", label: "Пропуски" },
  { key: "invited", label: "Приглашены" },
  { key: "accepted", label: "Приняли приглашение" },
  { key: "interviewed", label: "Собеседования" },
  { key: "offered", label: "Офферы" },
  { key: "hired", label: "Наняты" },
];

const PERIODS = [
  { value: "7", label: "7 дней" },
  { value: "30", label: "30 дней" },
  { value: "90", label: "90 дней" },
] as const;

/** Inclusive UTC calendar range ending today; the API accepts at most 90 days. */
export function periodRange(days: number, today = new Date()): { from: string; to: string } {
  const to = today.toISOString().slice(0, 10);
  const start = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - (days - 1)));
  return { from: start.toISOString().slice(0, 10), to };
}

/** Relative change vs the previous equal period; null when there is no base to compare with. */
export function changePercent(current: number, previous: number): number | null {
  if (previous === 0) return null;
  return Math.round(((current - previous) / previous) * 100);
}

/** «Статистика» (Figma «СТАТИСТИКА»): aggregate funnel of the employer's own vacancies, UTC days. */
export function StatisticsPage() {
  const [days, setDays] = useState("30");
  const [vacancyId, setVacancyId] = useState("");
  const vacancies = useResource(loadAllVacancies, []);
  const stats = useResource(() => api.getEmployerStatistics({ ...periodRange(Number(days)), ...(vacancyId ? { vacancy_id: vacancyId } : {}) }), [days, vacancyId]);

  return (
    <>
      <PageHeader title="Статистика" backTo="/vacancies" backLabel="К вакансиям" />
      <div className="mb-6 flex flex-wrap gap-3 lg:mb-[2rem] lg:gap-[0.78rem]">
        <SelectField aria-label="Период" value={days} onChange={setDays} options={PERIODS} containerClassName="w-44 lg:w-[13rem]" />
        <SelectField
          aria-label="Вакансия"
          placeholder="Все вакансии"
          value={vacancyId}
          onChange={setVacancyId}
          options={(vacancies.data?.items ?? []).filter((vacancy) => vacancy.publication).map((vacancy) => ({ value: vacancy.vacancy_id, label: vacancyTitle(vacancy) }))}
          containerClassName="w-72 lg:w-[26.875rem]"
        />
      </div>
      {!stats.data && stats.status === "loading" && <StateMessage kind="loading" title="Считаем статистику" />}
      {stats.status === "error" && (
        <StateMessage
          kind="error"
          title="Статистика недоступна"
          description={describeError(stats.error)}
          action={
            <Button variant="secondary" onClick={stats.reload}>
              Повторить
            </Button>
          }
        />
      )}
      {stats.data && <Dashboard stats={stats.data} period={periodRange(Number(days))} />}
    </>
  );
}

/** Furthest milestone a vacancy reached in the period, shown with the design's stage colours. */
export function vacancyStage(counts: EmployerStatistics["current"]): { label: string; className: string } {
  if (counts.hired > 0) return { label: "Найм", className: "text-success-strong" };
  if (counts.offered > 0) return { label: "Оффер", className: "text-success-strong" };
  if (counts.interviewed > 0 || counts.accepted > 0) return { label: "Собеседование", className: "text-warning" };
  if (counts.liked > 0 || counts.invited > 0) return { label: "Лайки", className: "text-stage-likes" };
  return { label: "Старт", className: "bg-accent-gradient bg-clip-text text-transparent" };
}

const PANEL = "rounded-[1rem] bg-surface-dialog";

function Dashboard({ stats, period }: { stats: EmployerStatistics; period: { from: string; to: string } }) {
  const likedChange = changePercent(stats.current.liked, stats.previous.liked);
  return (
    <div className="grid items-start gap-6 lg:grid-cols-[40.9375rem_minmax(0,1fr)] lg:gap-[2rem]">
      <div className="flex flex-col gap-6 lg:gap-[2rem]">
        <Reveal className={cx(PANEL, "grid grid-cols-3 gap-4 p-5 lg:gap-[2rem] lg:p-[2rem]")}>
          <Kpi value={stats.current.delivered} label="показов в подборе" />
          <Kpi value={stats.current.liked} label="лайков" change={likedChange} />
          <Kpi value={stats.current.interviewed} label="собесов назначено" />
        </Reveal>
        <Reveal delay={0.06} className={cx(PANEL, "flex flex-col gap-4 px-5 py-5 lg:gap-[1.5rem] lg:px-[1.875rem] lg:py-[1.5625rem]")}>
          <div>
            <h2 className="text-xl font-semibold tracking-[-0.03rem] lg:text-[1.75rem]">Воронка найма</h2>
            <p className="mt-1 text-sm font-medium text-white/60 lg:text-[1.125rem]">Уникальные пары вакансия–кандидат на каждом этапе за период</p>
          </div>
          <Funnel stats={stats} />
        </Reveal>
        <Reveal delay={0.12} className={cx(PANEL, "grid grid-cols-2 gap-4 p-5 lg:gap-[1.5rem] lg:p-[2rem]")}>
          <Kpi value={stats.inventory.open} label="открытых вакансий" />
          <Kpi value={stats.inventory.active_processes} label="активных процессов" />
          <Kpi value={stats.inventory.saved_pairs} label="в шортлистах" />
          <Kpi value={stats.unique_candidates} label="уникальных кандидатов" />
        </Reveal>
      </div>
      <div className="flex flex-col gap-6 lg:gap-[2rem]">
        <Reveal delay={0.04} className={cx(PANEL, "flex flex-col gap-5 px-5 py-5 lg:gap-[1.5625rem] lg:px-[1.875rem] lg:py-[1.25rem]")}>
          <Link to="/vacancies" className="flex w-fit items-center gap-2 text-xl font-semibold tracking-[-0.03rem] hover:opacity-80 lg:text-[1.75rem]">
            Последние вакансии
            <Icon name="angle-down" size={14} className="-rotate-90" />
          </Link>
          {stats.top_vacancies.length === 0 ? (
            <p className="text-base font-medium text-white/60 lg:text-[1.25rem]">За период событий по вакансиям не было.</p>
          ) : (
            <ul className="flex flex-col gap-5 lg:gap-[1.5625rem]">
              {stats.top_vacancies.map((vacancy) => (
                <RecentVacancy key={vacancy.vacancy_id} vacancy={vacancy} period={period} />
              ))}
            </ul>
          )}
        </Reveal>
        <Reveal delay={0.1} className={cx(PANEL, "flex flex-col gap-4 px-5 py-5 lg:gap-[1.5rem] lg:px-[1.875rem] lg:py-[1.5625rem]")}>
          <h2 className="text-xl font-semibold tracking-[-0.03rem] lg:text-[1.75rem]">Показы по дням</h2>
          <DailyChart stats={stats} />
        </Reveal>
      </div>
    </div>
  );
}

/** Row of «Последние вакансии»: title · status and impressions · furthest stage in its colour. */
function RecentVacancy({ vacancy, period }: { vacancy: EmployerStatistics["top_vacancies"][number]; period: { from: string; to: string } }) {
  const detail = useResource(() => api.getEmployerStatistics({ ...period, vacancy_id: vacancy.vacancy_id }), [vacancy.vacancy_id, period.from, period.to]);
  const stage = detail.data ? vacancyStage(detail.data.current) : null;
  return (
    <li>
      <Link to={`/vacancies/${vacancy.draft_id}`} className="grid grid-cols-[1fr_9.625rem_9rem] items-center gap-4 hover:opacity-85">
        <span className="truncate text-base font-medium tracking-[-0.021rem] lg:text-[1.25rem]">{vacancy.title}</span>
        <span className="flex flex-col gap-[0.125rem]">
          <span className="text-xs font-medium tracking-[-0.015rem] text-stat-muted lg:text-[0.875rem]">{vacancy.status === "open" ? "Активна" : vacancy.status === "closed" ? "Закрыта" : "Черновик"}</span>
          <span className="text-sm font-medium tracking-[-0.056rem] lg:text-[1.125rem]">{detail.data ? detail.data.current.delivered : "…"} показов</span>
        </span>
        <span className={cx("text-base font-medium tracking-[-0.021rem] lg:text-[1.25rem]", stage?.className)}>{stage?.label ?? ""}</span>
      </Link>
    </li>
  );
}

function Kpi({ value, label, change }: { value: number; label: string; change?: number | null }) {
  return (
    <p className="flex flex-col">
      <span className="text-3xl leading-none font-semibold tracking-[-0.14rem] tabular-nums lg:text-[2.875rem]">{value}</span>
      <span className="mt-2 text-base leading-tight font-medium tracking-[-0.021rem] lg:mt-[0.875rem] lg:text-[1.25rem]">{label}</span>
      {change !== undefined && change !== null && (
        <span className={cx("text-base font-medium lg:text-[1.25rem]", change >= 0 ? "text-success-strong" : "text-danger-soft")}>
          {change >= 0 ? "+" : "−"}
          {Math.abs(change)}%
        </span>
      )}
    </p>
  );
}

/** Horizontal single-series bars, one per milestone; value printed as text, not series color. */
function Funnel({ stats }: { stats: EmployerStatistics }) {
  const max = Math.max(1, ...STAGES.map((stage) => stats.current[stage.key]));
  return (
    <ul className="flex flex-col gap-2.5">
      {STAGES.map((stage) => {
        const value = stats.current[stage.key];
        return (
          <li key={stage.key} className="grid grid-cols-[9.375rem_1fr_auto] items-center gap-3 text-sm font-medium lg:text-[1.125rem]">
            <span className="text-subtle">{stage.label}</span>
            <span className="h-3 overflow-hidden rounded-r-[0.25rem] bg-white/5" aria-hidden>
              <motion.span
                className="block h-full rounded-r-[0.25rem] bg-primary"
                initial={{ width: 0 }}
                animate={{ width: `${(value / max) * 100}%` }}
                transition={{ ...REVEAL, delay: 0.15 }}
              />
            </span>
            <span className="w-10 text-right tabular-nums">{value}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** Daily delivered counts as columns with a hover tooltip and a screen-reader table. */
function DailyChart({ stats }: { stats: EmployerStatistics }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...stats.daily.map((day) => day.counts.delivered));
  const active = hover === null ? null : stats.daily[hover];
  const last = stats.daily.at(-1);
  const dayLabel = (iso: string) => new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(`${iso}T00:00:00Z`));
  return (
    <figure className="flex flex-col gap-2">
      <p className="h-5 text-sm text-subtle" aria-live="polite">
        {active ? `${dayLabel(active.date)}: ${active.counts.delivered} показов, ${active.counts.liked} лайков` : "Наведите на столбец, чтобы увидеть день"}
      </p>
      <div className="flex h-40 items-end gap-[0.125rem] border-b border-white/15" onMouseLeave={() => setHover(null)}>
        {stats.daily.map((day, index) => (
          <button
            key={day.date}
            type="button"
            aria-label={`${dayLabel(day.date)}: ${day.counts.delivered} показов`}
            onMouseEnter={() => setHover(index)}
            onFocus={() => setHover(index)}
            className="flex h-full min-w-0 flex-1 cursor-default items-end justify-center focus-visible:outline-none"
          >
            <span
              className={cx("block w-full max-w-6 rounded-t-[0.25rem] transition-opacity duration-150", hover !== null && hover !== index ? "bg-primary/40" : "bg-primary")}
              style={{ height: `${(day.counts.delivered / max) * 100}%`, minHeight: day.counts.delivered > 0 ? 2 : 0 }}
            />
          </button>
        ))}
      </div>
      <figcaption className="flex justify-between text-xs text-muted">
        <span>{stats.daily[0] ? dayLabel(stats.daily[0].date) : ""}</span>
        <span>UTC</span>
        <span>{last ? dayLabel(last.date) : ""}</span>
      </figcaption>
      <table className="sr-only">
        <caption>Показы и лайки по дням</caption>
        <thead>
          <tr>
            <th>Дата</th>
            <th>Показы</th>
            <th>Лайки</th>
          </tr>
        </thead>
        <tbody>
          {stats.daily.map((day) => (
            <tr key={day.date}>
              <td>{day.date}</td>
              <td>{day.counts.delivered}</td>
              <td>{day.counts.liked}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

import type { EmployerStatistics, EmployerStatisticStage } from "@comit/shared";
import { Button, Panel, SelectField, StateMessage, cx } from "@comit/ui-kit";
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
      <PageHeader title="Статистика" />
      <div className="mb-6 flex flex-wrap gap-3">
        <SelectField aria-label="Период" value={days} onChange={setDays} options={PERIODS} containerClassName="w-40" />
        <SelectField
          aria-label="Вакансия"
          placeholder="Все вакансии"
          value={vacancyId}
          onChange={setVacancyId}
          options={(vacancies.data?.items ?? []).filter((vacancy) => vacancy.publication).map((vacancy) => ({ value: vacancy.vacancy_id, label: vacancyTitle(vacancy) }))}
          containerClassName="w-72"
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
      {stats.data && <Dashboard stats={stats.data} />}
    </>
  );
}

function Dashboard({ stats }: { stats: EmployerStatistics }) {
  const likedChange = changePercent(stats.current.liked, stats.previous.liked);
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,480px)_minmax(0,1fr)]">
      <div className="flex flex-col gap-6">
        <Panel tone="solid" className="grid grid-cols-3 gap-4 p-5 sm:p-6">
          <Kpi value={stats.current.delivered} label="показов в подборе" />
          <Kpi value={stats.current.liked} label="лайков" change={likedChange} />
          <Kpi value={stats.current.interviewed} label="собеседований" />
        </Panel>
        <Panel tone="solid" className="flex flex-col gap-4 p-5 sm:p-6">
          <div>
            <h2 className="text-2xl">Воронка найма</h2>
            <p className="text-sm text-muted">Уникальные пары вакансия–кандидат на каждом этапе за период</p>
          </div>
          <Funnel stats={stats} />
        </Panel>
        <Panel tone="solid" className="grid grid-cols-2 gap-4 p-5 text-sm sm:p-6">
          <p>
            <span className="block text-3xl">{stats.inventory.open}</span>открытых вакансий
          </p>
          <p>
            <span className="block text-3xl">{stats.inventory.active_processes}</span>активных процессов
          </p>
          <p>
            <span className="block text-3xl">{stats.inventory.saved_pairs}</span>в шортлистах
          </p>
          <p>
            <span className="block text-3xl">{stats.unique_candidates}</span>уникальных кандидатов
          </p>
        </Panel>
      </div>
      <div className="flex flex-col gap-6">
        <Panel tone="solid" className="flex flex-col gap-4 p-5 sm:p-6">
          <h2 className="text-2xl">Показы по дням</h2>
          <DailyChart stats={stats} />
        </Panel>
        <Panel tone="solid" className="flex flex-col gap-3 p-5 sm:p-6">
          <h2 className="text-2xl">Активные вакансии</h2>
          {stats.top_vacancies.length === 0 ? (
            <p className="text-sm text-muted">За период событий по вакансиям не было.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-white/10">
              {stats.top_vacancies.map((vacancy) => (
                <li key={vacancy.vacancy_id}>
                  <Link to={`/vacancies/${vacancy.draft_id}`} className="grid grid-cols-[1fr_auto] items-center gap-4 py-3 hover:text-white">
                    <span className="truncate text-lg">{vacancy.title}</span>
                    <span className="text-right text-sm text-muted">
                      {vacancy.events} событий · {vacancy.candidates} кандидатов
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}

function Kpi({ value, label, change }: { value: number; label: string; change?: number | null }) {
  return (
    <p className="flex flex-col">
      <span className="text-4xl leading-none font-medium tabular-nums sm:text-5xl">{value}</span>
      <span className="mt-2 text-base leading-tight">{label}</span>
      {change !== undefined && change !== null && (
        <span className={cx("text-sm", change >= 0 ? "text-success" : "text-danger-soft")}>
          {change >= 0 ? "+" : "−"}
          {Math.abs(change)}% к прошлому периоду
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
          <li key={stage.key} className="grid grid-cols-[150px_1fr_auto] items-center gap-3 text-sm">
            <span className="text-subtle">{stage.label}</span>
            <span className="h-3 overflow-hidden rounded-r-[0.25rem] bg-white/5" aria-hidden>
              <span className="block h-full rounded-r-[0.25rem] bg-primary" style={{ width: `${(value / max) * 100}%` }} />
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

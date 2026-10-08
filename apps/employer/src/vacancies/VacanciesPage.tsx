import type { AgentsDraftView } from "@comit/shared";
import { Button, Chip, Icon, Panel, StateMessage, StatusDot, addArrowUrl, cx } from "@comit/ui-kit";
import { useState } from "react";
import { Link, Outlet, useNavigate, useOutletContext } from "react-router";
import { api, describeError } from "../api";
import { PageHeader } from "../layout/PageHeader";
import { WORK_TYPE_LABEL, formatSalaryRange, topRequirements, vacancyBadge, vacancyTitle } from "../lib/format";
import { useResource } from "../lib/use-resource";
import { VacancyFormDialog } from "./VacancyFormDialog";

/** Upper bound of keyset pages fetched for one list view (50 per page); an employer rarely exceeds it. */
const MAX_PAGES = 10;

type Filter = "all" | "open" | "closed";

const FILTERS: ReadonlyArray<{ key: Filter; label: string; match: (vacancy: AgentsDraftView) => boolean }> = [
  { key: "all", label: "Все", match: () => true },
  { key: "open", label: "Открытые", match: (vacancy) => vacancy.business_status === "open" },
  { key: "closed", label: "Закрытые", match: (vacancy) => vacancy.business_status === "closed" },
];

/** Read every non-archived vacancy of the signed-in employer, following keyset cursors. */
export async function loadAllVacancies(): Promise<{ items: AgentsDraftView[]; truncated: boolean }> {
  const items: AgentsDraftView[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < MAX_PAGES; page++) {
    const result = await api.listVacancies(50, cursor);
    items.push(...result.items);
    if (!result.has_more || !result.next_cursor) return { items, truncated: false };
    cursor = result.next_cursor;
  }
  return { items, truncated: true };
}

/** «Все вакансии»: status tabs and vacancy tiles; nested routes render the create dialog on top. */
export function VacanciesPage() {
  const vacancies = useResource(loadAllVacancies, []);
  const [filter, setFilter] = useState<Filter>("all");
  const items = vacancies.data?.items ?? [];
  const active = FILTERS.find((entry) => entry.key === filter) ?? FILTERS[0]!;
  const visible = items.filter(active.match);

  return (
    <>
      <PageHeader
        title="Все вакансии"
        actions={
          <Link
            to="new"
            className="bg-accent-gradient inline-flex h-[51px] items-center gap-2.5 rounded-pill pr-1 pl-5 text-lg font-semibold text-text transition-[filter] duration-150 hover:brightness-110"
          >
            Добавить вакансию
            <img src={addArrowUrl} alt="" width={43} height={43} />
          </Link>
        }
      />

      <div role="tablist" aria-label="Фильтр вакансий" className="mb-6 grid grid-cols-3 gap-2 sm:gap-5">
        {FILTERS.map((entry) => {
          const count = items.filter(entry.match).length;
          const selected = entry.key === filter;
          return (
            <button
              key={entry.key}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => setFilter(entry.key)}
              className={cx(
                "glass cursor-pointer rounded-field border px-3 py-3 text-left text-base font-medium transition-colors duration-150 sm:px-8 sm:py-5 sm:text-[22px]",
                "shadow-tab-glow",
                selected ? "border-white/40 text-text" : "border-glass-border text-text/80 hover:text-text",
              )}
            >
              {entry.label}
              {vacancies.status === "ready" && `(${count})`}
            </button>
          );
        })}
      </div>

      {vacancies.status === "loading" && items.length === 0 && <StateMessage kind="loading" title="Загружаем вакансии" />}
      {vacancies.status === "error" && (
        <StateMessage
          kind="error"
          title="Не удалось загрузить вакансии"
          description={describeError(vacancies.error)}
          action={
            <Button variant="secondary" onClick={vacancies.reload}>
              Повторить
            </Button>
          }
        />
      )}
      {vacancies.status === "ready" && visible.length === 0 && (
        <StateMessage
          kind="empty"
          title={items.length === 0 ? "Вакансий пока нет" : "В этой вкладке пусто"}
          description={
            items.length === 0
              ? "Создайте первую вакансию — после публикации комбайн подберёт кандидатов с объяснением попадания."
              : undefined
          }
        />
      )}

      {visible.length > 0 && (
        <ul className="grid gap-4 sm:grid-cols-2 sm:gap-5 xl:grid-cols-3">
          {visible.map((vacancy) => (
            <li key={vacancy.draft_id}>
              <VacancyCard vacancy={vacancy} />
            </li>
          ))}
        </ul>
      )}
      {vacancies.data?.truncated && <p className="mt-6 text-center text-sm text-muted">Показаны первые 500 вакансий.</p>}

      <Outlet context={{ reload: vacancies.reload }} />
    </>
  );
}

/** Vacancy tile: status, title, top stack requirements, mandatory salary range, location. */
function VacancyCard({ vacancy }: { vacancy: AgentsDraftView }) {
  const badge = vacancyBadge(vacancy);
  const { fields } = vacancy;
  const stacks = topRequirements(fields.stacks, 3);
  const location = [fields.city, fields.type ? WORK_TYPE_LABEL[fields.type] : null].filter(Boolean).join(" · ");
  return (
    <Panel tone="card" className="group relative flex h-full flex-col gap-4 p-4 transition-colors duration-150 hover:border-white/30">
      <span className="glass inline-flex w-fit items-center gap-3 rounded-chip px-3 py-3 text-base font-medium sm:text-lg">
        <StatusDot tone={badge.tone} />
        {badge.label}
      </span>
      <h2 className="text-2xl leading-tight font-semibold sm:text-[32px]">
        <Link to={vacancy.draft_id} className="after:absolute after:inset-0 after:rounded-card focus-visible:outline-none">
          {vacancyTitle(vacancy)}
        </Link>
      </h2>
      {stacks.length > 0 && (
        <div className="flex flex-wrap gap-2.5">
          {stacks.map((stack, index) => (
            <Chip key={stack} tone={index === 0 && badge.tone === "success" ? "info" : "light"}>
              {stack}
            </Chip>
          ))}
        </div>
      )}
      <p className="flex flex-wrap items-end gap-x-2.5 gap-y-1">
        <span className="font-display text-2xl tracking-tight sm:text-[32px]">{formatSalaryRange(fields.zp_min, fields.zp_max)}</span>
        <span className="text-lg font-semibold text-muted">в месяц</span>
      </p>
      <div className="mt-auto flex items-center justify-between gap-3 border-t border-white/15 pt-4">
        <span className="truncate text-lg font-medium text-subtle sm:text-2xl">{location || "Локация не указана"}</span>
        <span
          aria-hidden
          className="inline-flex size-[46px] shrink-0 items-center justify-center rounded-field border-2 border-white/40 text-text transition-colors duration-150 group-hover:border-white/80"
        >
          <Icon name="angle-down" size={14} className="-rotate-90" />
        </span>
      </div>
    </Panel>
  );
}

/** `/vacancies/new`: the create dialog over the list; publishing navigates to the new vacancy page. */
export function NewVacancyRoute() {
  const navigate = useNavigate();
  const { reload } = useOutletContext<{ reload: () => void }>();
  return (
    <VacancyFormDialog
      onChanged={reload}
      onPublished={(draft) => navigate(`/vacancies/${draft.draft_id}`)}
      onClose={() => navigate("/vacancies")}
    />
  );
}

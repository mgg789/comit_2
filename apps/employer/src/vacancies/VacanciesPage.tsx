import type { AgentsDraftView } from "@comit/shared";
import { Button, Chip, Icon, StateMessage, StatusDot, cx, SPRING, StaggerItem, StaggerList, motion } from "@comit/ui-kit";
import { useState } from "react";
import { Link, Outlet, useNavigate, useOutletContext } from "react-router";
import { api, describeError } from "../api";
import { HeaderAction, PageHeader } from "../layout/PageHeader";
import { SPECIALIZATION_LABEL, WORK_TYPE_LABEL, formatSalaryRange, topRequirements, vacancyBadge, vacancyTitle } from "../lib/format";
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
        backHref="/"
        backLabel="На главную COMIT"
        actions={
          <HeaderAction to="new" icon="arrow-up-right">
            Добавить вакансию
          </HeaderAction>
        }
      />

      <div role="tablist" aria-label="Фильтр вакансий" className="mb-6 grid grid-cols-3 gap-2 lg:mb-[2.125rem] lg:gap-[1.25rem]">
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
                "glass-tab relative h-12 cursor-pointer overflow-hidden rounded-field px-3 text-left text-base font-medium transition-colors duration-150 lg:h-[3.875rem] lg:px-[2rem] lg:text-[1.375rem]",
                selected ? "text-text" : "text-white/60 hover:text-white/90",
              )}
            >
              {selected && (
                <motion.span
                  layoutId="vacancy-tab-indicator"
                  transition={SPRING}
                  aria-hidden
                  className="absolute inset-0 rounded-field ring-1 ring-white/35 ring-inset"
                />
              )}
              <span className="relative">
                {entry.label}
                {vacancies.status === "ready" && `(${count})`}
              </span>
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
        <StaggerList key={filter} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 lg:gap-x-[1.25rem] lg:gap-y-[2.5rem]">
          {visible.map((vacancy) => (
            <StaggerItem key={vacancy.draft_id}>
              <VacancyCard vacancy={vacancy} />
            </StaggerItem>
          ))}
        </StaggerList>
      )}
      {vacancies.data?.truncated && <p className="mt-6 text-center text-sm text-muted">Показаны первые 500 вакансий.</p>}

      <Outlet context={{ reload: vacancies.reload }} />
    </>
  );
}

/**
 * Vacancy tile (design 573×355): glass badge at 16/16, 32px title, chips row, Murs Gothic salary,
 * gradient divider, footer with location and the outlined open button.
 */
function VacancyCard({ vacancy }: { vacancy: AgentsDraftView }) {
  const badge = vacancyBadge(vacancy);
  const { fields } = vacancy;
  const stacks = topRequirements(fields.stacks, 3);
  const location = [fields.city, fields.type ? WORK_TYPE_LABEL[fields.type] : null].filter(Boolean).join(" · ");
  return (
    <article className="glass-card group relative flex h-full flex-col overflow-hidden rounded-card p-4 transition-transform duration-300 ease-out hover:-translate-y-1 lg:h-[22.1875rem] lg:px-[1rem] lg:pt-[1rem] lg:pb-0">
      <span className="glass inline-flex h-10 w-fit items-center gap-[0.75rem] rounded-chip px-[0.75rem] text-base font-medium lg:h-[2.875rem] lg:text-[1.125rem]">
        <StatusDot tone={badge.tone} />
        {badge.label}
      </span>
      <h2 className="mt-5 truncate text-2xl leading-none font-semibold lg:mt-[2rem] lg:text-[2rem]">
        <Link to={vacancy.draft_id} className="after:absolute after:inset-0 after:rounded-card focus-visible:outline-none">
          {vacancyTitle(vacancy)}
        </Link>
      </h2>
      <div className="mt-3 flex h-10 flex-wrap gap-[0.625rem] overflow-hidden lg:mt-[0.9375rem] lg:h-[2.75rem]">
        {stacks.length > 0 ? (
          stacks.map((stack) => (
            <Chip key={stack} tone={badge.tone === "success" ? "info" : "light"}>
              {stack}
            </Chip>
          ))
        ) : (
          <Chip tone="light">{fields.class ? SPECIALIZATION_LABEL[fields.class] : "Направление не задано"}</Chip>
        )}
      </div>
      <p className="mt-6 flex flex-wrap items-end gap-x-[0.625rem] lg:mt-[2.1875rem]">
        <span className="font-display text-2xl leading-none tracking-[-0.034rem] lg:text-[2rem]">{formatSalaryRange(fields.zp_min, fields.zp_max)}</span>
        <span className="text-base leading-none font-semibold text-muted lg:text-[1.25rem]">за месяц</span>
      </p>
      <div aria-hidden className="mt-5 h-px bg-gradient-to-r from-white/50 via-white/25 to-white/5 lg:mt-[1.75rem]" />
      <div className="flex flex-1 items-center justify-between gap-3 py-3 lg:py-0">
        <span className="truncate text-lg font-medium text-subtle lg:text-[1.5rem]">{location || "Локация не указана"}</span>
        <span
          aria-hidden
          className="mr-[0.875rem] inline-flex size-[2.875rem] shrink-0 items-center justify-center rounded-[1rem] border-2 border-white/40 text-text transition-colors duration-150 group-hover:border-white/80"
        >
          <Icon name="angle-down" size={14} className="-rotate-90" />
        </span>
      </div>
    </article>
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

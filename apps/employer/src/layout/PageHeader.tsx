import { Icon } from "@comit/ui-kit";
import type { ReactNode } from "react";
import { Link } from "react-router";

interface PageHeaderProps {
  title: string;
  /** Route of the round back-arrow; omitted on top-level sections without a parent. */
  backTo?: string;
  backLabel?: string;
  /** Inline actions placed right after the title (e.g. «Добавить вакансию»). */
  actions?: ReactNode;
}

/** Page title row from the design: round back arrow + wide display heading + inline actions. */
export function PageHeader({ title, backTo, backLabel = "Назад", actions }: PageHeaderProps) {
  return (
    <div className="mb-6 flex flex-wrap items-center gap-4 sm:mb-8 sm:gap-5">
      {backTo && (
        <Link
          to={backTo}
          aria-label={backLabel}
          className="glass inline-flex size-12 shrink-0 items-center justify-center rounded-pill border border-glass-border text-text transition-colors duration-150 hover:bg-white/10 sm:size-[66px]"
        >
          <Icon name="arrow-left" size={26} />
        </Link>
      )}
      <h1 className="font-display text-2xl leading-none sm:text-[40px]">{title}</h1>
      {actions}
    </div>
  );
}

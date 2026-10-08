import { Icon, type IconName } from "@comit/ui-kit";
import type { ReactNode } from "react";
import { Link } from "react-router";

interface PageHeaderProps {
  title: string;
  /** In-app route of the round back-arrow. */
  backTo?: string;
  /** Full URL outside the cabinet (e.g. the shared start screen "/"), used when there is no in-app parent. */
  backHref?: string;
  backLabel?: string;
  /** Inline actions placed right after the title (e.g. «Добавить вакансию»). */
  actions?: ReactNode;
}

/** Page title row (design: 66px back button, 20px gap, 40px Murs Gothic title, 32px below). */
export function PageHeader({ title, backTo, backHref, backLabel = "Назад", actions }: PageHeaderProps) {
  const backClass =
    "glass inline-flex size-12 shrink-0 items-center justify-center rounded-pill text-text transition-colors duration-150 hover:bg-white/10 lg:size-[4.125rem]";
  return (
    <div className="mb-6 flex flex-wrap items-center gap-4 lg:mb-[2rem] lg:gap-[1.25rem]">
      {backTo ? (
        <Link to={backTo} aria-label={backLabel} className={backClass}>
          <Icon name="arrow-left" size={30} />
        </Link>
      ) : (
        backHref && (
          <a href={backHref} aria-label={backLabel} className={backClass}>
            <Icon name="arrow-left" size={30} />
          </a>
        )
      )}
      <h1 className="font-display text-2xl leading-none lg:text-[2.5rem]">{title}</h1>
      {actions}
    </div>
  );
}

/** Gradient pill action from the design («Добавить вакансию», «Редактировать»): 51px tall, white disc on the right. */
export function HeaderAction({ to, onClick, children, icon }: { to?: string; onClick?: () => void; children: ReactNode; icon: IconName }) {
  const className =
    "bg-accent-gradient inline-flex h-11 cursor-pointer items-center gap-[0.625rem] rounded-[1.6875rem] pr-[0.25rem] pl-[1.25rem] text-base font-semibold text-text transition-[filter] duration-150 hover:brightness-110 lg:h-[3.1875rem] lg:text-[1.25rem]";
  const content = (
    <>
      {children}
      <span className="inline-flex size-9 items-center justify-center rounded-full bg-light text-on-light lg:size-[2.6875rem]">
        <Icon name={icon} size={20} />
      </span>
    </>
  );
  return to ? (
    <Link to={to} className={className}>
      {content}
    </Link>
  ) : (
    <button type="button" onClick={onClick} className={className}>
      {content}
    </button>
  );
}

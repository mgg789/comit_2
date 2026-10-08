import { Avatar, Backdrop, Icon, Logo, cx, type IconName } from "@comit/ui-kit";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet } from "react-router";
import { AgentLauncher } from "../agent/AgentOverlay";
import { useAccount, useSession } from "../auth/session";
import { NotificationsMenu } from "./NotificationsMenu";

/** Top-level sections of the employer cabinet, in Figma nav order. */
export const NAV_ITEMS: ReadonlyArray<{ to: string; label: string; icon: IconName; iconSize: number }> = [
  { to: "/vacancies", label: "Вакансии", icon: "bars-sort", iconSize: 19 },
  { to: "/resumes", label: "Резюме", icon: "resumes", iconSize: 25 },
  { to: "/candidates", label: "Соискатели", icon: "people", iconSize: 24 },
  { to: "/statistics", label: "Статистика", icon: "chart-line", iconSize: 24 },
];

/** Authenticated layout (Figma header: 50px from the top, 80px side gutters on the 1920 artboard). */
export function AppShell() {
  return (
    <div className="relative min-h-dvh">
      <Backdrop />
      <header className="px-4 pt-4 lg:px-[5rem] lg:pt-[3.125rem]">
        <div className="mx-auto flex max-w-[110rem] items-center justify-between gap-3">
          <Link to="/vacancies" aria-label="COMIT — на главную" className="glass inline-flex h-12 items-center rounded-pill px-4 lg:h-[4.125rem] lg:px-[2rem]">
            <Logo size={38} />
          </Link>
          <nav aria-label="Разделы" className="flex items-center gap-2 lg:gap-[1.25rem]">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                title={item.label}
                className={({ isActive }) =>
                  cx(
                    "inline-flex items-center justify-center transition-colors duration-150",
                    isActive
                      ? "h-12 gap-[0.625rem] rounded-[2.0625rem] bg-light px-5 text-on-light shadow-pill lg:h-[4.0625rem] lg:px-[2.5rem]"
                      : "glass size-12 rounded-pill text-subtle hover:text-text lg:size-[4.125rem]",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon name={item.icon} size={item.iconSize} />
                    <span className={cx("text-lg font-semibold lg:text-[1.5rem]", isActive ? "hidden md:inline" : "sr-only")}>{item.label}</span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>
          <UserPill />
        </div>
      </header>
      <main className="mx-auto max-w-[120rem] px-4 pt-8 pb-40 lg:px-[5rem] lg:pt-[6.25rem]">
        <Outlet />
      </main>
      <AgentLauncher />
    </div>
  );
}

/** Right-hand pill: search shortcut, notifications, account menu (design: 20px gaps, 50px avatar). */
function UserPill() {
  const account = useAccount();
  const { signOut } = useSession();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="glass flex h-12 items-center gap-3 rounded-pill px-3 lg:h-[4.125rem] lg:gap-[1.25rem] lg:px-[1.75rem]">
      <Link to="/candidates" aria-label="Поиск соискателей" className="hidden text-text hover:opacity-80 sm:inline-flex">
        <Icon name="magnifier" size={30} />
      </Link>
      <NotificationsMenu />
      <div ref={root} className="relative">
        <button
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label="Аккаунт"
          onClick={() => setOpen((value) => !value)}
          className="inline-flex cursor-pointer items-center gap-2 text-text lg:gap-[1.25rem]"
        >
          <Avatar name={account.email} size={50} shape="circle" />
          <Icon name="chevron-down" size={20} className={cx("transition-transform duration-150", open && "rotate-180")} />
        </button>
        {open && (
          <div role="menu" className="glass absolute top-full right-0 z-40 mt-4 w-64 rounded-field p-2">
            <p className="truncate px-3 py-2 text-sm text-muted">{account.email}</p>
            <button
              type="button"
              role="menuitem"
              onClick={() => void signOut()}
              className="w-full cursor-pointer rounded-[0.625rem] px-3 py-2 text-left text-text hover:bg-white/10"
            >
              Выйти
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

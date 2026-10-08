import { Avatar, Backdrop, Icon, Logo, cx, type IconName } from "@comit/ui-kit";
import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet } from "react-router";
import { AgentLauncher } from "../agent/AgentOverlay";
import { useAccount, useSession } from "../auth/session";

/** Top-level sections of the employer cabinet, in Figma nav order. */
export const NAV_ITEMS: ReadonlyArray<{ to: string; label: string; icon: IconName }> = [
  { to: "/vacancies", label: "Вакансии", icon: "bars-sort" },
  { to: "/resumes", label: "Резюме", icon: "resumes" },
  { to: "/candidates", label: "Соискатели", icon: "people" },
  { to: "/statistics", label: "Статистика", icon: "chart-line" },
];

/** Authenticated layout: backdrop, sticky top navigation and the routed page. */
export function AppShell() {
  return (
    <div className="relative min-h-dvh">
      <Backdrop />
      <header className="sticky top-0 z-30 bg-gradient-to-b from-bg/90 to-transparent px-4 pt-4 pb-6 sm:px-10 sm:pt-8 xl:px-20 xl:pt-[50px]">
        <div className="mx-auto flex max-w-[1760px] items-center justify-between gap-3">
          <Link to="/vacancies" aria-label="COMIT — на главную" className="glass inline-flex h-12 items-center rounded-pill border border-glass-border px-4 sm:h-[66px] sm:px-8">
            <Logo size={30} />
          </Link>
          <nav aria-label="Разделы" className="flex items-center gap-2 sm:gap-3 lg:gap-5">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                title={item.label}
                className={({ isActive }) =>
                  cx(
                    "inline-flex h-12 items-center justify-center gap-2.5 rounded-pill transition-colors duration-150 sm:h-[66px]",
                    isActive
                      ? "bg-light px-5 text-on-light shadow-pill sm:px-10"
                      : "glass w-12 border border-glass-border text-subtle hover:text-text sm:w-[66px]",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon name={item.icon} size={22} />
                    <span className={cx("text-lg font-semibold sm:text-2xl", isActive ? "hidden md:inline" : "sr-only")}>{item.label}</span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>
          <AccountMenu />
        </div>
      </header>
      <main className="mx-auto max-w-[1760px] px-4 pt-2 pb-32 sm:px-10 sm:pt-6 xl:px-20 xl:pt-14">
        <Outlet />
      </main>
      <AgentLauncher />
    </div>
  );
}

/** Right-hand account pill with sign-out; closes on outside click and Escape. */
function AccountMenu() {
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
    <div ref={root} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Аккаунт"
        onClick={() => setOpen((value) => !value)}
        className="glass inline-flex h-12 cursor-pointer items-center gap-2 rounded-pill border border-glass-border px-2 text-text sm:h-[66px] sm:gap-3 sm:px-4"
      >
        <Avatar name={account.email} size={40} shape="circle" />
        <Icon name="chevron-down" size={18} className={cx("transition-transform duration-150", open && "rotate-180")} />
      </button>
      {open && (
        <div role="menu" className="glass absolute top-full right-0 mt-2 w-64 rounded-field border border-glass-border p-2 shadow-xl">
          <p className="truncate px-3 py-2 text-sm text-muted">{account.email}</p>
          <button
            type="button"
            role="menuitem"
            onClick={() => void signOut()}
            className="w-full cursor-pointer rounded-[10px] px-3 py-2 text-left text-text hover:bg-white/10"
          >
            Выйти
          </button>
        </div>
      )}
    </div>
  );
}

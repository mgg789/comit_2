import { useEffect, useId, useRef, type ComponentProps, type ReactNode, type Ref } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Icon } from "./design";

/** Existing employer header; role-specific navigation, API actions and assistant lifecycle stay in the application. */
export function AppShellHeader({ brandHref, brandLabel, navigationLabel, items, selected, navigate, email, menu, toggleMenu, menuContent, toolsRef, menuRef, searchLabel, onSearch, secondaryLabel, onSecondary, secondaryIcon = "message", assistantOpen = false, toolsContent, overlayContent }: {
  brandHref: string; brandLabel: string; navigationLabel: string;
  items: readonly { id: string; label: string; path: string; icon: ComponentProps<typeof Icon>["name"] }[];
  selected: string; navigate: (path: string) => void; email: string;
  menu: boolean; toggleMenu: () => void; menuContent: ReactNode;
  toolsRef?: Ref<HTMLDivElement>; menuRef?: Ref<HTMLDivElement>;
  searchLabel: string; onSearch: () => void; secondaryLabel: string; onSecondary: () => void;
  toolsContent?: ReactNode; overlayContent?: ReactNode;
  secondaryIcon?: ComponentProps<typeof Icon>["name"]; assistantOpen?: boolean;
}) {
  return <header className={`topbar ${assistantOpen ? "assistant-open" : ""}`}>
    <a className="brand glass" href={brandHref} aria-label={brandLabel}><Icon name="logo" /><span className="brand-word">comit</span></a>
    <ShellNavigation navigationLabel={navigationLabel} items={items} selected={selected} navigate={navigate} />
    <div className="account-tools glass" ref={toolsRef}>
      <button className="plain-icon" aria-label={searchLabel} onClick={onSearch}><Icon name="search" /></button>
      {toolsContent}
      <button className="plain-icon" aria-label={secondaryLabel} onClick={onSecondary}><Icon name={secondaryIcon} /></button>
      <button className="plain-icon" onClick={toggleMenu} aria-label="Открыть меню аккаунта" aria-expanded={menu}><span className="account-avatar">{email.slice(0, 1).toUpperCase()}</span><Icon name="chevron" /></button>
    </div>
    {overlayContent}
    <AnimatePresence>{menu && <motion.div ref={menuRef} className="account-menu glass" initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}><p>{email}</p>{menuContent}</motion.div>}</AnimatePresence>
  </header>;
}

/** Employer confirmation presentation with optional focus management for apps without a global modal manager. */
export function Dialog({ title, close, children, manageFocus = true }: { title: string; close: () => void; children: ReactNode; manageFocus?: boolean }) {
  const titleId = useId();
  const panel = useRef<HTMLElement>(null);
  const onClose = useRef(close);
  onClose.current = close;
  useEffect(() => {
    if (!manageFocus) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const controls = () => Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]') ?? []).filter(item => item.getClientRects().length > 0);
    (panel.current?.querySelector<HTMLElement>("input:not(:disabled)") ?? controls()[0])?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose.current(); }
      if (event.key !== "Tab") return;
      const list = controls(), first = list[0], last = list.at(-1);
      if (!first || !last) return;
      if (!panel.current?.contains(document.activeElement) || (event.shiftKey && document.activeElement === first)) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", key);
    return () => { document.body.style.overflow = overflow; document.removeEventListener("keydown", key); previous?.focus(); };
  }, [manageFocus]);
  return <motion.div className="modal-shade" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={event => { if (event.target === event.currentTarget) close(); }}><section ref={panel} className="modal account-confirmation glass" role="dialog" aria-modal="true" aria-labelledby={titleId}><h2 id={titleId}>{title}</h2>{children}</section></motion.div>;
}

type ShellNavigationProps = {
  navigationLabel: string;
  items: readonly { id: string; label: string; path: string; icon: ComponentProps<typeof Icon>["name"] }[];
  selected: string;
  navigate: (path: string) => void;
};

function ShellNavigation({ navigationLabel, items, selected, navigate }: ShellNavigationProps) {
  return <nav className="nav" aria-label={navigationLabel}>{items.map(item => <button key={item.id} className={`nav-item glass ${selected === item.id ? "selected" : ""}`} onClick={() => navigate(item.path)} aria-label={item.label} aria-current={selected === item.id ? "page" : undefined}>
      <Icon name={item.icon} /><motion.span className="nav-label" aria-hidden={selected !== item.id} initial={false} animate={{ width: selected === item.id ? "auto" : 0, opacity: selected === item.id ? 1 : 0, marginLeft: selected === item.id ? "10rem" : "0rem" }} transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}>{item.label}</motion.span>
    </button>)}</nav>;
}

/** Public entrance shares the authenticated shell navigation, branding and motion without account controls. */
export function PublicShellHeader({ brandHref, loginHref, ...navigation }: ShellNavigationProps & { brandHref: string; loginHref: string }) {
  return <header className="topbar public-topbar">
    <a className="brand glass" href={brandHref} aria-label="Comit — главная"><Icon name="logo" /><span className="brand-word">comit</span></a>
    <ShellNavigation {...navigation} />
    <a className="action glass public-login" href={loginHref}>Войти<Icon name="login" /></a>
  </header>;
}

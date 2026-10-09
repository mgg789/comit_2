import { useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { ApiError } from "@comit/api-client";
import type { AccountView } from "@comit/shared";
import { Action, Backdrop, Icon, PageTitle, State } from "@comit/ui-kit/design";
import { EmailLogin } from "@comit/ui-kit/email-login";
import { api, errorText, useRoute } from "./runtime";
import { ProfileScreen, OffersScreen, OfferScreen, StatisticsScreen } from "./screens";
const navigation = [
  { path: "/resume", label: "Резюме", icon: "resumes" },
  { path: "/offers", label: "Предложения", icon: "vacancies" },
  { path: "/statistics", label: "Статистика", icon: "statistics" },
  { path: "/settings", label: "Настройки", icon: "settings" },
] as const;
const legal = import.meta.glob("../../../docs/legal/*.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
/** Cookie-authenticated candidate shell shares the employer design and motion primitives. */
export default function App() {
  const { route, navigate } = useRoute();
  const [account, setAccount] = useState<AccountView | null>(null);
  const [checking, setChecking] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [search, setSearch] = useState(false);
  const [query, setQuery] = useState("");
  const [logout, setLogout] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const menuRoot = useRef<HTMLDivElement>(null);
  async function checkAccount() {
    setChecking(true);
    setAuthError(null);
    try {
      setAccount(await api.getAccount("candidate"));
    }
    catch (reason) {
      setAccount(null);
      if (!(reason instanceof ApiError && reason.status === 401))
        setAuthError(errorText(reason));
    }
    finally {
      setChecking(false);
    }
  }
  useEffect(() => { void checkAccount(); }, []);
  useEffect(() => { setMenu(false); setSearch(false); }, [route]);
  useEffect(() => {
    if (!menu)
      return;
    const dismiss = (event: PointerEvent) => { if (event.target instanceof Node && !menuRoot.current?.contains(event.target))
      setMenu(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape")
      setMenu(false); };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [menu]);
  async function signOut() {
    if (busy)
      return;
    setBusy(true);
    setFailure(null);
    try {
      await api.logout("candidate");
      setAccount(null);
      setLogout(false);
      setQuery("");
    }
    catch (reason) {
      setFailure(errorText(reason));
    }
    finally {
      setBusy(false);
    }
  }
  const selected = route.startsWith("/offer") ? "/offers" : route;
  return <MotionConfig reducedMotion="user">
  <div className={`candidate-app comit-design ${!account ? "is-login" : ""}`}>
  <Backdrop />{account && <CandidateArt route={route}/>}
  {route.startsWith("/legal/") ? <main className="main-content candidate-legal panel">
    <a href="#/resume">← Вернуться</a>
    <pre>{legal[`../../../docs/legal/${route.slice(7)}.md`] ?? "Документ не найден"}</pre>
    </main>
      : checking ? <main className="main-content">
      <State loading/>
      </main>
        : authError ? <main className="main-content">
        <State error={authError} retry={() => void checkAccount()}/>
        </main>
          : !account ? <EmailLogin requestCode={email => api.requestLoginCode("candidate", email)} verifyCode={async (body) => (await api.verifyLoginCode("candidate", body)).account} onLogin={setAccount} errorText={errorText}/>
            : <>
    <header className="topbar">
     <a className="brand glass" href="#/resume" aria-label="Comit — резюме">
            <Icon name="logo"/>
            <span className="brand-word">comit</span>
            </a>
     <nav className="nav" aria-label="Навигация кандидата">{navigation.map(item => <button key={item.path} className={`nav-item glass ${selected === item.path ? "selected" : ""}`} aria-label={item.label} aria-current={selected === item.path ? "page" : undefined} onClick={() => navigate(item.path)}>{item.path === "/resume" || item.path === "/offers" ? <span className="icon" aria-hidden="true">
                <img src={`${import.meta.env.BASE_URL}design/${item.path === "/resume" ? "abd1b.svg" : "0f423.svg"}`} alt=""/>
                </span> : <Icon name={item.icon}/>}<AnimatePresence initial={false}>{selected === item.path && <motion.span className="nav-label" initial={{ width: 0, opacity: 0 }} animate={{ width: "auto", opacity: 1 }} exit={{ width: 0, opacity: 0 }} transition={{ duration: .3 }}>
                <span className="candidate-nav-label">{item.label}</span>
                </motion.span>}</AnimatePresence>
              <span className="tooltip">{item.label}</span>
              </button>)}</nav>
     <div className="account-tools glass" ref={menuRoot}>
      <button className="plain-icon" aria-label="Поиск предложений" onClick={() => { setMenu(false); setSearch(true); }}>
            <Icon name="search"/>
            </button>
      <button className="plain-icon" aria-label="Предложения работы" onClick={() => navigate("/offers")}>
            <Icon name="bell"/>
            </button>
      <button className="plain-icon" aria-label="Меню аккаунта" aria-expanded={menu} onClick={() => setMenu(value => !value)}>
            <span className="account-avatar">{account.email.slice(0, 1).toUpperCase()}</span>
            <Icon name="chevron"/>
            </button>
      <AnimatePresence>{menu && <motion.div className="account-menu glass" initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
              <p>{account.email}</p>
              <Action onClick={() => navigate("/offers")}>Мои предложения</Action>
              <Action onClick={() => { setMenu(false); setLogout(true); setFailure(null); }}>Выйти</Action>
              </motion.div>}</AnimatePresence>
     </div>
    </header>
    <main className="main-content candidate-content" key={account.id}>
     {route === "/resume" ? <ProfileScreen navigate={navigate}/> : route === "/offers" ? <OffersScreen navigate={navigate} query={query}/> : route.startsWith("/offer/") ? <OfferScreen id={decodeRouteId(route.slice(7))} navigate={navigate}/> : route === "/statistics" ? <StatisticsScreen navigate={navigate}/> : route === "/settings" ? <>
              <PageTitle title="Настройки"/>
              <section className="panel candidate-settings">
              <h2>Аккаунт</h2>
              <p>{account.email}</p>
              <p>Данные резюме и согласие на показ работодателям можно изменить в резюме.</p>
              <Action variant="primary" onClick={() => navigate("/resume")}>К резюме</Action>
              <Action onClick={() => { setLogout(true); setFailure(null); }}>Выйти из аккаунта</Action>
              <a href="#/legal/privacy-policy">Политика конфиденциальности</a>
              </section>
              </> : <State empty="Страница не найдена" retry={() => navigate("/resume")}/>}
    </main>
    {search && <Dialog title="Поиск предложений" close={() => setSearch(false)}>
              <form onSubmit={event => { event.preventDefault(); navigate("/offers"); setSearch(false); }}>
              <label className="field">
              <span>Компания или название вакансии</span>
              <input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="Что ищем?"/>
              </label>
              <Action variant="primary" type="submit">Найти</Action>
              </form>
              </Dialog>}
    {logout && <Dialog title="Выйти из аккаунта?" close={() => { if (!busy)
                setLogout(false); }}>
              <p>Для возвращения понадобится код из письма.</p>{failure && <p role="alert">{failure}</p>}<div className="candidate-dialog-actions">
              <Action disabled={busy} onClick={() => setLogout(false)}>Отмена</Action>
              <Action variant="primary" disabled={busy} onClick={() => void signOut()}>{busy ? "Выходим…" : "Выйти"}</Action>
              </div>
              </Dialog>}
   </>}
 </div>
  </MotionConfig>;
}
/** Dialog traps keyboard focus, restores its trigger and locks background scrolling. */
function Dialog({ title, close, children }: {
  title: string;
  close: () => void;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const onClose = useRef(close);
  onClose.current = close;
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const controls = () => Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),a[href],[tabindex="0"]') ?? []);
    (panel.current?.querySelector<HTMLElement>("input:not(:disabled)") ?? controls()[0])?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose.current();
      }
      if (event.key === "Tab") {
        const list = controls();
        const first = list[0];
        const last = list[list.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        }
        else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => { document.body.style.overflow = overflow; document.removeEventListener("keydown", key); previous?.focus(); };
  }, []);
  return <div className="candidate-modal" onClick={event => { if (event.target === event.currentTarget)
    close(); }}>
  <motion.div ref={panel} className="panel candidate-dialog" role="dialog" aria-modal="true" aria-label={title} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
  <div className="candidate-dialog-heading">
  <h2>{title}</h2>
  <Action aria-label="Закрыть" onClick={close}>×</Action>
  </div>{children}</motion.div>
  </div>;
}
/** Exact exported backdrop art is static; account and portrait slots remain real data. */
function CandidateArt({ route }: {
  route: string;
}) {
  const group = route === "/statistics" ? "statistics" : route.startsWith("/offer") ? "offers" : "resume";
  const asset = (name: string) => `${import.meta.env.BASE_URL}design/${name}`;
  return <div className={`candidate-art candidate-art-${group}`} aria-hidden="true">{group === "resume" ? <>
    <img className="candidate-art-one" src={asset("f4e60.png")} alt=""/>
    <img className="candidate-art-two" src={asset("f4e60.png")} alt=""/>
    <img className="candidate-art-three" src={asset("44a44.png")} alt=""/>
    </> : group === "offers" ? <img className="candidate-art-one" src={asset("bb5d7.png")} alt=""/> : <>
    <img className="candidate-art-one" src={asset("af3af.png")} alt=""/>
    <img className="candidate-art-two" src={asset("af3af.png")} alt=""/>
    </>}</div>;
}
/** Malformed user-entered URL escapes must not crash the authenticated shell. */
function decodeRouteId(value: string): string { try {
  return decodeURIComponent(value);
}
catch {
  return "";
} }

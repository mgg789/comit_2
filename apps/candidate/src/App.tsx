import { useEffect, useRef, useState } from "react";
import { AnimatePresence, MotionConfig } from "framer-motion";
import { AppShellHeader, Dialog } from "@comit/ui-kit/app-shell";
import { ApiError } from "@comit/api-client";
import type { AccountView } from "@comit/shared";
import { Action, Backdrop, PageTitle, State } from "@comit/ui-kit/design";
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
  const menuPanel = useRef<HTMLDivElement>(null);
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
    const dismiss = (event: PointerEvent) => { if (event.target instanceof Node && !menuRoot.current?.contains(event.target) && !menuPanel.current?.contains(event.target))
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
    <AppShellHeader brandHref="#/resume" brandLabel="Comit — резюме" navigationLabel="Навигация кандидата"
      items={navigation.map(item => ({ ...item, id: item.path }))} selected={selected} navigate={navigate} email={account.email}
      menu={menu} toggleMenu={() => setMenu(value => !value)} toolsRef={menuRoot} menuRef={menuPanel}
      searchLabel="Поиск предложений" onSearch={() => { setMenu(false); setSearch(true); }} secondaryLabel="Предложения работы" secondaryIcon="bell" onSecondary={() => navigate("/offers")}
      menuContent={<><Action onClick={() => navigate("/offers")}>Мои предложения</Action><Action onClick={() => { setMenu(false); setLogout(true); setFailure(null); }}>Выйти</Action></>}
    />
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
    <AnimatePresence>{search && <Dialog title="Поиск предложений" close={() => setSearch(false)}>
              <form onSubmit={event => { event.preventDefault(); navigate("/offers"); setSearch(false); }}>
              <label className="field">
              <span>Компания или название вакансии</span>
              <input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="Что ищем?"/>
              </label>
              <Action variant="primary" type="submit">Найти</Action>
              </form>
              </Dialog>}</AnimatePresence>
    <AnimatePresence>{logout && <Dialog title="Выйти из аккаунта?" close={() => { if (!busy)
                setLogout(false); }}>
              <p>Для возвращения понадобится код из письма.</p>{failure && <p role="alert">{failure}</p>}<div className="account-confirmation-actions">
              <Action disabled={busy} onClick={() => setLogout(false)}>Отмена</Action>
              <Action variant="primary" disabled={busy} onClick={() => void signOut()}>{busy ? "Выходим…" : "Выйти"}</Action>
              </div>
              </Dialog>}</AnimatePresence>
   </>}
 </div>
  </MotionConfig>;
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

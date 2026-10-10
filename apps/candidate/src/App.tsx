import { useEffect, useRef, useState } from "react";
import { AnimatePresence, MotionConfig } from "framer-motion";
import { PersistentStateScope, usePersistentState } from "@comit/ui-kit/ui-state";
import { AppShellHeader, Dialog, homePageHref } from "@comit/ui-kit/app-shell";
import { ApiError } from "@comit/api-client";
import type { AccountView } from "@comit/shared";
import { Action, Backdrop, PageTitle, State } from "@comit/ui-kit/design";
import { EmailLogin } from "@comit/ui-kit/email-login";
import { api, errorText, useRoute } from "./runtime";
import { ProfileScreen, OffersScreen, OfferScreen, StatisticsScreen, ChatsScreen, ChatScreen } from "./screens";
const navigation = [
  { path: "/resume", label: "Резюме", icon: "resumes" },
  { path: "/offers", label: "Предложения", icon: "vacancies" },
  { path: "/statistics", label: "Статистика", icon: "statistics" },
  { path: "/settings", label: "Настройки", icon: "settings" },
] as const;
const legal = import.meta.glob("../../../docs/legal/*.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
/** Cookie-authenticated candidate shell shares the employer design and motion primitives. */
export default function App() {
  const { route: fullRoute, navigate } = useRoute();
  const route = fullRoute.split("?")[0] || "/resume";
  const [account, setAccount] = useState<AccountView | null>(null);
  const [checking, setChecking] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [query, setQuery] = usePersistentState(`candidate:${account?.id || "anonymous"}:search`, "");
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
  useEffect(() => { setMenu(false); }, [route]);
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
    }
    catch (reason) {
      setFailure(errorText(reason));
    }
    finally {
      setBusy(false);
    }
  }
  const selected = route.startsWith("/offer") || route.startsWith("/chat") ? "/offers" : route;
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
          : !account ? <EmailLogin homeHref={homePageHref(window.location.origin, import.meta.env.DEV, import.meta.env.VITE_WELCOME_URL)} requestCode={email => api.requestLoginCode("candidate", email)} verifyCode={async (body) => (await api.verifyLoginCode("candidate", body)).account} onLogin={setAccount} errorText={errorText}/>
            : <PersistentStateScope scope={`candidate:${account.id}`}>
    <AppShellHeader brandHref={homePageHref(window.location.origin, import.meta.env.DEV, import.meta.env.VITE_WELCOME_URL)} brandLabel="Comit — главная" navigationLabel="Навигация кандидата"
      items={navigation.map(item => ({ ...item, id: item.path }))} selected={selected} navigate={navigate} email={account.email}
      menu={menu} toggleMenu={() => setMenu(value => !value)} toolsRef={menuRoot} menuRef={menuPanel}
      searchLabel="Поиск предложений" onSearch={() => { setMenu(false); if (route === "/offers") window.dispatchEvent(new Event("candidate:focus-offer-search")); else navigate("/offers?search=1"); }} secondaryLabel="Чаты" secondaryIcon="message" onSecondary={() => navigate("/chat")}
      menuContent={<><Action onClick={() => navigate("/chat")}>Мои чаты</Action><Action onClick={() => navigate("/offers")}>Мои предложения</Action><Action onClick={() => { setMenu(false); setLogout(true); setFailure(null); }}>Выйти</Action></>}
    />
    <main className="main-content candidate-content" key={account.id}>
     {route === "/resume" ? <ProfileScreen navigate={navigate}/> : route === "/offers" ? <OffersScreen navigate={navigate} query={query} setQuery={setQuery} focusSearch={new URLSearchParams(fullRoute.split("?")[1]).get("search") === "1"}/> : route.startsWith("/offer/") ? <OfferScreen id={decodeRouteId(route.slice(7))} navigate={navigate}/> : route === "/chat" ? <ChatsScreen navigate={navigate}/> : route.startsWith("/chat/") ? <ChatScreen id={decodeRouteId(route.slice(6))} route={fullRoute} navigate={navigate}/> : route === "/statistics" ? <StatisticsScreen navigate={navigate}/> : route === "/settings" ? <>
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

    <AnimatePresence>{logout && <Dialog title="Выйти из аккаунта?" close={() => { if (!busy)
                setLogout(false); }}>
              <p>Для возвращения понадобится код из письма.</p>{failure && <p role="alert">{failure}</p>}<div className="account-confirmation-actions">
              <Action disabled={busy} onClick={() => setLogout(false)}>Отмена</Action>
              <Action variant="primary" disabled={busy} onClick={() => void signOut()}>{busy ? "Выходим…" : "Выйти"}</Action>
              </div>
              </Dialog>}</AnimatePresence>
   </PersistentStateScope>}
 </div>
  </MotionConfig>;
}
/** Exact exported backdrop art is static; account and portrait slots remain real data. */
function CandidateArt({ route }: {
  route: string;
}) {
  const group = route === "/statistics" ? "statistics" : route.startsWith("/offer") || route.startsWith("/chat") ? "offers" : "resume";
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

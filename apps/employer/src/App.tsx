import { useEffect, useRef, useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { ApiError } from "@comit/api-client";
import type { AccountView, AgentsDraftView } from "@comit/shared";
import { api, asset, errorText, useRoute } from "./runtime";
import { Action, Backdrop, Icon, State } from "./ui";
import { Login } from "./login";
import { LegalDocument } from "./legal";
import { Settings } from "./settings";
import { useEmployerPreferences } from "./preferences";
import { OrganizationAgent, RecruitmentChat } from "./communications";
import { Vacancies, VacancyDetail, VacancyEditor } from "./vacancies";
import {
  NotificationsButton,
  NotificationsPanel,
  useRecruitmentNotifications,
} from "./notifications-menu";
import {
  Resume,
  Resumes,
  Showcase,
  Statistics,
  VacancyResumes,
} from "./discovery";

/** Employer SPA with a persistent global assistant and cookie-authenticated routes. */
export default function App() {
  const { route, navigate } = useRoute();
  const [account, setAccount] = useState<AccountView | null>(null);
  const [checking, setChecking] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [agent, setAgent] = useState(false);
  const afterAgentClose = useRef<(() => void) | null>(null);
  function leaveAgent(action: () => void) {
    setMenu(false);
    if (agent || afterAgentClose.current) {
      afterAgentClose.current = action;
      setAgent(false);
    } else action();
  }
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [notificationsSeenBefore, setNotificationsSeenBefore] = useState(0);
  const notificationsRef = useRef<HTMLDivElement>(null);
  const accountToolsRef = useRef<HTMLDivElement>(null);
  const [accountAction, setAccountAction] = useState<
    "logout" | "logout-all" | "delete" | null
  >(null);
  const [accountBusy, setAccountBusy] = useState(false);
  const [accountActionError, setAccountActionError] = useState<string | null>(null);
  const { preferences } = useEmployerPreferences();
  const notifications = useRecruitmentNotifications(account?.id);
  function confirmAccountAction(action: "logout" | "logout-all" | "delete") {
    setMenu(false);
    setAccountActionError(null);
    setAccountAction(action);
  }
  async function logoutConfirmed() {
    if (accountBusy || !accountAction || accountAction === "delete") return;
    setAccountBusy(true);
    setAccountActionError(null);
    try {
      await api.logout("employer", accountAction === "logout-all");
      setAccount(null);
      setAgent(false);
      setEditor(null);
      setAccountAction(null);
    } catch (error) {
      setAccountActionError(errorText(error));
    } finally {
      setAccountBusy(false);
    }
  }
  const [editor, setEditor] = useState<AgentsDraftView | "new" | null>(null);
  const [checkpoint, setCheckpoint] = useState(0);
  async function checkAccount() {
    setChecking(true);
    setAuthError(null);
    try {
      setAccount(await api.getAccount("employer"));
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401))
        setAuthError(errorText(error));
      setAccount(null);
    } finally {
      setChecking(false);
    }
  }
  useEffect(() => {
    void checkAccount();
  }, []);
  useEffect(() => {
    if (!account) return;
    const heartbeat = () => {
      void api
        .setOrganizationPresence(document.visibilityState === "visible")
        .catch(() => undefined);
    };
    heartbeat();
    const timer = window.setInterval(heartbeat, 25000);
    document.addEventListener("visibilitychange", heartbeat);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", heartbeat);
    };
  }, [account?.id]);
  useEffect(() => {
    let dialog: HTMLElement | null = null;
    let prior: HTMLElement | null = null;
    const focusable = (root: HTMLElement) => Array.from(
      root.querySelectorAll<HTMLElement>(
        'button,input:not([type="hidden"]):not([type="file"]),textarea,select,a[href],[tabindex="0"]',
      ),
    ).filter((item) =>
      item.tabIndex >= 0 && !item.matches(":disabled") &&
      !item.closest('[inert],[aria-hidden="true"]') &&
      item.getClientRects().length > 0 && getComputedStyle(item).visibility !== "hidden",
    );
    const update = () => {
      const next =
        Array.from(
          document.querySelectorAll<HTMLElement>(
            '[role="dialog"][aria-modal="true"]',
          ),
        ).at(-1) || null;
      if (next === dialog) return;
      if (next) {
        if (!dialog)
          prior =
            document.activeElement instanceof HTMLElement
              ? document.activeElement
              : null;
        dialog = next;
        focusable(next)[0]?.focus();
      } else {
        dialog = null;
        prior?.focus();
        prior = null;
      }
    };
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !dialog) return;
      const items = focusable(dialog);
      const first = items[0],
        last = items.at(-1);
      if (!first || !last) return;
      if (
        !dialog.contains(document.activeElement) ||
        (event.shiftKey && document.activeElement === first)
      ) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    const observer = new MutationObserver(update);
    observer.observe(document.body, { childList: true, subtree: true });
    document.addEventListener("keydown", trap);
    return () => {
      observer.disconnect();
      document.removeEventListener("keydown", trap);
    };
  }, []);
  useEffect(() => {
    setMenu(false);
    setNotificationsOpen(false);
    window.scrollTo(0, 0);
  }, [route]);
  useEffect(() => {
    if (!notificationsOpen) return;
    const outside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !notificationsRef.current?.contains(event.target) &&
        !accountToolsRef.current?.contains(event.target)
      ) setNotificationsOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [notificationsOpen]);
  useEffect(() => {
    if (!menu) return;
    const outside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !menuRef.current?.contains(event.target) &&
        !accountToolsRef.current?.contains(event.target)
      ) setMenu(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [menu]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (accountBusy) return;
        setAccountAction(null);
        setAgent(false);
        setMenu(false);
        setNotificationsOpen(false);
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [accountBusy]);
  const [path, query = ""] = route.split("?");
  const pieces = (path || "/vacancies").split("/").filter(Boolean);
  const draftQuery = new URLSearchParams(query).get("draft") || undefined;
  const selected =
    pieces[0] === "settings"
      ? "settings"
      : pieces[0] === "statistics"
      ? "statistics"
      : pieces[0] === "resumes" ||
          pieces[0] === "resume" ||
          (pieces[0] === "vacancy" && pieces[2] === "resumes")
        ? "resumes"
        : pieces[0] === "showcase"
          ? "candidates"
          : "vacancies";
  let screen;
  if (pieces[0] === "settings" && account)
    screen = (
      <Settings
        account={account}
        onConfirmLogoutAll={() => confirmAccountAction("logout-all")}
        onConfirmDelete={() => confirmAccountAction("delete")}
      />
    );
  else if (pieces[0] === "vacancy" && pieces[1])
    screen =
      pieces[2] === "resumes" ? (
        <VacancyResumes draftId={pieces[1]} navigate={navigate} />
      ) : (
        <VacancyDetail
          draftId={pieces[1]}
          navigate={navigate}
          onEdit={setEditor}
        />
      );
  else if (pieces[0] === "resume" && pieces[1])
    screen = (
      <Resume
        candidateId={pieces[1]}
        draftId={draftQuery}
        fromVacancy={new URLSearchParams(query).get("from") === "vacancy"}
        navigate={navigate}
      />
    );
  else if (pieces[0] === "resumes") screen = <Resumes navigate={navigate} />;
  else if (pieces[0] === "showcase")
    screen = (
      <Showcase
        navigate={navigate}
        focusSearch={new URLSearchParams(query).get("search") === "1"}
      />
    );
  else if (pieces[0] === "statistics")
    screen = <Statistics navigate={navigate} />;
  else if (pieces[0] === "chat" && account)
    screen = (
      <RecruitmentChat
        account={account}
        onBack={() => navigate("/vacancies")}
        initialProcessId={pieces[1]}
        onProfile={(candidateId) => navigate(`/resume/${candidateId}`)}
      />
    );
  else
    screen = (
      <Vacancies navigate={navigate} onCreate={() => setEditor("new")} />
    );
  return (
    <MotionConfig reducedMotion={preferences.reducedMotion ? "always" : "user"}>
      <div className={`employer-app ${account ? "" : "is-login"} ${preferences.reducedMotion ? "reduce-motion" : ""}`}>
        <Backdrop />
        {pieces[0] === "legal" ? (
          <LegalDocument name={pieces[1] || "privacy-policy"} />
        ) : checking ? (
          <main className="auth-check">
            <State loading />
          </main>
        ) : !account ? (
          <>
            {authError && (
              <div className="auth-warning">
                <p role="alert">{authError}</p>
                <button onClick={() => void checkAccount()}>Повторить</button>
              </div>
            )}
            <Login
              onLogin={(value) => {
                setAccount(value);
                setAuthError(null);
              }}
            />
          </>
        ) : (
          <>
            <header className={`topbar ${agent ? "assistant-open" : ""}`}>
              <a
                className="brand glass"
                href="#/vacancies"
                aria-label="Comit — вакансии"
              >
                <Icon name="logo" />
                <span className="brand-word">comit</span>
              </a>
              <nav className="nav" aria-label="Навигация работодателя">
                {(
                  [
                    { id: "vacancies", label: "Вакансии", path: "/vacancies" },
                    { id: "resumes", label: "Резюме", path: "/resumes" },
                    { id: "candidates", label: "Соискатели", path: "/showcase" },
                    {
                      id: "statistics",
                      label: "Статистика",
                      path: "/statistics",
                    },
                    { id: "settings", label: "Настройки", path: "/settings" },
                  ] as const
                ).map((item) => (
                  <button
                    key={item.id}
                    className={`nav-item glass ${selected === item.id ? "selected" : ""}`}
                    onClick={() => {
                      navigate(item.path);
                    }}
                    aria-label={item.label}
                    aria-current={selected === item.id ? "page" : undefined}
                  >
                    <Icon name={item.id} />
                    <motion.span
                      className="nav-label"
                      aria-hidden={selected !== item.id}
                      initial={false}
                      animate={{
                        width: selected === item.id ? "auto" : 0,
                        opacity: selected === item.id ? 1 : 0,
                        marginLeft: selected === item.id ? "10rem" : "0rem",
                      }}
                      transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
                    >
                      {item.label}
                    </motion.span>
                  </button>
                ))}
              </nav>
              <div className="account-tools glass" ref={accountToolsRef}>
                <button
                  className="plain-icon"
                  aria-label="Поиск кандидатов"
                  onClick={() => leaveAgent(() => {
                    if (pieces[0] === "showcase")
                      window.dispatchEvent(
                        new Event("employer:focus-candidate-search"),
                      );
                    else navigate("/showcase?search=1");
                  })}
                >
                  <Icon name="search" />
                </button>
                <NotificationsButton
                  unseen={notifications.unseen}
                  open={notificationsOpen}
                  onClick={() => {
                    setMenu(false);
                    if (!notificationsOpen)
                      setNotificationsSeenBefore(notifications.markSeen());
                    setNotificationsOpen(!notificationsOpen);
                  }}
                />
                <button
                  className="plain-icon"
                  aria-label="Переписка и приглашения"
                  onClick={() => leaveAgent(() => navigate("/chat"))}
                >
                  <Icon name="message" />
                </button>
                <button
                  className="plain-icon"
                  onClick={() => {
                    setNotificationsOpen(false);
                    setMenu((value) => !value);
                  }}
                  aria-label="Открыть меню аккаунта"
                  aria-expanded={menu}
                >
                  <span className="account-avatar">
                    {account.email.slice(0, 1).toUpperCase()}
                  </span>
                  <Icon name="chevron" />
                </button>
              </div>
              <AnimatePresence>
                {notificationsOpen && (
                  <NotificationsPanel
                    ref={notificationsRef}
                    events={notifications.events}
                    seenBefore={notificationsSeenBefore}
                    onSelect={(processId) => {
                      setNotificationsOpen(false);
                      leaveAgent(() => navigate(`/chat/${processId}`));
                    }}
                  />
                )}
              </AnimatePresence>
              <AnimatePresence>
                {menu && (
                  <motion.div
                    ref={menuRef}
                    className="account-menu glass"
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                  >
                    <p>{account.email}</p>
                    <Action
                      onClick={() => {
                        leaveAgent(() => navigate("/chat"));
                      }}
                    >
                      Мои чаты
                    </Action>
                    <Action
                      onClick={() => confirmAccountAction("logout")}
                    >
                      Выйти
                    </Action>
                    <Action onClick={() => confirmAccountAction("delete")}>Удалить аккаунт</Action>
                  </motion.div>
                )}
              </AnimatePresence>
            </header>
            <AnimatePresence>
              {accountAction && (
                <motion.div
                  className="modal-shade"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  onClick={(event) => {
                    if (event.target === event.currentTarget && !accountBusy)
                      setAccountAction(null);
                  }}
                >
                  <section
                    className="modal account-confirmation glass"
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="account-confirmation-title"
                  >
                    <h2 id="account-confirmation-title">
                      {accountAction === "delete"
                        ? "Удалить аккаунт?"
                        : accountAction === "logout-all"
                          ? "Выйти на всех устройствах?"
                          : "Выйти из аккаунта?"}
                    </h2>
                    <p>
                      {accountAction === "delete"
                        ? "Удаление аккаунта пока недоступно: сервисы ещё не поддерживают удаление связанных вакансий, переписки и данных подбора. Аккаунт и его данные сохранятся."
                        : accountAction === "logout-all"
                          ? "Все сессии будут завершены. Для возвращения понадобится код из письма."
                          : "Для возвращения понадобится код из письма."}
                    </p>
                    {accountActionError && (
                      <p role="alert" className="form-error">{accountActionError}</p>
                    )}
                    <div className="account-confirmation-actions">
                      <Action disabled={accountBusy} onClick={() => setAccountAction(null)}>{accountAction === "delete" ? "Закрыть" : "Отмена"}</Action>
                      {accountAction !== "delete" && (
                        <Action variant="primary" disabled={accountBusy} onClick={() => void logoutConfirmed()}>
                          {accountBusy ? "Выходим…" : "Выйти"}
                        </Action>
                      )}
                    </div>
                  </section>
                </motion.div>
              )}
            </AnimatePresence>
            <main className="main-content">
              <AnimatePresence mode="wait">
                {/* Plain crossfade: each screen brings its own Reveal, so a second slide would double the motion. */}
                <motion.div
                  key={`${route}:${checkpoint}`}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.18 }}
                >
                  {screen}
                </motion.div>
              </AnimatePresence>
            </main>
            <Action
              className="agent-launcher glass glass-surface"
              onClick={() => setAgent(true)}
              aria-label="Открыть AI-агента"
            >
              <img
                src={asset("51a529e354010b613e34c8613e41f4a02916cf2e.png")}
                alt=""
              />
            </Action>
            <AnimatePresence>
              {editor && (
                <VacancyEditor
                  key={editor === "new" ? "new" : editor.draft_id}
                  draft={editor === "new" ? undefined : editor}
                  onClose={() => {
                    setEditor(null);
                    setCheckpoint((value) => value + 1);
                  }}
                  onSaved={(draft) => {
                    setEditor(null);
                    setCheckpoint((value) => value + 1);
                    navigate(`/vacancy/${draft.draft_id}`);
                  }}
                  onAgent={() => {
                    setEditor(null);
                    setAgent(true);
                  }}
                />
              )}
            </AnimatePresence>
            <AnimatePresence onExitComplete={() => {
              const action = afterAgentClose.current;
              afterAgentClose.current = null;
              action?.();
            }}>
              {agent && <OrganizationAgent onClose={() => setAgent(false)} />}
            </AnimatePresence>
          </>
        )}
      </div>
    </MotionConfig>
  );
}

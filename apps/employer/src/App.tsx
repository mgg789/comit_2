import { useEffect, useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { ApiError } from "@comit/api-client";
import type { AccountView, AgentsDraftView } from "@comit/shared";
import { api, asset, errorText, useRoute } from "./runtime";
import { Action, Backdrop, Icon, Reveal, State } from "./ui";
import { Login } from "./login";
import { LegalDocument } from "./legal";
import { OrganizationAgent, RecruitmentChat } from "./communications";
import { Vacancies, VacancyDetail, VacancyEditor } from "./vacancies";
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
  const [menu, setMenu] = useState(false);
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
        next
          .querySelector<HTMLElement>(
            'input:not([type="hidden"]),textarea,button,select',
          )
          ?.focus();
      } else {
        dialog = null;
        prior?.focus();
        prior = null;
      }
    };
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !dialog) return;
      const items = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled):not([type="file"]),textarea:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]',
        ),
      ).filter((item) => item.getClientRects().length > 0);
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
    window.scrollTo(0, 0);
  }, [route]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAgent(false);
        setMenu(false);
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, []);
  const [path, query = ""] = route.split("?");
  const pieces = (path || "/vacancies").split("/").filter(Boolean);
  const draftQuery = new URLSearchParams(query).get("draft") || undefined;
  const selected =
    pieces[0] === "statistics"
      ? "statistics"
      : pieces[0] === "resumes" ||
          pieces[0] === "resume" ||
          (pieces[0] === "vacancy" && pieces[2] === "resumes")
        ? "resumes"
        : pieces[0] === "showcase"
          ? "candidates"
          : "vacancies";
  let screen;
  if (pieces[0] === "vacancy" && pieces[1])
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
        navigate={navigate}
      />
    );
  else if (pieces[0] === "resumes") screen = <Resumes navigate={navigate} />;
  else if (pieces[0] === "showcase") screen = <Showcase navigate={navigate} />;
  else if (pieces[0] === "statistics")
    screen = <Statistics navigate={navigate} />;
  else if (pieces[0] === "chat" && account)
    screen = (
      <RecruitmentChat
        account={account}
        initialProcessId={pieces[1]}
        onProfile={(candidateId) => navigate(`/resume/${candidateId}`)}
      />
    );
  else
    screen = (
      <Vacancies navigate={navigate} onCreate={() => setEditor("new")} />
    );
  return (
    <MotionConfig reducedMotion="user">
      <div className={`employer-app ${account ? "" : "is-login"}`}>
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
                    { id: "candidates", label: "Кандидаты", path: "/showcase" },
                    {
                      id: "statistics",
                      label: "Статистика",
                      path: "/statistics",
                    },
                    { id: "settings", label: "Аккаунт" },
                  ] as const
                ).map((item) => (
                  <motion.button
                    layout
                    transition={{
                      layout: {
                        type: "spring",
                        stiffness: 360,
                        damping: 34,
                        mass: 0.85,
                      },
                    }}
                    key={item.id}
                    className={`nav-item glass ${selected === item.id ? "selected" : ""}`}
                    onClick={() => {
                      if ("path" in item) navigate(item.path);
                      else setMenu((value) => !value);
                    }}
                    aria-label={item.label}
                    aria-current={selected === item.id ? "page" : undefined}
                  >
                    <Icon name={item.id} />
                    <AnimatePresence initial={false}>
                      {selected === item.id && (
                        <motion.span
                          className="nav-label"
                          initial={{ width: 0, opacity: 0 }}
                          animate={{ width: "auto", opacity: 1 }}
                          exit={{ width: 0, opacity: 0 }}
                          transition={{
                            duration: 0.28,
                            ease: [0.22, 1, 0.36, 1],
                          }}
                        >
                          {item.label}
                        </motion.span>
                      )}
                    </AnimatePresence>
                  </motion.button>
                ))}
              </nav>
              <div className="account-tools glass">
                <button
                  className="plain-icon"
                  aria-label="Поиск кандидатов"
                  onClick={() => navigate("/showcase")}
                >
                  <Icon name="search" />
                </button>
                <button
                  className="plain-icon"
                  aria-label="Переписка и приглашения"
                  onClick={() => navigate("/chat")}
                >
                  <Icon name="bell" />
                </button>
                <button
                  className="plain-icon"
                  onClick={() => setMenu((value) => !value)}
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
                {menu && (
                  <motion.div
                    className="account-menu glass"
                    initial={{ opacity: 0, y: -8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                  >
                    <p>{account.email}</p>
                    <small>Кабинет работодателя</small>
                    <Action
                      onClick={() => {
                        navigate("/chat");
                        setMenu(false);
                      }}
                    >
                      Мои чаты
                    </Action>
                    <Action
                      onClick={async () => {
                        try {
                          await api.logout("employer");
                          setAccount(null);
                          setAgent(false);
                          setEditor(null);
                        } catch (error) {
                          setAuthError(errorText(error));
                        }
                      }}
                    >
                      Выйти
                    </Action>
                    {authError && (
                      <p className="form-error" role="alert">
                        {authError}
                      </p>
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
            </header>
            <main className="main-content">
              <AnimatePresence mode="wait">
                <Reveal key={`${route}:${checkpoint}`}>{screen}</Reveal>
              </AnimatePresence>
            </main>
            <Action
              className="agent-launcher glass"
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
            <AnimatePresence>
              {agent && <OrganizationAgent onClose={() => setAgent(false)} />}
            </AnimatePresence>
          </>
        )}
      </div>
    </MotionConfig>
  );
}

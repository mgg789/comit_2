import { RecruitmentChatList, RecruitmentConversation, type ConversationTransport } from "@comit/ui-kit/recruitment-conversation";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ApiError } from "@comit/api-client";
import type {
  AccountView,
  AgentsOrganizationBudget,
  AgentsDocumentView,
  AgentsOrganizationMessage,
  AgentsOrganizationTurn,
  AgentsPendingAnswer,
  ContactsView,
  RecruitmentAction,
  RecruitmentView,
} from "@comit/shared";
import { AgentReply, CopyMessage, agentHistoryProgress, groupAgentMessages } from "./agent-messages";
import { api, asset, documentErrorText, errorText, salary, useResource } from "./runtime";
import { Action, Field, Icon, PageTitle, State } from "./ui";
import "./communications.css";
import { ChatComposer } from "@comit/ui-kit/chat-composer";
import { budgetRemainingPercent } from "./budget";
import { useEmployerPreferences } from "./preferences";

const employerConversationTransport: ConversationTransport = {
  getMessages: (id, after, limit) => api.getChatMessages("employer", id, after, limit),
  sendMessage: (id, body) => api.sendChatMessage("employer", id, body),
  uploadFile: (id, body) => api.uploadChatFile("employer", id, body),
  downloadFile: (id, fileId) => api.downloadChatFile("employer", id, fileId),
};

/** Process states shared with the notifications bell. */
export const recruitmentStateNames: Record<RecruitmentView["state"], string> = {
  sent: "Приглашение отправлено",
  viewed: "Приглашение просмотрено",
  accepted: "Приглашение принято",
  declined: "Приглашение отклонено",
  rejected: "Отказ",
  offer_sent: "Оффер отправлен",
  hired: "Кандидат нанят",
  offer_declined: "Оффер отклонён",
  withdrawn: "Приглашение отозвано",
};

/** Original assistant artwork; no stock candidate photographs are used. */
function AssistantAvatar() {
  return (
    <span className="assistant-avatar">
      <img src={asset("51a529e354010b613e34c8613e41f4a02916cf2e.png")} alt="" />
    </span>
  );
}

/** Read private file bytes locally; only the authorized file endpoint receives them. */
async function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      typeof reader.result === "string"
        ? resolve(reader.result.split(",")[1] || "")
        : reject(new Error("file_read"));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

/** Save opaque attachment bytes without navigating to a private URL or executing HTML. */
function saveFile(name: string, base64: string) {
  const bytes = Uint8Array.from(atob(base64), (character) =>
    character.charCodeAt(0),
  );
  const url = URL.createObjectURL(
    new Blob([bytes], { type: "application/octet-stream" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Participant-scoped chats list and standalone conversation use distinct routes. */
export function RecruitmentChat({ account, initialProcessId, onProfile, onBack, onOpen, backLabel }: {
  account: AccountView; initialProcessId?: string; onProfile: (candidateId: string) => void;
  onBack: () => void; onOpen: (id: string) => void; backLabel: string;
}) {
  if (initialProcessId) return <section className="recruitment-screen">
    <div className="chat-back"><Action className="circle" onClick={onBack} aria-label="Вернуться"><Icon name="back" /></Action><h1>{backLabel}</h1></div>
    <RecruitmentThread key={initialProcessId} id={initialProcessId} account={account} onProfile={onProfile} />
  </section>;
  return <RecruitmentChats onOpen={onOpen} />;
}

/** Fetch only list snapshots here; selecting a tile opens a separate message route. */
function RecruitmentChats({ onOpen }: { onOpen: (id: string) => void }) {
  const list = useResource(() => api.getRecruitmentList("employer", 100));
  const [moreError, setMoreError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  async function loadMore() {
    if (loadingMore || !list.data?.has_more) return;
    setLoadingMore(true); setMoreError(null);
    try {
      const page = await api.getRecruitmentList("employer", 100, list.data.next_cursor || undefined, list.data.items.length);
      list.setData(current => current ? { ...page, items: [...current.items, ...page.items.filter(item => !current.items.some(prior => prior.id === item.id))] } : page);
    } catch (reason) { setMoreError(errorText(reason)); } finally { setLoadingMore(false); }
  }
  return <section><PageTitle title="Чаты" />
    <RecruitmentChatList items={(list.data?.items || []).map(item => ({ id: item.id, title: item.terms?.vacancy_title || "Вакансия", subtitle: "Кандидат", state: recruitmentStateNames[item.state] }))}
      loading={list.loading} error={list.error} retry={list.refresh} emptyText="Чаты появятся после отправки приглашений кандидатам" onOpen={onOpen}
      hasMore={list.data?.has_more} loadMore={() => void loadMore()} loadingMore={loadingMore} />
    {moreError && <p className="communication-error" role="alert">{moreError}</p>}
  </section>;
}

/** Cursor polling survives server stream expiry; failed sends reuse the complete UUID-bound payload. */
function RecruitmentThread({
  id,
  account,
  onProfile,
}: {
  id: string;
  account: AccountView;
  onProfile: (candidateId: string) => void;
}) {
  const process = useResource(() => api.getRecruitment("employer", id), [id]);
  const [contacts, setContacts] = useState<ContactsView | null>(null);
  const [command, setCommand] = useState<"reject" | "withdraw" | "interview_propose" | "offer_send" | null>(null);
  useEffect(() => {
    let active = true;
    setContacts(null);
    if (
      process.data &&
      ["accepted", "offer_sent", "hired", "offer_declined"].includes(
        process.data.state,
      )
    )
      api
        .getRecruitmentContacts(id)
        .then((value) => {
          if (active) setContacts(value);
        })
        .catch(() => {
          /* Contact gate failures never expose identity. */
        });
    return () => {
      active = false;
    };
  }, [id, process.data?.state]);
  useEffect(() => {
    let active = true;
    const timer = setInterval(() => {
      api
        .getRecruitment("employer", id)
        .then((value) => {
          if (active) process.setData(value);
        })
        .catch(() => {});
    }, 6000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [id]);
  const p = process.data;
  if (!p)
    return (
      <div className="recruitment-thread">
        <State
          loading={process.loading}
          error={process.error}
          retry={process.refresh}
        />
      </div>
    );
  return (
    <div className="recruitment-thread">
      <header className="participant-header">
        <button
          className="anonymous-avatar"
          aria-label="Посмотреть резюме"
          onClick={() => onProfile(p.candidate_id)}
        >
          <Icon name="candidates" />
        </button>
        <button
          className="participant-name"
          onClick={() => onProfile(p.candidate_id)}
        >
          <strong>{contacts?.display_name || "Кандидат"}</strong>
          <small>{recruitmentStateNames[p.state]}</small>
        </button>
        <details className="process-menu">
          <summary aria-label="Действия с кандидатом">···</summary>
          <div className="panel glass">
            {contacts && (
              <p>
                {contacts.email}
                {contacts.phone && (
                  <>
                    <br />
                    {contacts.phone}
                  </>
                )}
              </p>
            )}
            <p>{salary(p.terms?.salary.from_rub, p.terms?.salary.to_rub)}</p>
            {(
              ["interview_propose", "offer_send", "reject", "withdraw"] as const
            )
              .filter((action) => p.allowed_actions.includes(action))
              .map((action) => (
                <Action key={action} onClick={() => setCommand(action)}>
                  {
                    {
                      interview_propose: "Назначить интервью",
                      offer_send: "Отправить оффер",
                      reject: "Отказать кандидату",
                      withdraw: "Отозвать приглашение",
                    }[action]
                  }
                </Action>
              ))}
            {p.interviews.map((interview) => (
              <p key={interview.id}>
                Интервью:{" "}
                {new Date(interview.starts_at).toLocaleString("ru-RU")} ·{" "}
                {interview.state}
              </p>
            ))}
          </div>
        </details>
      </header>
      <RecruitmentConversation process={p} accountId={account.id} transport={employerConversationTransport} errorText={errorText} emptyText="Начните разговор с кандидатом" messageLabel="Сообщение кандидату" />
      {command && (
        <ProcessCommand
          process={p}
          command={command}
          onClose={() => setCommand(null)}
          onDone={(value) => {
            process.setData(value);
            setCommand(null);
          }}
        />
      )}
    </div>
  );
}

/** Backend-provided allowed actions determine visibility; CAS prevents stale state changes. */
function ProcessCommand({
  process,
  command,
  onClose,
  onDone,
}: {
  process: RecruitmentView;
  command: "reject" | "withdraw" | "interview_propose" | "offer_send";
  onClose: () => void;
  onDone: (value: RecruitmentView) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef(crypto.randomUUID());
  const request = useRef<RecruitmentAction | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const identity = {
      expected_revision: process.revision,
      idempotency_key: key.current,
    };
    const value = (name: string) => String(data.get(name) || "");
    const action: RecruitmentAction =
      command === "interview_propose"
        ? {
            ...identity,
            action: command,
            kind: value("kind") === "technical" ? "technical" : "general",
            starts_at: new Date(value("starts_at")).toISOString(),
            time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            duration_minutes: Number(value("duration")),
            format: value("format") === "onsite" ? "onsite" : "remote",
            location: value("location"),
          }
        : command === "offer_send"
          ? {
              ...identity,
              action: command,
              salary_rub: Number(value("salary")),
              description: value("description"),
              expires_at: new Date(value("expires_at")).toISOString(),
            }
          : { ...identity, action: command, reason: value("reason") };
    setBusy(true);
    setError(null);
    request.current ||= action;
    try {
      onDone(
        await api.actOnRecruitment("employer", process.id, request.current),
      );
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div
      className="communication-modal"
      role="dialog"
      aria-modal="true"
      aria-label="Действие с кандидатом"
    >
      <form
        className="panel glass"
        onSubmit={submit}
        onChange={() => {
          request.current = null;
          key.current = crypto.randomUUID();
        }}
      >
        <h2>
          {
            {
              reject: "Отказ кандидату",
              withdraw: "Отзыв приглашения",
              interview_propose: "Назначить интервью",
              offer_send: "Отправить оффер",
            }[command]
          }
        </h2>
        {command === "reject" || command === "withdraw" ? (
          <Field label="Причина">
            <textarea name="reason" required maxLength={4000} />
          </Field>
        ) : command === "interview_propose" ? (
          <>
            <Field label="Вид интервью">
              <select name="kind">
                <option value="technical">Техническое</option>
                <option value="general">Общее</option>
              </select>
            </Field>
            <Field label="Дата и время">
              <input type="datetime-local" name="starts_at" required />
            </Field>
            <Field label="Длительность, минут">
              <input
                type="number"
                name="duration"
                min={5}
                max={480}
                defaultValue={60}
                required
              />
            </Field>
            <Field label="Формат">
              <select name="format">
                <option value="remote">Онлайн</option>
                <option value="onsite">Очно</option>
              </select>
            </Field>
            <Field label="Ссылка или адрес">
              <input name="location" required maxLength={2000} />
            </Field>
          </>
        ) : (
          <>
            <Field label="Зарплата, ₽">
              <input type="number" name="salary" min={1} step={1} required />
            </Field>
            <Field label="Условия">
              <textarea name="description" required maxLength={4000} />
            </Field>
            <Field label="Действует до">
              <input type="datetime-local" name="expires_at" required />
            </Field>
          </>
        )}
        {error && <p role="alert">{error}</p>}
        <Action type="button" disabled={busy} onClick={onClose}>
          Отмена
        </Action>
        <Action variant="primary" disabled={busy} type="submit">
          {busy ? "Сохраняем…" : "Подтвердить"}
        </Action>
      </form>
    </div>
  );
}

/** Global durable assistant overlays the current page and remains independent of route changes. */
export function OrganizationAgent({ onClose }: { onClose: () => void }) {
  const { preferences } = useEmployerPreferences();
  const systemReducedMotion = useReducedMotion();
  const reducedMotion = systemReducedMotion || preferences.reducedMotion;
  const [outgoing, setOutgoing] = useState<AgentsOrganizationMessage | null>(null);
  const [now, setNow] = useState(Date.now());
  const [messages, setMessages] = useState<AgentsOrganizationMessage[]>([]);
  const [questions, setQuestions] = useState<AgentsPendingAnswer[]>([]);
  const [turn, setTurn] = useState<AgentsOrganizationTurn | null>(null);
  const [budget, setBudget] = useState<AgentsOrganizationBudget | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showLoading, setShowLoading] = useState(false);
  const [documentError, setDocumentError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [showBudget, setShowBudget] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);
  const [historyGeneration, setHistoryGeneration] = useState(0);
  const stopHistory = useRef<() => void>(() => {});
  const [showReasoning, setShowReasoning] = useState(false);
  const [reasoningEffort, setReasoningEffort] = useState<
    "off" | "medium" | "max"
  >(preferences.reasoningEffort);
  const [uploadingDocument, setUploadingDocument] = useState(false);
  const [uploadedDocuments, setUploadedDocuments] = useState<
    AgentsDocumentView[]
  >([]);
  const documentInput = useRef<HTMLInputElement>(null);
  const documentUploadInFlight = useRef(false);
  const composerWrap = useRef<HTMLDivElement>(null);
  const [historyBottom, setHistoryBottom] = useState<number>();
  const pending = useRef<{
    message: string;
    idempotency_key: string;
    reasoning_effort: "off" | "medium" | "max";
    document_ids: string[];
  } | null>(null);
  const activeTurn = useRef<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const history = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  useEffect(() => {
    // The overlay owns scrolling, including its side gutters and touch gestures.
    const bodyOverflow = document.body.style.overflow;
    const rootOverflow = document.documentElement.style.overflow;
    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = bodyOverflow;
      document.documentElement.style.overflow = rootOverflow;
    };
  }, []);
  useEffect(() => {
    if (!sending && (!turn || turn.status === "succeeded" || turn.status === "failed")) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [sending, turn?.status]);
  useEffect(() => {
    if (outgoing?.turn_id && messages.some((item) => item.role === "user" && item.turn_id === outgoing.turn_id)) setOutgoing(null);
  }, [messages, outgoing?.turn_id]);

  useEffect(() => {
    if (!loading) {
      setShowLoading(false);
      return;
    }
    const timer = setTimeout(() => setShowLoading(true), 450);
    return () => clearTimeout(timer);
  }, [loading]);
  useLayoutEffect(() => {
    const wrap = composerWrap.current;
    if (!wrap) return;
    const measure = () => {
      const rootUnit =
        Number.parseFloat(
          getComputedStyle(document.documentElement).fontSize,
        ) || 1;
      setHistoryBottom(
        wrap.offsetHeight +
          (Number.parseFloat(getComputedStyle(wrap).bottom) || 0) +
          20 * rootUnit,
      );
    };
    const observer = new ResizeObserver(measure);
    observer.observe(wrap);
    window.addEventListener("resize", measure);
    measure();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);
  useEffect(() => {
    let active = true;
    let cursor = 0;
    let recovered = false;
    let lastUserTurn: string | null = null;
    let timer: ReturnType<typeof setTimeout>;
    const stop = () => { active = false; clearTimeout(timer); };
    stopHistory.current = stop;
    const poll = async () => {
      try {
        const page = await api.getOrganizationMessages(cursor, 100);
        if (!active) return;
        setMessages((current) => {
          const seen = new Set(current.map((item) => item.message_id));
          return [
            ...current,
            ...page.items.filter((item) => !seen.has(item.message_id)),
          ];
        });
        const progress = agentHistoryProgress(page, cursor);
        cursor = progress.cursor;
        if (page.items.length > 0) {
          api
            .getOrganizationBudget()
            .then((value) => {
              if (active) setBudget(value);
            })
            .catch(() => {});
          api
            .getOrganizationPendingAnswers()
            .then((value) => {
              if (active) setQuestions(value.filter((item) => !item.answered));
            })
            .catch(() => {});
        }
        // Recover the durable turn after reopening the overlay or reloading the page.
        const latestUser = [...page.items].reverse().find((item) => item.role === "user" && item.turn_id);
        if (latestUser?.turn_id) lastUserTurn = latestUser.turn_id;
        if (!recovered && !progress.hasMore) {
          recovered = true;
          if (!activeTurn.current) activeTurn.current = lastUserTurn;
        }
        let finished = false;
        if (activeTurn.current) {
          const value = await api.getOrganizationTurn(activeTurn.current);
          if (!active) return;
          setTurn(value);
          if (value.status === "succeeded" || value.status === "failed") {
            activeTurn.current = null;
            finished = true;
          }
        }
        setLoading(false);
        timer = setTimeout(poll, progress.hasMore || finished ? 50 : activeTurn.current ? 1000 : 2500);
      } catch (reason) {
        if (active) {
          setError(errorText(reason));
          setLoading(false);
          timer = setTimeout(poll, 6000);
        }
      }
    };
    api
      .getOrganizationChat()
      .then(() => {
        if (active) void poll();
      })
      .catch((reason) => {
        if (active) {
          setError(errorText(reason));
          setLoading(false);
        }
      });
    api
      .getOrganizationBudget()
      .then((value) => {
        if (active) setBudget(value);
      })
      .catch(() => {});
    api
      .getOrganizationPendingAnswers()
      .then((value) => {
        if (active) setQuestions(value.filter((item) => !item.answered));
      })
      .catch(() => {});
    return stop;
  }, [historyGeneration]);
  useEffect(() => {
    const scroller = history.current;
    if (scroller && followLatest.current) scroller.scrollTo({ top: scroller.scrollHeight, behavior: reducedMotion ? "instant" : "smooth" });
  }, [messages.length, outgoing?.message_id, turn?.status, historyBottom, reducedMotion]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (confirmClear) { event.stopPropagation(); if (!clearing) setConfirmClear(false); }
        else onClose();
      }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose, confirmClear, clearing]);
  async function clearHistory() {
    if (clearing) return;
    setClearing(true);
    setClearError(null);
    try {
      const context = await api.getOrganizationContext();
      await api.clearOrganizationMessages({ expected_context_revision: context.revision });
      stopHistory.current();
      activeTurn.current = null;
      pending.current = null;
      followLatest.current = true;
      setMessages([]);
      setQuestions([]);
      setOutgoing(null);
      setTurn(null);
      setError(null);
      setLoading(true);
      setConfirmClear(false);
      setHistoryGeneration(value => value + 1);
    } catch (reason) {
      const code = reason instanceof ApiError && reason.body && typeof reason.body === "object" && "detail" in reason.body ? reason.body.detail : "";
      setClearError(code === "organization_chat_busy"
        ? "Агент ещё работает или ждёт завершения операции. Завершите задания и попробуйте снова."
        : code === "context_revision_conflict" ? "Переписка изменилась. Попробуйте очистить её ещё раз." : errorText(reason));
    } finally { setClearing(false); }
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    if (
      (!pending.current && !text.trim() && !uploadedDocuments.length) ||
      sending ||
      uploadingDocument ||
      activeTurn.current
    )
      return;
    const body = pending.current || {
      message: [
        text.trim() || "Используй прикреплённый документ.",
        ...uploadedDocuments.map(
          (document) =>
            `Прикреплённый документ: ${document.name} (ID: ${document.document_id})`,
        ),
      ].join("\n\n"),
      idempotency_key: crypto.randomUUID(),
      reasoning_effort: reasoningEffort,
      document_ids: uploadedDocuments.map((document) => document.document_id),
    };
    if (body.message.length > 8000) {
      setError(
        "Сообщение вместе с названиями вложений должно быть короче 8000 символов.",
      );
      return;
    }
    setSending(true);
    followLatest.current = true;
    setError(null);
    setTurn(null);
    setNow(Date.now());
    setOutgoing({ message_id: `local-${body.idempotency_key}`, chat_id: "", role: "user", content: body.message,
      sequence: Number.MAX_SAFE_INTEGER, created_at: new Date().toISOString() });
    setText("");
    pending.current = body;
    try {
      const value = await api.sendOrganizationMessage({
        message: body.message,
        idempotency_key: body.idempotency_key,
        reasoning_effort: body.reasoning_effort,
      });
      setTurn(value);
      setOutgoing((item) => item ? { ...item, turn_id: value.turn_id } : null);
      activeTurn.current =
        value.status === "failed" || value.status === "succeeded"
          ? null
          : value.turn_id;
      setText("");
      setUploadedDocuments((current) =>
        current.filter(
          (document) => !body.document_ids.includes(document.document_id),
        ),
      );
      pending.current = null;
    } catch (reason) {
      setOutgoing(null);
      setText(body.message);
      setError(errorText(reason));
    } finally {
      setSending(false);
    }
  }
  async function uploadDocument(file?: File) {
    if (!file) return;
    if (documentInput.current) documentInput.current.value = "";
    if (documentUploadInFlight.current || sending) return;
    setError(null);
    if (uploadedDocuments.length >= 3) {
      setDocumentError(
        "Можно прикрепить до трёх файлов. Уберите одно вложение, чтобы добавить другое.",
      );
      return;
    }
    if (
      !file.size ||
      file.size > 5 * 1024 * 1024 ||
      !/\.(pdf|txt|md)$/i.test(file.name)
    ) {
      setDocumentError(!file.size ? "Файл пустой. Выберите документ с содержимым." : "Поддерживаются PDF, TXT и MD до 5 МБ.");
      return;
    }
    documentUploadInFlight.current = true;
    setUploadingDocument(true);
    setDocumentError(null);
    try {
      const document = await api.uploadOrganizationDocument({
        name: file.name,
        content_base64: await fileBase64(file),
        idempotency_key: crypto.randomUUID(),
      });
      setUploadedDocuments((current) => [
        ...current.filter((item) => item.document_id !== document.document_id),
        document,
      ]);
      pending.current = null;
    } catch (reason) {
      setDocumentError(documentErrorText(reason));
    } finally {
      documentUploadInFlight.current = false;
      setUploadingDocument(false);
      if (documentInput.current) documentInput.current.value = "";
    }
  }
  const visible = groupAgentMessages(messages);
  if (outgoing && !messages.some((item) => item.role === "user" && item.turn_id && item.turn_id === outgoing.turn_id)) visible.push(outgoing);
  const thinking = sending || (!!turn && turn.status !== "succeeded" && turn.status !== "failed");
  const currentId = turn?.turn_id || outgoing?.message_id;
  if (currentId && (thinking || turn?.status === "failed") && !visible.some((row) => "messages" in row && row.id === currentId)) {
    visible.push({ id: currentId, messages: [], startedAt: turn?.created_at || outgoing?.created_at || null });
  }
  const percentage = budget ? budgetRemainingPercent(budget) : null;
  return (
    <motion.section
      className={`organization-overlay${reducedMotion ? " reduce-motion" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-label="AI-агент"
      initial={{ opacity: 0, backdropFilter: "blur(0rem)" }}
      animate={{ opacity: 1, backdropFilter: "blur(10rem)" }}
      exit={{ opacity: 0, backdropFilter: "blur(0rem)" }}
      transition={{ duration: reducedMotion ? 0 : 0.25 }}
    >
      <div
        ref={history}
        className="organization-history"
        aria-live="polite"
        onWheel={(event) => { if (event.deltaY < 0) followLatest.current = false; }}
        onTouchMove={() => { followLatest.current = false; }}
        onScroll={() => {
          const scroller = history.current;
          if (scroller && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80) followLatest.current = true;
        }}
        style={
          historyBottom == null
            ? undefined
            : { paddingBottom: historyBottom }
        }
      >
        <div className="organization-history-content">
        {loading && showLoading && <State loading />}
        <AnimatePresence initial={false}>
          {visible.map((row) => {
            const group = "messages" in row;
            return <motion.article
              className={`organization-message ${group ? "assistant" : "user"}`}
              key={group ? row.id : row.message_id}
              initial={reducedMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reducedMotion ? 0 : 0.28 }}>
              {group && <motion.div className="agent-avatar-wrap" initial={reducedMotion ? false : { opacity: 0, scale: 0.92 }}
                animate={{ opacity: 1, scale: 1 }} transition={{ duration: reducedMotion ? 0 : 0.28, ease: [0.22, 1, 0.36, 1] }}><AssistantAvatar /></motion.div>}
              <div className="organization-message-body">
                {group ? <AgentReply group={row} active={thinking && row.id === currentId} now={now}
                  reducedMotion={!!reducedMotion} failed={row.id === turn?.turn_id && turn.status === "failed"} /> : <>
                  <p>{row.content}</p>
                  {row.attachments?.map((file) => <button className="message-file" key={file.document_id}
                    onClick={async () => {
                      try { const value = await api.downloadOrganizationDocument(file.document_id); saveFile(value.document.name, value.content_base64); }
                      catch (reason) { setError(errorText(reason)); }
                    }}>{file.name} ↓</button>)}
                  <CopyMessage text={row.content || ""} />
                </>}
              </div>
            </motion.article>;
          })}
        </AnimatePresence>
        {questions.length > 0 && (
          <div className="agent-questions agent-pending-questions panel glass">
            <h2>Вопросы от агента</h2>
            {questions.map((question) => (
              <form
                key={question.answer_id}
                onSubmit={async (event) => {
                  event.preventDefault();
                  const answer = String(
                    new FormData(event.currentTarget).get("answer") || "",
                  );
                  try {
                    await api.answerOrganizationQuestion(question.answer_id, {
                      answer,
                    });
                    setQuestions((current) =>
                      current.filter(
                        (item) => item.answer_id !== question.answer_id,
                      ),
                    );
                  } catch (reason) {
                    setError(errorText(reason));
                  }
                }}
              >
                <Field label={question.prompt}>
                  <input name="answer" required />
                </Field>
                <Action type="submit">Ответить</Action>
              </form>
            ))}
          </div>
        )}
        <div ref={end} />
        </div>
      </div>
      {!loading && !visible.length && (
        <div className="assistant-welcome">
          <AssistantAvatar />
          <h2>Чем могу помочь?</h2>
          <p>
            Расскажите о задаче: я помогу с вакансиями и работой с кандидатами.
          </p>
        </div>
      )}
      <motion.div
        ref={composerWrap}
        className="communication-composer-wrap organization-composer"
        initial={{ opacity: 0, y: "100vh" }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: "100vh" }}
        transition={{ duration: reducedMotion ? 0 : 0.35, ease: [0.22, 1, 0.36, 1] }}
      >
        <AnimatePresence initial={false}>
          {!!uploadedDocuments.length && (
            <motion.div
              className="agent-attached-documents"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
            >
              {uploadedDocuments.map((document) => (
                <div
                  className="agent-attached-document"
                  key={document.document_id}
                >
                  <Icon name="attached-file" className="agent-attached-document-icon" />
                  <span title={document.name}>{document.name}</span>
                  <button
                    type="button"
                    aria-label={`Убрать вложение ${document.name}`}
                    disabled={sending}
                    onClick={() => {
                      setDocumentError(null);
                      setError(null);
                      setUploadedDocuments((current) =>
                        current.filter(
                          (item) => item.document_id !== document.document_id,
                        ),
                      );
                      pending.current = null;
                    }}
                  >
                    <Icon name="close" />
                  </button>
                </div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>
        {documentError && (
          <p className="communication-error" role="alert">{documentError}</p>
        )}
        {error && (
          <p className="communication-error" role="alert">
            {error}
          </p>
        )}
        <ChatComposer text={text} onTextChange={value => { setText(value); pending.current = null; }} onSubmit={send}
          messageLabel="Сообщение AI-агенту" sendLabel="Отправить сообщение агенту" maxLength={8000}
          autoFocus disabled={sending} submitWithEnter={preferences.submitWithEnter}
          canSubmit={!sending && !uploadingDocument && !activeTurn.current && (!!text.trim() || !!uploadedDocuments.length)}
          attachLabel="Загрузить PDF, TXT или MD до 5 МБ" attachDisabled={uploadingDocument || sending || uploadedDocuments.length >= 3}
          onAttach={() => documentInput.current?.click()} uploading={uploadingDocument}>
          <input ref={documentInput} type="file" accept=".pdf,.txt,.md" hidden onChange={event => void uploadDocument(event.target.files?.[0])} />
        </ChatComposer>
        <p className="communication-attribution">
          <img
            className="droidje-ai-logo"
            src={asset("droidje-ai-logo.svg")}
            alt=""
          />
          Работает на <span>Droidje AI</span>
        </p>
      </motion.div>
      <motion.aside
        className="agent-tools glass"
        initial={{ opacity: 0, y: "190rem", scale: 0.65 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: "190rem", scale: 0.65 }}
        transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
      >
        <button
          type="button"
          title="Очистить историю AI"
          aria-label="Очистить историю AI"
          disabled={loading || clearing}
          onClick={() => { setClearError(null); setConfirmClear(true); }}
        >
          <Icon name="clear-conversation" />
        </button>
        <button
          type="button"
          aria-label="Остаток бюджета AI"
          onMouseEnter={() => setShowBudget(true)}
          onMouseLeave={() => setShowBudget(false)}
          onFocus={() => setShowBudget(true)}
          onBlur={() => setShowBudget(false)}
          onClick={() => setShowBudget((value) => !value)}
        >
          <Icon name="usage-stats" />
        </button>
        <div
          className="agent-reasoning-control"
          onMouseEnter={() => setShowReasoning(true)}
          onMouseLeave={() => setShowReasoning(false)}
          onFocus={() => setShowReasoning(true)}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget))
              setShowReasoning(false);
          }}
        >
          <button
            type="button"
            aria-label="Уровень размышлений AI"
            aria-expanded={showReasoning}
            aria-controls="agent-reasoning-options"
            onClick={() => setShowReasoning(true)}
          >
            <Icon name="reasoning" />
          </button>
          <AnimatePresence>
            {showReasoning && (
              <motion.div
                id="agent-reasoning-options"
                className="agent-reasoning glass"
                role="group"
                aria-label="Уровень размышлений"
                initial={{ opacity: 0, x: 8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 8 }}
                transition={{ duration: 0.15 }}
              >
                <span aria-hidden="true">low</span>
                <span className="agent-reasoning-slider">
                  <input
                    type="range"
                    min={0}
                    max={2}
                    step={1}
                    value={{ off: 0, medium: 1, max: 2 }[reasoningEffort]}
                    aria-label="Уровень размышлений AI"
                    aria-valuetext={
                      {
                        off: "Без размышлений",
                        medium: "Средний",
                        max: "Максимальный",
                      }[reasoningEffort]
                    }
                    disabled={sending || !!activeTurn.current}
                    onChange={(event) => {
                      const value = (["off", "medium", "max"] as const)[
                        Number(event.target.value)
                      ];
                      if (!value || value === reasoningEffort) return;
                      setReasoningEffort(value);
                      pending.current = null;
                    }}
                  />
                  <span className="agent-reasoning-track" aria-hidden="true">
                    <motion.span
                      className="agent-reasoning-thumb"
                      initial={false}
                      animate={{
                        x: { off: "0rem", medium: "51.5rem", max: "103rem" }[
                          reasoningEffort
                        ],
                      }}
                      transition={{
                        type: "spring",
                        stiffness: 420,
                        damping: 32,
                        mass: 0.65,
                      }}
                    />
                  </span>
                </span>
                <span aria-hidden="true">max</span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </motion.aside>
      <AnimatePresence>
        {showBudget && (
          <motion.div
            className="agent-budget glass"
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 8 }}
            transition={{ duration: 0.15 }}
          >
            {percentage == null ? (
              "Бюджет недоступен"
            ) : (
              <>
                <strong>{percentage}%</strong>
                <span
                  className="agent-budget-track"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={percentage}
                  aria-label="Осталось бюджета AI"
                >
                  <span style={{ width: `${percentage}%` }} />
                </span>
              </>
            )}
          </motion.div>
        )}
      </AnimatePresence>
      {confirmClear && <div className="communication-modal" onClick={() => { if (!clearing) setConfirmClear(false); }}>
        <section className="agent-clear-dialog glass" role="dialog" aria-modal="true" aria-label="Очистить историю AI"
          onClick={event => event.stopPropagation()}>
          <h2>Очистить переписку?</h2>
          <p>Сообщения исчезнут. Документы, память ассистента, бюджет и результаты работы сохранятся.</p>
          {clearError && <p role="alert">{clearError}</p>}
          <div className="actions">
            <Action disabled={clearing} onClick={() => setConfirmClear(false)}>Отмена</Action>
            <Action variant="primary" disabled={clearing} onClick={() => void clearHistory()}>{clearing ? "Очищаем…" : "Очистить"}</Action>
          </div>
        </section>
      </div>}
      <Action
        className="agent-close circle glass glass-surface"
        aria-label="Закрыть AI-агента"
        onClick={onClose}
      >
        <Icon name="plus" />
      </Action>
    </motion.section>
  );
}

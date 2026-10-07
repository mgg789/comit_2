import { useEffect, useRef, useState, type FormEvent } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type {
  AccountView,
  AgentsOrganizationBudget,
  AgentsOrganizationMessage,
  AgentsOrganizationTurn,
  AgentsPendingAnswer,
  ChatFile,
  ChatMessage,
  ContactsView,
  RecruitmentAction,
  RecruitmentView,
  SendChatMessage,
} from "@comit/shared";
import { api, asset, errorText, salary, useResource } from "./runtime";
import { Action, Field, Icon, State } from "./ui";
import "./communications.css";

const stateNames: Record<RecruitmentView["state"], string> = {
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

/** Invitation conversations remain participant scoped; names/contacts unlock only after acceptance. */
export function RecruitmentChat({
  account,
  initialProcessId,
  onProfile,
}: {
  account: AccountView;
  initialProcessId?: string;
  onProfile: (candidateId: string) => void;
}) {
  const list = useResource(() => api.getRecruitmentList("employer", 100));
  const [selected, setSelected] = useState(initialProcessId || "");
  const [listError, setListError] = useState<string | null>(null);
  useEffect(() => {
    if (initialProcessId) setSelected(initialProcessId);
  }, [initialProcessId]);
  const id = selected || list.data?.items[0]?.id;
  if (!id)
    return (
      <section className="recruitment-empty">
        <h1>Чаты</h1>
        <State
          loading={list.loading}
          error={list.error}
          empty="Чаты появятся после отправки приглашений кандидатам"
          retry={list.refresh}
        />
      </section>
    );
  return (
    <section className="recruitment-screen">
      <div className="chat-back">
        <Action
          className="circle"
          onClick={() => {
            window.location.hash = `/vacancy/${list.data?.items.find((item) => item.id === id)?.draft_id || ""}`;
          }}
          aria-label="К вакансии"
        >
          <Icon name="back" />
        </Action>
        <h1>К вакансии</h1>
      </div>
      {list.data && list.data.items.length > 1 && (
        <select
          className="chat-picker glass"
          aria-label="Выберите разговор"
          value={id}
          onChange={(event) => setSelected(event.target.value)}
        >
          {list.data.items.map((item, index) => (
            <option value={item.id} key={item.id}>
              {item.terms?.vacancy_title || "Вакансия"} · Кандидат {index + 1}
            </option>
          ))}
        </select>
      )}
      {list.data?.has_more && (
        <Action
          className="chat-more"
          onClick={async () => {
            try {
              const page = await api.getRecruitmentList(
                "employer",
                100,
                list.data?.next_cursor || undefined,
                list.data?.items.length || 0,
              );
              list.setData((current) =>
                current
                  ? { ...page, items: [...current.items, ...page.items] }
                  : page,
              );
            } catch (error) {
              setListError(errorText(error));
            }
          }}
        >
          Другие разговоры
        </Action>
      )}
      {listError && (
        <p className="communication-error" role="alert">
          {listError}
        </p>
      )}
      <RecruitmentThread
        key={id}
        id={id}
        account={account}
        onProfile={onProfile}
      />
    </section>
  );
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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<ChatFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [command, setCommand] = useState<
    "reject" | "withdraw" | "interview_propose" | "offer_send" | null
  >(null);
  const retry = useRef<SendChatMessage | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!process.data?.chat_available) {
      setHistoryLoading(false);
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    const poll = async () => {
      try {
        const page = await api.getChatMessages("employer", id, cursor, 100);
        if (!active) return;
        setMessages((current) => {
          const seen = new Set(current.map((item) => item.id));
          return [
            ...current,
            ...page.messages.filter((item) => !seen.has(item.id)),
          ].sort((a, b) => a.sequence - b.sequence);
        });
        cursor = page.next_sequence;
        setHistoryLoading(false);
        timer = setTimeout(poll, page.messages.length === 100 ? 50 : 2500);
      } catch (reason) {
        if (active) {
          setError(errorText(reason));
          setHistoryLoading(false);
          timer = setTimeout(poll, 6000);
        }
      }
    };
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [id, process.data?.chat_available]);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);
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
  async function send(event: FormEvent) {
    event.preventDefault();
    if (sending || uploading || (!text.trim() && !files.length)) return;
    setSending(true);
    setError(null);
    const body = retry.current || {
      idempotency_key: crypto.randomUUID(),
      text: text.trim(),
      files,
    };
    retry.current = body;
    try {
      const message = await api.sendChatMessage("employer", id, body);
      setMessages((current) =>
        current.some((item) => item.id === message.id)
          ? current
          : [...current, message].sort((a, b) => a.sequence - b.sequence),
      );
      retry.current = null;
      setText("");
      setFiles([]);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setSending(false);
    }
  }
  async function upload(file?: File) {
    if (!file) return;
    if (!file.size || file.size > 8388608 || files.length >= 8) {
      setError("Файл должен быть до 8 МБ. В сообщении — до 8 файлов.");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const uploaded = await api.uploadChatFile("employer", id, {
        idempotency_key: crypto.randomUUID(),
        name: file.name,
        media_type: file.type || "application/octet-stream",
        data_base64: await fileBase64(file),
      });
      setFiles((current) => [...current, uploaded]);
      retry.current = null;
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  }
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
          <small>{stateNames[p.state]}</small>
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
      <div className="recruitment-messages" aria-live="polite">
        {historyLoading && <State loading />}
        {!p.chat_available && (
          <State empty="Чат недоступен для этого приглашения" />
        )}
        {p.chat_available && !historyLoading && !messages.length && (
          <p className="conversation-empty">Начните разговор с кандидатом</p>
        )}
        <AnimatePresence initial={false}>
          {messages.map((message) => (
            <motion.article
              key={message.id}
              className={`message-bubble ${message.sender_id === account.id ? "outgoing" : "incoming"}`}
              initial={{ opacity: 0, y: 12, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ duration: 0.18 }}
            >
              <p>{message.text}</p>
              {message.files.map((file) => (
                <button
                  className="message-file"
                  key={file.id}
                  onClick={async () => {
                    try {
                      const value = await api.downloadChatFile(
                        "employer",
                        id,
                        file.id,
                      );
                      saveFile(value.name, value.data_base64);
                    } catch (reason) {
                      setError(errorText(reason));
                    }
                  }}
                >
                  {file.name} · {(file.size / 1024).toFixed(0)} КБ ↓
                </button>
              ))}
              <time dateTime={message.created_at}>
                {new Date(message.created_at).toLocaleTimeString("ru-RU", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </time>
            </motion.article>
          ))}
        </AnimatePresence>
        <div ref={end} />
      </div>
      <div className="communication-composer-wrap">
        {error && (
          <p className="communication-error" role="alert">
            {error}
          </p>
        )}
        {files.length > 0 && (
          <div className="pending-files">
            {files.map((file) => (
              <button
                key={file.id}
                onClick={() => {
                  setFiles((current) =>
                    current.filter((item) => item.id !== file.id),
                  );
                  retry.current = null;
                }}
              >
                {file.name} ×
              </button>
            ))}
          </div>
        )}
        <form className="communication-composer glass" onSubmit={send}>
          <input
            ref={input}
            type="file"
            hidden
            onChange={(event) => void upload(event.target.files?.[0])}
          />
          <button
            className="attach-button"
            type="button"
            aria-label="Прикрепить файл до 8 МБ"
            disabled={!p.chat_available || uploading || sending}
            onClick={() => input.current?.click()}
          >
            {uploading ? "…" : "+"}
          </button>
          <input
            aria-label="Сообщение кандидату"
            placeholder="Отправьте сообщение"
            value={text}
            maxLength={16000}
            disabled={!p.chat_available || sending}
            onChange={(event) => {
              setText(event.target.value);
              retry.current = null;
            }}
          />
          <Action
            className="circle send-button"
            type="submit"
            disabled={
              !p.chat_available ||
              sending ||
              uploading ||
              (!text.trim() && !files.length)
            }
            aria-label="Отправить сообщение"
          >
            {sending ? <span className="spinner" /> : <Icon name="arrow" />}
          </Action>
        </form>
        <p className="communication-attribution">
          <Icon name="logo" />
          Защищено с <span>QGramm</span>
        </p>
      </div>
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
  const [messages, setMessages] = useState<AgentsOrganizationMessage[]>([]);
  const [questions, setQuestions] = useState<AgentsPendingAnswer[]>([]);
  const [turn, setTurn] = useState<AgentsOrganizationTurn | null>(null);
  const [budget, setBudget] = useState<AgentsOrganizationBudget | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [showBudget, setShowBudget] = useState(false);
  const [showQuestions, setShowQuestions] = useState(false);
  const [uploadingDocument, setUploadingDocument] = useState(false);
  const documentInput = useRef<HTMLInputElement>(null);
  const pending = useRef<{ message: string; idempotency_key: string } | null>(
    null,
  );
  const activeTurn = useRef<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout>;
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
        cursor = page.next_sequence ?? cursor;
        if (activeTurn.current) {
          const value = await api.getOrganizationTurn(activeTurn.current);
          if (!active) return;
          setTurn(value);
          if (value.status === "succeeded" || value.status === "failed")
            activeTurn.current = null;
        }
        setLoading(false);
        timer = setTimeout(poll, page.items.length === 100 ? 50 : 2500);
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
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);
  async function send(event: FormEvent) {
    event.preventDefault();
    if (!text.trim() || sending || uploadingDocument || activeTurn.current)
      return;
    setSending(true);
    setError(null);
    const body = pending.current || {
      message: text.trim(),
      idempotency_key: crypto.randomUUID(),
    };
    pending.current = body;
    try {
      const value = await api.sendOrganizationMessage(body);
      setTurn(value);
      activeTurn.current =
        value.status === "failed" || value.status === "succeeded"
          ? null
          : value.turn_id;
      setText("");
      pending.current = null;
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setSending(false);
    }
  }
  async function uploadDocument(file?: File) {
    if (!file) return;
    if (
      !file.size ||
      file.size > 5 * 1024 * 1024 ||
      !/\.(pdf|txt|md)$/i.test(file.name)
    ) {
      setError("Поддерживаются PDF, TXT и MD до 5 МБ.");
      return;
    }
    setUploadingDocument(true);
    setError(null);
    try {
      const document = await api.uploadOrganizationDocument({
        name: file.name,
        content_base64: await fileBase64(file),
        idempotency_key: crypto.randomUUID(),
      });
      setText(
        (current) =>
          `${current}${current ? "\n" : ""}Используй загруженный документ «${document.name}».`,
      );
      pending.current = null;
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setUploadingDocument(false);
      if (documentInput.current) documentInput.current.value = "";
    }
  }
  const visible = messages.filter(
    (message) =>
      (message.role === "assistant" || message.role === "user") &&
      (message.content || message.attachments?.length),
  );
  const percentage =
    budget && Number(budget.limit_micro_usd) > 0
      ? Math.min(
          100,
          Math.round(
            (Number(budget.spent_micro_usd) / Number(budget.limit_micro_usd)) *
              100,
          ),
        )
      : null;
  return (
    <motion.section
      className="organization-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="AI-агент"
      initial={{ opacity: 0, backdropFilter: "blur(0rem)" }}
      animate={{ opacity: 1, backdropFilter: "blur(10rem)" }}
      exit={{ opacity: 0, backdropFilter: "blur(0rem)" }}
      transition={{ duration: 0.25 }}
    >
      <div className="organization-history" aria-live="polite">
        {loading && <State loading />}
        {!loading && !visible.length && (
          <div className="assistant-welcome">
            <AssistantAvatar />
            <h2>Чем могу помочь?</h2>
            <p>
              Расскажите о задаче: я помогу с вакансиями и работой с
              кандидатами.
            </p>
          </div>
        )}
        <AnimatePresence initial={false}>
          {visible.map((message) => (
            <motion.article
              className={`organization-message ${message.role}`}
              key={message.message_id}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2 }}
            >
              {message.role === "assistant" && <AssistantAvatar />}
              <div className="organization-message-body">
                <p>{message.content}</p>
                {message.attachments?.map((file) => (
                  <button
                    className="message-file"
                    key={file.document_id}
                    onClick={async () => {
                      try {
                        const value = await api.downloadOrganizationDocument(
                          file.document_id,
                        );
                        saveFile(value.document.name, value.content_base64);
                      } catch (reason) {
                        setError(errorText(reason));
                      }
                    }}
                  >
                    {file.name} ↓
                  </button>
                ))}
                <button
                  className="copy-message"
                  aria-label="Скопировать сообщение"
                  onClick={() =>
                    navigator.clipboard
                      .writeText(message.content || "")
                      .catch(() => setError("Не удалось скопировать сообщение"))
                  }
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <rect x="8" y="8" width="12" height="13" rx="2" />
                    <path d="M15 5H5v11" />
                  </svg>
                </button>
              </div>
            </motion.article>
          ))}
        </AnimatePresence>
        {turn && (
          <div className="agent-turn-state" role="status">
            {
              {
                queued: "Сообщение в очереди",
                processing: "Думаю…",
                retrying: "Повторяю запрос к AI…",
                succeeded: "",
                failed:
                  "AI не смог выполнить запрос. Сообщение сохранено; повторите запрос.",
              }[turn.status || "queued"]
            }
          </div>
        )}
        <div ref={end} />
      </div>
      <div className="communication-composer-wrap organization-composer">
        {error && (
          <p className="communication-error" role="alert">
            {error}
          </p>
        )}
        <form className="communication-composer glass" onSubmit={send}>
          <input
            ref={documentInput}
            type="file"
            accept=".pdf,.txt,.md"
            hidden
            onChange={(event) => void uploadDocument(event.target.files?.[0])}
          />
          <button
            type="button"
            className="attach-button"
            aria-label="Загрузить PDF, TXT или MD до 5 МБ"
            disabled={uploadingDocument || sending}
            onClick={() => documentInput.current?.click()}
          >
            {uploadingDocument ? "…" : "+"}
          </button>
          <input
            autoFocus
            aria-label="Сообщение AI-агенту"
            placeholder="Отправьте сообщение"
            value={text}
            maxLength={16000}
            disabled={sending}
            onChange={(event) => {
              setText(event.target.value);
              pending.current = null;
            }}
          />
          <Action
            className="circle send-button"
            type="submit"
            aria-label="Отправить сообщение агенту"
            disabled={
              sending ||
              uploadingDocument ||
              !!activeTurn.current ||
              !text.trim()
            }
          >
            {sending ? <span className="spinner" /> : <Icon name="arrow" />}
          </Action>
        </form>
        <p className="communication-attribution">
          <Icon name="logo" />
          Работает на Droidje AI
        </p>
      </div>
      <aside className="agent-tools glass">
        <button
          type="button"
          title="Очистка истории пока недоступна"
          aria-label="Очистка истории недоступна"
          disabled
        >
          <img
            src={asset("cb79bcac8a5b1ef4846052c9915814f7ef84aea8.svg")}
            alt=""
          />
        </button>
        <button
          type="button"
          aria-label="Расход бюджета AI"
          onMouseEnter={() => setShowBudget(true)}
          onMouseLeave={() => setShowBudget(false)}
          onFocus={() => setShowBudget(true)}
          onBlur={() => setShowBudget(false)}
          onClick={() => setShowBudget((value) => !value)}
        >
          <img
            src={asset("85f60c52ed44e925a0abfc46008446040627dd6a.svg")}
            alt=""
          />
        </button>
        <button
          type="button"
          aria-label="Вопросы от агента"
          onClick={() => {
            setShowQuestions((value) => !value);
            api
              .getOrganizationPendingAnswers()
              .then((value) =>
                setQuestions(value.filter((item) => !item.answered)),
              )
              .catch((reason) => setError(errorText(reason)));
          }}
        >
          <img
            src={asset("36de0441b18c0a44358f7a80a61cac89e2b38031.svg")}
            alt=""
          />
          {questions.length > 0 && (
            <span className="agent-question-count">{questions.length}</span>
          )}
        </button>
      </aside>
      {showBudget && (
        <div className="agent-budget glass">
          {percentage == null ? (
            "Бюджет недоступен"
          ) : (
            <>
              <strong>{percentage}%</strong>
              <meter
                min={0}
                max={100}
                value={percentage}
                aria-label="Использовано бюджета AI"
              />
            </>
          )}
        </div>
      )}
      {showQuestions && (
        <div className="agent-questions panel glass">
          <h2>Вопросы от агента</h2>
          {!questions.length && <p>Нет вопросов, ожидающих ответа</p>}
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
      <Action
        className="agent-close circle glass"
        aria-label="Закрыть AI-агента"
        onClick={onClose}
      >
        ×
      </Action>
    </motion.section>
  );
}

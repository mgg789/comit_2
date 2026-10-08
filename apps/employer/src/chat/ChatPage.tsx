import type { ChatFile, ChatMessage, RecruitmentView } from "@comit/shared";
import { ApiError } from "@comit/api-client";
import { Avatar, Icon, Spinner, StateMessage, cx } from "@comit/ui-kit";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useParams } from "react-router";
import { api, describeError, errorDetail, isUnauthorized, newIdempotencyKey } from "../api";
import { useSession } from "../auth/session";
import { PageHeader } from "../layout/PageHeader";
import { useCandidateNames } from "../lib/use-candidate-names";
import { useResource } from "../lib/use-resource";
import { RECRUITMENT_STATE_LABEL, TERMINAL_STATES } from "../recruitment/labels";

/** Upload limit enforced by api-core (8 MiB of decoded bytes, docs/chat.md). */
const MAX_FILE_BYTES = 8 * 1024 * 1024;
const HISTORY_PAGE = 50;
/** Delay before reopening the event stream after a transport failure. */
const RECONNECT_MS = 2000;

function toBase64(buffer: ArrayBuffer): string {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let index = 0; index < bytes.length; index += 0x8000) binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  return btoa(binary);
}

function mergeMessages(previous: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const byId = new Map(previous.map((message) => [message.id, message]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort((left, right) => left.sequence - right.sequence);
}

/** Private chat of one hiring process (Figma «ЧАТ с соискателем»), served by QGramm through api-core. */
export function ChatPage() {
  const { processId = "" } = useParams();
  const process = useResource(() => api.getRecruitment("employer", processId), [processId]);
  const names = useCandidateNames(process.data ? [process.data.candidate_id] : []);

  if (!process.data) {
    return (
      <>
        <PageHeader title="К вакансии" backTo="/resumes" />
        {process.status === "error" ? (
          <StateMessage kind="error" title="Чат недоступен" description={describeError(process.error)} />
        ) : (
          <StateMessage kind="loading" title="Открываем чат" />
        )}
      </>
    );
  }
  const name = names.get(process.data.candidate_id) ?? "Кандидат";
  return <ChatRoom process={process.data} name={name} />;
}

function ChatRoom({ process, name }: { process: RecruitmentView; name: string }) {
  const { expire } = useSession();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<{ message: string; unconfigured: boolean } | null>(null);
  const cursor = useRef(0);
  const list = useRef<HTMLDivElement>(null);
  const readOnly = TERMINAL_STATES.has(process.state);

  const loadHistory = useCallback(async () => {
    cursor.current = 0;
    let collected: ChatMessage[] = [];
    for (;;) {
      const page = await api.getChatMessages("employer", process.id, cursor.current, HISTORY_PAGE);
      collected = mergeMessages(collected, page.messages);
      const advanced = page.next_sequence > cursor.current;
      cursor.current = page.next_sequence;
      if (page.messages.length < HISTORY_PAGE || !advanced) break;
    }
    setMessages(collected);
  }, [process.id]);

  // History first, then a long-lived SSE loop that resumes from the last processed cursor.
  useEffect(() => {
    const abort = new AbortController();
    let stopped = false;
    void (async () => {
      try {
        await loadHistory();
        setStatus("ready");
      } catch (failure) {
        if (isUnauthorized(failure)) expire();
        setError({ message: describeError(failure), unconfigured: errorDetail(failure) === "chat_unavailable" });
        setStatus("error");
        return;
      }
      while (!stopped) {
        try {
          cursor.current = await api.streamChatEvents(
            "employer",
            process.id,
            (event) => {
              if (event.type === "message.created") setMessages((previous) => mergeMessages(previous, [event.message]));
              cursor.current = event.sequence;
            },
            { afterSequence: cursor.current, signal: abort.signal },
          );
        } catch (failure) {
          if (stopped) return;
          if (isUnauthorized(failure)) return expire();
          if (failure instanceof ApiError && failure.status === 410) await loadHistory().catch(() => undefined);
          await new Promise((resolve) => window.setTimeout(resolve, RECONNECT_MS));
        }
      }
    })();
    return () => {
      stopped = true;
      abort.abort();
    };
  }, [process.id, loadHistory, expire]);

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight, behavior: "smooth" });
  }, [messages.length]);

  return (
    <>
      <div className="flex flex-wrap items-center gap-4">
        <PageHeader title="К вакансии" backTo={`/vacancies/${process.draft_id}`} />
        <div className="mb-6 flex min-w-0 flex-1 items-center gap-3 rounded-pill bg-light py-1.5 pr-6 pl-1.5 text-on-light sm:mb-8 lg:max-w-[700px]">
          <Avatar name={name} size={44} shape="circle" />
          <div className="min-w-0">
            <p className="truncate font-display text-base">{name}</p>
            <p className="truncate text-sm text-faint">{RECRUITMENT_STATE_LABEL[process.state]}</p>
          </div>
        </div>
      </div>

      <div className="mx-auto flex max-w-[700px] flex-col gap-4">
        <div ref={list} aria-live="polite" className="flex max-h-[calc(100dvh-380px)] min-h-[240px] flex-col gap-4 overflow-y-auto pr-1">
          {status === "loading" && <StateMessage kind="loading" title="Загружаем переписку" />}
          {status === "error" && (
            <StateMessage kind="error" title={error?.unconfigured ? "Чат не настроен" : "Чат недоступен"} description={error?.message} />
          )}
          {status === "ready" && messages.length === 0 && (
            <StateMessage kind="empty" title="Сообщений пока нет" description="Напишите кандидату — контакты откроются после принятия приглашения, а чат доступен уже сейчас." />
          )}
          {messages.map((message) => (
            <Bubble key={message.id} message={message} mine={message.sender_id === process.owner_id} processId={process.id} />
          ))}
        </div>
        {readOnly ? (
          <p className="text-center text-sm text-muted">Процесс завершён — история доступна только для чтения.</p>
        ) : (
          <Composer processId={process.id} onSent={(message) => setMessages((previous) => mergeMessages(previous, [message]))} />
        )}
        <p className="text-center text-xs text-muted">
          Защищено с <span className="text-primary">QGramm</span>
        </p>
      </div>
    </>
  );
}

function Bubble({ message, mine, processId }: { message: ChatMessage; mine: boolean; processId: string }) {
  const time = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(new Date(message.created_at));
  return (
    <div className={cx("flex max-w-[80%] flex-col gap-2 rounded-[15px] px-4 py-3", mine ? "self-end bg-surface-raised text-text" : "self-start bg-light text-on-light")}>
      {message.text && <p className="whitespace-pre-wrap break-words">{message.text}</p>}
      {message.files.map((file) => (
        <AttachmentLink key={file.id} file={file} processId={processId} />
      ))}
      <span className={cx("self-end text-xs", mine ? "text-muted" : "text-faint")}>{time}</span>
    </div>
  );
}

/** Attachments are downloaded as files, never rendered inline (docs/chat.md safety note). */
function AttachmentLink({ file, processId }: { file: ChatFile; processId: string }) {
  const [busy, setBusy] = useState(false);
  async function download() {
    setBusy(true);
    try {
      const result = await api.downloadChatFile("employer", processId, file.id);
      const bytes = Uint8Array.from(atob(result.data_base64), (char) => char.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = file.name;
      link.click();
      URL.revokeObjectURL(url);
    } finally {
      setBusy(false);
    }
  }
  return (
    <button type="button" disabled={busy} onClick={() => void download()} className="cursor-pointer text-left text-sm underline disabled:opacity-60">
      📎 {file.name} · {Math.ceil(file.size / 1024)} КБ
    </button>
  );
}

function Composer({ processId, onSent }: { processId: string; onSent: (message: ChatMessage) => void }) {
  const { expire } = useSession();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<ChatFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Key bound to the current payload: a retry after a network error replays instead of duplicating.
  const pendingKey = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function attach(file: File) {
    if (file.size > MAX_FILE_BYTES) {
      setError("Файл больше 8 МБ.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const uploaded = await api.uploadChatFile("employer", processId, {
        idempotency_key: newIdempotencyKey(),
        name: file.name,
        media_type: file.type || "application/octet-stream",
        data_base64: toBase64(await file.arrayBuffer()),
      });
      setFiles((previous) => [...previous, uploaded]);
      pendingKey.current = null;
    } catch (failure) {
      if (isUnauthorized(failure)) expire();
      setError(`Файл не загружен: ${describeError(failure)}`);
    } finally {
      setBusy(false);
    }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!text.trim() && files.length === 0) return;
    pendingKey.current ??= newIdempotencyKey();
    setBusy(true);
    setError(null);
    try {
      const message = await api.sendChatMessage("employer", processId, { idempotency_key: pendingKey.current, text: text.trim(), files });
      onSent(message);
      setText("");
      setFiles([]);
      pendingKey.current = null;
    } catch (failure) {
      if (isUnauthorized(failure)) expire();
      setError(`Не отправлено: ${describeError(failure)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={send} className="flex flex-col gap-2">
      {files.length > 0 && (
        <ul className="flex flex-wrap gap-2 text-sm">
          {files.map((file) => (
            <li key={file.id} className="rounded-chip bg-surface-raised px-3 py-1">
              📎 {file.name}
            </li>
          ))}
        </ul>
      )}
      <div className="glass flex items-center gap-3 rounded-pill border border-glass-border p-1.5">
        <input
          ref={fileInput}
          type="file"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void attach(file);
            event.target.value = "";
          }}
        />
        <button
          type="button"
          aria-label="Прикрепить файл"
          disabled={busy}
          onClick={() => fileInput.current?.click()}
          className="bg-accent-gradient inline-flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-full text-2xl text-white disabled:opacity-50"
        >
          +
        </button>
        <label className="sr-only" htmlFor="chat-input">
          Сообщение
        </label>
        <input
          id="chat-input"
          value={text}
          maxLength={16000}
          onChange={(event) => {
            setText(event.target.value);
            pendingKey.current = null;
          }}
          placeholder="Отправьте сообщение"
          className="min-w-0 flex-1 bg-transparent text-lg text-text outline-none placeholder:text-text"
        />
        <button
          type="submit"
          aria-label="Отправить"
          disabled={busy}
          className="glass inline-flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-full border border-glass-border text-text hover:bg-white/10 disabled:opacity-50"
        >
          {busy ? <Spinner size={18} /> : <Icon name="arrow-left" size={22} className="rotate-180" />}
        </button>
      </div>
      {error && (
        <p role="alert" className="text-center text-sm text-danger-soft">
          {error}
        </p>
      )}
    </form>
  );
}

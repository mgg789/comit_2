import type { ChatFile, ChatMessage, RecruitmentView } from "@comit/shared";
import { ApiError } from "@comit/api-client";
import { Avatar, Icon, MessageBubble, MessageComposer, StateMessage, qgrammIconUrl } from "@comit/ui-kit";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router";
import { api, describeError, errorDetail, isUnauthorized, newIdempotencyKey } from "../api";
import { useSession } from "../auth/session";
import { PageHeader } from "../layout/PageHeader";
import { candidateLabel } from "../lib/format";
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
  const name = candidateLabel(names.get(process.data.candidate_id) ?? "Кандидат", process.data.candidate_id);
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
      <div className="flex flex-wrap items-center gap-4 lg:gap-x-[3.5rem]">
        <PageHeader title="К вакансии" backTo={`/vacancies/${process.draft_id}`} />
        <div className="mb-6 flex h-14 min-w-0 flex-1 items-center gap-3 rounded-[6.25rem] bg-white pr-6 pl-1.5 text-on-chip lg:mb-[2rem] lg:h-[4.125rem] lg:max-w-[60.1875rem] lg:gap-[0.875rem] lg:pl-[0.375rem]">
          <Avatar name={name} size={54} shape="circle" />
          <div className="min-w-0">
            <p className="truncate font-display text-base leading-tight lg:text-[1.25rem]">{name}</p>
            <p className="truncate text-sm font-semibold text-on-chip/80 lg:text-[1.125rem]">{RECRUITMENT_STATE_LABEL[process.state]}</p>
          </div>
        </div>
      </div>

      <div className="mx-auto flex max-w-[60.1875rem] flex-col gap-6 pb-48 lg:pb-[14rem]">
        <div ref={list} aria-live="polite" className="flex min-h-[15rem] flex-col gap-4 lg:gap-[2rem]">
          {status === "loading" && <StateMessage kind="loading" title="Загружаем переписку" />}
          {status === "error" && (
            <StateMessage kind="error" title={error?.unconfigured ? "Чат не настроен" : "Чат недоступен"} description={error?.message} />
          )}
          {status === "ready" && messages.length === 0 && (
            <StateMessage kind="empty" title="Сообщений пока нет" description="Напишите кандидату — чат открыт с момента приглашения, контакты откроются после принятия." />
          )}
          {messages.map((message) => (
            <Bubble key={message.id} message={message} mine={message.sender_id === process.owner_id} processId={process.id} />
          ))}
        </div>
      </div>

      <div className="fixed inset-x-0 bottom-6 z-20 flex justify-center px-4 lg:bottom-[3.75rem]">
        <div className="w-full max-w-[44.1875rem]">
          {readOnly ? (
            <p className="glass rounded-pill px-6 py-4 text-center text-sm text-muted">Процесс завершён — история доступна только для чтения.</p>
          ) : (
            <Composer processId={process.id} onSent={(message) => setMessages((previous) => mergeMessages(previous, [message]))} />
          )}
        </div>
      </div>
    </>
  );
}

function Bubble({ message, mine, processId }: { message: ChatMessage; mine: boolean; processId: string }) {
  const time = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" }).format(new Date(message.created_at));
  return (
    <MessageBubble mine={mine} meta={time}>
      {message.text && <p className="break-words whitespace-pre-wrap">{message.text}</p>}
      {message.files.map((file) => (
        <AttachmentLink key={file.id} file={file} processId={processId} />
      ))}
    </MessageBubble>
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
      <Icon name="paperclip" size={14} className="mr-1 align-[-0.125rem]" />
      {file.name} · {Math.ceil(file.size / 1024)} КБ
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

  async function send() {
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
    <div className="flex flex-col gap-2">
      {files.length > 0 && (
        <ul className="flex flex-wrap justify-center gap-2 text-sm">
          {files.map((file) => (
            <li key={file.id} className="glass rounded-chip px-3 py-1">
              <Icon name="paperclip" size={14} className="mr-1 align-[-0.125rem]" />
              {file.name}
            </li>
          ))}
        </ul>
      )}
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
      <MessageComposer
        value={text}
        maxLength={16000}
        onChange={(value) => {
          setText(value);
          pendingKey.current = null;
        }}
        onSubmit={() => void send()}
        canSubmit={text.trim().length > 0 || files.length > 0}
        busy={busy}
        onAttach={() => fileInput.current?.click()}
        footer={
          <>
            <img src={qgrammIconUrl} alt="" className="h-4 w-auto" />
            <span>
              Защищено с <span className="text-link-muted">QGramm</span>
            </span>
          </>
        }
      />
      {error && (
        <p role="alert" className="text-center text-sm text-danger-soft">
          {error}
        </p>
      )}
    </div>
  );
}

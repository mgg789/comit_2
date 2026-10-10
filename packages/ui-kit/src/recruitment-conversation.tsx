import { useLayoutEffect, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { ChatFile, ChatMessage, ChatPage, DownloadChatFile, RecruitmentView, SendChatMessage, UploadChatFile } from "@comit/shared";
import { Action, Icon, State } from "./design";
import "./conversation.css";
import { ChatComposer } from "./chat-composer";
const qgrammMark = new URL("./qgramm-mark.svg", import.meta.url).href;

/** Callbacks bind the caller's authenticated role; this component never selects authority. */
export interface ConversationTransport {
  getMessages(id: string, after: number, limit: number): Promise<ChatPage>;
  sendMessage(id: string, body: SendChatMessage): Promise<ChatMessage>;
  uploadFile(id: string, body: UploadChatFile): Promise<ChatFile>;
  downloadFile(id: string, fileId: string): Promise<DownloadChatFile>;
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

/** Employer's proven thread, composer and attachments shared with the candidate cabinet. */
export function RecruitmentConversation({ process, accountId, transport, errorText, header, emptyText, messageLabel }: {
  process: RecruitmentView; accountId: string; transport: ConversationTransport;
  errorText: (reason: unknown) => string; header?: ReactNode; emptyText: string; messageLabel: string;
}) {
  const id = process.id;
  const reduced = useReducedMotion();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<ChatFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const retry = useRef<SendChatMessage | null>(null);
  const currentId = useRef(id); currentId.current = id;
  const transportRef = useRef(transport); transportRef.current = transport;
  useEffect(() => { setMessages([]); setText(""); setFiles([]); retry.current = null; setError(null); setSending(false); setUploading(false); setHistoryLoading(process.chat_available); }, [id]);
  const input = useRef<HTMLInputElement>(null);
  const history = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLDivElement>(null);
  const [historyHeight, setHistoryHeight] = useState<number | undefined>();
  useLayoutEffect(() => {
    const wrap = composer.current, list = history.current;
    if (!wrap || !list) return;
    const fit = () => {
      const unit = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 1;
      setHistoryHeight(Math.max(120 * unit, Math.floor(wrap.getBoundingClientRect().top - list.getBoundingClientRect().top - 16 * unit)));
    };
    const observer = new ResizeObserver(fit); observer.observe(wrap); fit();
    window.addEventListener("resize", fit);
    return () => { observer.disconnect(); window.removeEventListener("resize", fit); };
  }, [id]);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!process.chat_available) {
      setHistoryLoading(false);
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    const poll = async () => {
      try {
        const page = await transportRef.current.getMessages(id, cursor, 100);
        if (!active) return;
        setMessages((current) => {
          const seen = new Set(current.map((item) => item.id));
          return [
            ...current,
            ...page.messages.filter((item) => !seen.has(item.id)),
          ].sort((a, b) => a.sequence - b.sequence);
        });
        const previousCursor = cursor;
        cursor = Math.max(cursor, page.next_sequence);
        setHistoryLoading(false);
        timer = setTimeout(poll, page.messages.length === 100 && cursor > previousCursor ? 50 : 2500);
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
  }, [id, process.chat_available]);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "end" });
  }, [messages.length]);
  async function send(event: FormEvent) {
    event.preventDefault();
    if (!process.chat_writable || sending || uploading || (!text.trim() && !files.length)) return;
    setSending(true);
    setError(null);
    const body = retry.current || {
      idempotency_key: crypto.randomUUID(),
      text: text.trim(),
      files,
    };
    retry.current = body;
    try {
      const message = await transport.sendMessage(id, body);
      if (currentId.current !== id) return;
      setMessages((current) =>
        current.some((item) => item.id === message.id)
          ? current
          : [...current, message].sort((a, b) => a.sequence - b.sequence),
      );
      retry.current = null;
      setText("");
      setFiles([]);
    } catch (reason) {
      if (currentId.current === id) setError(errorText(reason));
    } finally {
      if (currentId.current === id) setSending(false);
    }
  }
  async function upload(file?: File) {
    if (!file || !process.chat_writable || uploading || sending || retry.current) return;
    if (!file.size || file.size > 8388608 || files.length >= 8) {
      setError("Файл должен быть до 8 МБ. В сообщении — до 8 файлов.");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const uploaded = await transport.uploadFile(id, {
        idempotency_key: crypto.randomUUID(),
        name: file.name,
        media_type: file.type || "application/octet-stream",
        data_base64: await fileBase64(file),
      });
      if (currentId.current !== id) return;
      setFiles((current) => [...current, uploaded]);
      retry.current = null;
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      if (currentId.current === id) setUploading(false);
      if (input.current) input.current.value = "";
    }
  }
  return <div className="recruitment-conversation">{header}
      <div ref={history} className="recruitment-messages" style={{ height: historyHeight }} aria-live="polite">
        {historyLoading && <State loading />}
        {!process.chat_available && (
          <State empty="Чат недоступен для этого приглашения" />
        )}
        {process.chat_available && !historyLoading && !messages.length && (
          <p className="conversation-empty">{emptyText}</p>
        )}
        <AnimatePresence initial={false}>
          {process.chat_available && messages.map((message) => (
            <motion.article
              key={message.id}
              className={`message-bubble ${message.sender_id === accountId ? "outgoing" : "incoming"}`}
              initial={reduced ? false : { opacity: 0, y: 12, scale: 0.98 }}
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
                      const value = await transport.downloadFile(
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
      <div ref={composer} className="communication-composer-wrap">
        {error && (
          <p className="communication-error" role="alert">
            {error}
          </p>
        )}
        {!!retry.current && !sending && <p className="empty-note" role="status">Повторите отправку, чтобы проверить результат предыдущей попытки.</p>}
        {files.length > 0 && (
          <div className="pending-files">
            {files.map((file) => (
              <button
                key={file.id}
                disabled={sending || !!retry.current}
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
        {process.chat_available && !process.chat_writable && <p className="empty-note" role="status">Чат доступен только для чтения. Отправка сообщений и файлов сейчас недоступна.</p>}
        <ChatComposer text={text} onTextChange={value => { setText(value); retry.current = null; }} onSubmit={send}
          messageLabel={messageLabel} maxLength={16000} disabled={!process.chat_writable || sending || !!retry.current}
          placeholder={process.chat_writable ? "Отправьте сообщение" : "Чат доступен только для чтения"}
          canSubmit={process.chat_writable && !sending && !uploading && (!!text.trim() || !!files.length)}
          attachLabel="Прикрепить файл до 8 МБ" attachDisabled={!process.chat_writable || uploading || sending || !!retry.current}
          onAttach={() => input.current?.click()} uploading={uploading} sending={sending}>
          <input ref={input} type="file" hidden onChange={event => void upload(event.target.files?.[0])} />
        </ChatComposer>
        <p className="communication-attribution">
          <img className="qgramm-mark" src={qgrammMark} alt="" />
          Защищено с <a href="https://github.com/mgg789/QGramm" target="_blank" rel="noopener noreferrer">QGramm</a>
        </p>
      </div>
  </div>;
}

/** The same list tiles for both roles; listing chats never mounts a conversation or fetches its messages. */
export function RecruitmentChatList({ items, loading, error, emptyText, retry, onOpen, hasMore, loadMore, loadingMore }: {
  items: readonly { id: string; title: string; subtitle: string; state: string }[];
  loading: boolean; error: string | null; emptyText: string; retry: () => void;
  onOpen: (id: string) => void; hasMore?: boolean; loadMore?: () => void; loadingMore?: boolean;
}) {
  return <div className="recruitment-chat-list">
    {(loading || error || !items.length) && <State loading={loading} error={error} empty={!items.length ? emptyText : undefined} retry={retry} />}
    {items.map(item => <button key={item.id} className="recruitment-chat-tile panel" onClick={() => onOpen(item.id)}>
      <span className="anonymous-avatar"><Icon name="candidates" /></span>
      <span className="chat-tile-label"><strong>{item.title}</strong><small>{item.subtitle}</small></span>
      <span className="chat-tile-state">{item.state}</span><Icon name="arrow" />
    </button>)}
    {hasMore && loadMore && <Action disabled={loadingMore} onClick={loadMore}>{loadingMore ? "Загружаем…" : "Другие разговоры"}</Action>}
  </div>;
}

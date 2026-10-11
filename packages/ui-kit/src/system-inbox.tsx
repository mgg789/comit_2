import { useEffect, useRef, useState, type FormEventHandler } from "react";
import type { ChatUnreadStatus, SystemChatFile, SystemChatFileDownload, SystemChatPage, SystemChatReadStatus, SystemNotification } from "@comit/shared";
import { Action, Icon, State } from "./design";
import { ChatComposer } from "./chat-composer";
import "./conversation.css";
import { systemNotificationPath } from "./system-navigation";

/** Caller binds account authority; sendMessage is present when the Comit chat accepts user text. */
export interface SystemInboxTransport {
  getPage(after: number, limit: number): Promise<SystemChatPage>;
  markRead(sequence: number): Promise<SystemChatReadStatus>;
  /** Write into the conversation with a stable UUID retry key; absent renders a read-only thread. */
  sendMessage?(text: string, files?: SystemChatFile[]): Promise<SystemNotification>;
  uploadFile?(file: File): Promise<SystemChatFile>;
  downloadFile?(fileId: string): Promise<SystemChatFileDownload>;
}

/** Platform events arrive from Comit; the account's own support messages render as outgoing. */
export const isOwnSystemMessage = (item: SystemNotification): boolean => item.type === "support.user_message";

async function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === "string" ? resolve(reader.result.split(",")[1] || "") : reject(new Error("file_read"));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function saveFile(name: string, base64: string): void {
  const bytes = Uint8Array.from(atob(base64), character => character.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Refresh the role-scoped total after reading either conversation type and when the tab returns. */
export function useChatUnread(accountId: string | undefined, fetchUnread: () => Promise<ChatUnreadStatus>) {
  const [status, setStatus] = useState<ChatUnreadStatus | null>(null);
  useEffect(() => {
    setStatus(null);
    if (!accountId) return;
    let active = true, pending = false, requested = false;
    const refresh = async () => {
      if (pending) { requested = true; return; }
      if (document.visibilityState !== "visible") return;
      pending = true;
      try { const value = await fetchUnread(); if (active) setStatus(value); }
      catch { /* Keep the last confirmed count; an outage must not claim messages were read. */ }
      finally { pending = false; if (active && requested) { requested = false; void refresh(); } }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    window.addEventListener("comit:chat-read", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => { active = false; clearInterval(timer); window.removeEventListener("comit:chat-read", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, [accountId, fetchUnread]);
  return status;
}

/** Permanent Comit conversation: platform notices plus support dialogue with optional user messages. */
export function SystemInbox({ transport, errorText, navigate }: { transport: SystemInboxTransport; errorText: (reason: unknown) => string; navigate: (path: string) => void }) {
  const [items, setItems] = useState<SystemNotification[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null);
  const [more, setMore] = useState(false), [visibleSequence, setVisibleSequence] = useState(0);
  // Complaint links fill a draft only; sending still requires the user's action.
  const draft = new URLSearchParams(window.location.hash.split("?")[1] || "").get("draft")?.slice(0, 2000) || "";
  const [text, setText] = useState(draft), [sending, setSending] = useState(false);
  const [files, setFiles] = useState<SystemChatFile[]>([]), [uploading, setUploading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const cursor = useRef(0), read = useRef(0), fetching = useRef(false), history = useRef<HTMLDivElement>(null), input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let active = true;
    cursor.current = 0; read.current = 0; setItems([]); setFiles([]); setLoading(true); setError(null); setVisibleSequence(0);
    const fetchPage = async () => {
      if (fetching.current || document.visibilityState !== "visible") return;
      fetching.current = true;
      try {
        const page = await transport.getPage(cursor.current, 50);
        if (!active) return;
        cursor.current = page.next_sequence;
        read.current = Math.max(read.current, page.read_sequence);
        setItems(prior => [...new Map([...prior, ...page.items].map(item => [item.id, item])).values()]);
        setMore(page.has_more); setError(null);
      } catch (reason) { if (active) setError(errorText(reason)); }
      finally { fetching.current = false; if (active) setLoading(false); }
    };
    void fetchPage();
    const timer = setInterval(() => void fetchPage(), 5000);
    const load = () => void fetchPage();
    window.addEventListener("comit:system-load-more", load);
    document.addEventListener("visibilitychange", load);
    return () => { active = false; clearInterval(timer); window.removeEventListener("comit:system-load-more", load); document.removeEventListener("visibilitychange", load); };
  }, [transport, errorText, attempt]);
  useEffect(() => {
    const observer = new IntersectionObserver(entries => {
      if (document.visibilityState !== "visible") return;
      for (const entry of entries) if (entry.isIntersecting && entry.intersectionRatio >= 0.95) setVisibleSequence(prior => Math.max(prior, Number((entry.target as HTMLElement).dataset.sequence) || 0));
    }, { root: history.current, threshold: 0.95 });
    history.current?.querySelectorAll("[data-sequence]").forEach(element => observer.observe(element));
    return () => observer.disconnect();
  }, [items]);
  // A conversation reads like a chat: follow the newest bubble, including just-sent ones.
  useEffect(() => { history.current?.lastElementChild?.scrollIntoView({ block: "end" }); }, [items.length]);
  useEffect(() => {
    if (visibleSequence <= read.current || document.visibilityState !== "visible") return;
    let active = true;
    void transport.markRead(visibleSequence).then(status => {
      if (!active) return;
      read.current = Math.max(read.current, status.read_sequence);
      window.dispatchEvent(new Event("comit:chat-read"));
    }).catch(reason => { if (active) setError(errorText(reason)); });
    return () => { active = false; };
  }, [transport, visibleSequence]);
  const submit: FormEventHandler<HTMLFormElement> = async event => {
    event.preventDefault();
    const body = text.trim();
    if ((!body && !files.length) || !transport.sendMessage || sending || uploading) return;
    setSending(true);
    try {
      // No cursor advance: the poll refetches from its own cursor and deduplicates by id.
      const item = await transport.sendMessage(body, files);
      setText("");
      setFiles([]);
      setItems(prior => [...new Map([...prior, item].map(entry => [entry.id, entry])).values()]);
      window.dispatchEvent(new Event("comit:chat-read"));
    } catch (reason) { setError(errorText(reason)); }
    finally { setSending(false); }
  };
  const upload = async (file: File | undefined): Promise<void> => {
    if (!file || !transport.uploadFile || uploading || sending || files.length >= 8) return;
    if (!file.size || file.size > 8 * 1024 * 1024) { setError("Файл должен быть до 8 МБ. В сообщении — до 8 файлов."); return; }
    if (!["application/pdf", "text/plain", "text/markdown", "image/png", "image/jpeg", "image/gif", "image/webp"].includes(file.type)
      && !/\.(pdf|txt|md|png|jpe?g|gif|webp)$/iu.test(file.name)) { setError("Поддерживаются PDF, TXT, MD, PNG, JPEG, GIF и WebP."); return; }
    setUploading(true); setError(null);
    try {
      const value = await transport.uploadFile(file);
      setFiles(current => current.some(item => item.id === value.id) ? current : [...current, value]);
    } catch (reason) { setError(errorText(reason)); }
    finally { setUploading(false); if (input.current) input.current.value = ""; }
  };
  const download = async (file: SystemChatFile): Promise<void> => {
    if (!transport.downloadFile) return;
    try { const value = await transport.downloadFile(file.id); saveFile(value.name, value.data_base64); }
    catch (reason) { setError(errorText(reason)); }
  };
  return <div className="recruitment-thread system-inbox">
    <header className="participant-header"><span className="anonymous-avatar system-avatar"><Icon name="logo" /></span><div className="participant-name"><strong>Comit</strong><small>Уведомления и поддержка</small></div></header>
    <div className="chat-history-viewport"><div className="recruitment-messages" ref={history} aria-label="Чат с Comit">
      {(loading || error) && <State loading={loading} error={error} retry={() => setAttempt(value => value + 1)} />}
      {!loading && !error && !items.length && <p className="empty-note">Здесь появятся уведомления платформы. Напишите нам — отвечаем в этом чате.</p>}
      {items.map(item => {
        const path = item.target ? systemNotificationPath(item.target.path) : null;
        return <article className={`message-bubble ${isOwnSystemMessage(item) ? "outgoing" : "incoming"}`} data-sequence={item.sequence} key={item.id}><p>{item.text}</p>{(item.files ?? []).map(file => <button className="message-file" key={file.id} type="button" onClick={() => void download(file)}>{file.name} · {(file.size / 1024).toFixed(0)} КБ ↓</button>)}{path && !isOwnSystemMessage(item) && <Action className="system-message-open" variant="primary" onClick={() => navigate(path)}>Открыть</Action>}<time dateTime={item.created_at}>{new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(new Date(item.created_at))}</time></article>;
      })}
      {more && <Action disabled={loading} onClick={() => window.dispatchEvent(new Event("comit:system-load-more"))}>Другие уведомления</Action>}
      <div className="chat-tail-space" aria-hidden="true" />
    </div></div>
    {transport.sendMessage && <div className="communication-composer-wrap">
      {error && <p className="communication-error" role="alert">{error}</p>}
      {!!files.length && <div className="pending-files">{files.map(file => <button key={file.id} type="button" onClick={() => setFiles(current => current.filter(item => item.id !== file.id))}>{file.name} ×</button>)}</div>}
      <ChatComposer text={text} onTextChange={value => { setText(value); setError(null); }} onSubmit={event => void submit(event)}
        autoFocus={!!draft} messageLabel="Сообщение в поддержку" sendLabel="Отправить в Comit" placeholder="Напишите в поддержку Comit" maxLength={2000}
        canSubmit={!sending && !uploading && (!!text.trim() || !!files.length)} sending={sending} uploading={uploading}
        attachDisabled={!transport.uploadFile || uploading || sending || files.length >= 8} onAttach={() => input.current?.click()}
        attachLabel={transport.uploadFile ? "Прикрепить файл до 8 МБ" : "Вложения в чате поддержки недоступны"}>
        {transport.uploadFile && <input ref={input} type="file" accept=".pdf,.txt,.md,image/png,image/jpeg,image/gif,image/webp" hidden onChange={event => void upload(event.target.files?.[0] as File | undefined)} />}
      </ChatComposer>
    </div>}
  </div>;
}

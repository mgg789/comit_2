import { useEffect, useRef, useState, type FormEventHandler } from "react";
import { ApiError } from "@comit/api-client";
import type { SupportConversation, SupportOperatorView, SystemChatFile, SystemNotification } from "@comit/shared";
import { Action, Field, Icon, State } from "@comit/ui-kit/design";
import { ChatComposer } from "@comit/ui-kit/chat-composer";
import { RecruitmentChatList } from "@comit/ui-kit/recruitment-conversation";
import { api, errorText, useResource, useRoute } from "./runtime";

const roleLabel: Record<SupportConversation["role"], string> = { candidate: "Кандидат", employer: "Работодатель" };
const time = (value: string | null): string => value ? new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "";
async function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => typeof reader.result === "string" ? resolve(reader.result.split(",")[1] || "") : reject(new Error("file_read")); reader.onerror = reject; reader.readAsDataURL(file); });
}
function saveFile(name: string, base64: string): void {
  const bytes = Uint8Array.from(atob(base64), value => value.charCodeAt(0)); const url = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Login gate: the operator email and access key come from the server env, never from this build. */
function LoginScreen({ onLogin }: { onLogin: (operator: SupportOperatorView) => void }) {
  const [email, setEmail] = useState(""), [key, setKey] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const submit: FormEventHandler<HTMLFormElement> = async event => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const session = await api.supportLogin({ email: email.trim(), access_key: key });
      onLogin(session.operator);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  };
  return <section className="support-login">
    <form className="support-login-card glass" onSubmit={submit}>
      <span className="anonymous-avatar system-avatar"><Icon name="logo" /></span>
      <h1>Comit · Поддержка</h1>
      <p className="support-login-note">Консоль службы поддержки. Вход только для оператора Comit.</p>
      <Field label="Почта оператора"><input type="email" required autoComplete="username" value={email} disabled={busy} onChange={event => setEmail(event.target.value)} /></Field>
      <Field label="Ключ доступа"><input type="password" required autoComplete="current-password" value={key} disabled={busy} onChange={event => setKey(event.target.value)} /></Field>
      <Action type="submit" variant="primary" disabled={busy}>{busy ? "Проверяем…" : "Войти"}</Action>
      {error && <p className="communication-error" role="alert">{error}</p>}
    </form>
  </section>;
}

/** Conversation directory: every account that has touched the Comit chat, newest activity first. */
function ChatsScreen({ navigate }: { navigate: (path: string) => void }) {
  const conversations = useResource(() => api.getSupportConversations(0, 100));
  const setData = conversations.setData;
  useEffect(() => {
    // Quiet 5 s refresh; setData is a stable setter, so this effect never restarts on renders.
    let active = true, timer: ReturnType<typeof setTimeout> | undefined;
    const poll = () => {
      if (document.visibilityState === "visible") {
        void api.getSupportConversations(0, 100)
          .then(page => { if (active) setData(page); })
          .catch(() => undefined);
      }
      timer = setTimeout(poll, 5000);
    };
    timer = setTimeout(poll, 5000);
    document.addEventListener("visibilitychange", poll);
    return () => { active = false; if (timer) clearTimeout(timer); document.removeEventListener("visibilitychange", poll); };
  }, [setData]);
  const items = (conversations.data?.items ?? []).map(item => ({
    id: item.account_id, title: item.email, subtitle: roleLabel[item.role],
    state: item.last_text ? `${time(item.last_created_at)} · ${item.last_text}` : "Нет сообщений",
    unread: item.unread_count > 0 ? item.unread_count : undefined,
  }));
  return <section className="support-screen">
    <div className="support-screen-header"><h1>Чаты поддержки</h1></div>
    <RecruitmentChatList loading={conversations.loading} error={conversations.error} retry={conversations.refresh}
      emptyText="Диалогов пока нет — они появятся, когда пользователи напишут в чат Comit."
      items={items} onOpen={id => navigate(`/chat/${id}`)} />
  </section>;
}

/** One account's Comit conversation; the operator answers as Comit. */
function ConversationScreen({ accountId, navigate }: { accountId: string; navigate: (path: string) => void }) {
  const [items, setItems] = useState<SystemNotification[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null);
  const [text, setText] = useState(""), [sending, setSending] = useState(false);
  const [files, setFiles] = useState<SystemChatFile[]>([]), [uploading, setUploading] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const cursor = useRef(0), fetching = useRef(false), known = useRef<Map<string, SystemNotification>>(new Map());
  const meta = useResource(() => api.getSupportConversations(0, 100), [accountId]);
  const conversation = meta.data?.items.find(item => item.account_id === accountId);
  useEffect(() => {
    let active = true;
    cursor.current = 0; known.current = new Map(); setItems([]); setFiles([]); setLoading(true); setError(null);
    const poll = async () => {
      if (fetching.current || document.visibilityState !== "visible") return;
      fetching.current = true;
      try {
        const page = await api.getSupportMessages(accountId, cursor.current, 100);
        if (!active) return;
        cursor.current = Math.max(cursor.current, page.next_sequence);
        for (const item of page.items) known.current.set(item.id, item);
        setItems([...known.current.values()].sort((a, b) => a.sequence - b.sequence));
        setError(null);
        const unread = page.items.filter(item => item.type === "support.user_message").at(-1);
        if (unread) void api.markSupportConversationRead(accountId, { read_sequence: unread.sequence }).catch(() => undefined);
      } catch (reason) { if (active) setError(errorText(reason)); }
      finally { fetching.current = false; if (active) setLoading(false); }
    };
    void poll();
    const timer = setInterval(() => void poll(), 2500);
    document.addEventListener("visibilitychange", poll);
    return () => { active = false; clearInterval(timer); document.removeEventListener("visibilitychange", poll); };
  }, [accountId]);
  const submit: FormEventHandler<HTMLFormElement> = async event => {
    event.preventDefault();
    const body = text.trim();
    if ((!body && !files.length) || sending || uploading) return;
    setSending(true);
    try {
      const item = await api.sendSupportMessage(accountId, { idempotency_key: crypto.randomUUID(), text: body, files });
      setText("");
      setFiles([]);
      known.current.set(item.id, item);
      setItems([...known.current.values()].sort((a, b) => a.sequence - b.sequence));
    } catch (reason) { setError(errorText(reason)); }
    finally { setSending(false); }
  };
  const upload = async (file: File | undefined): Promise<void> => {
    if (!file || uploading || sending || files.length >= 8) return;
    if (!file.size || file.size > 8 * 1024 * 1024) { setError("Файл должен быть до 8 МБ. В сообщении — до 8 файлов."); return; }
    if (!["application/pdf", "text/plain", "text/markdown", "image/png", "image/jpeg", "image/gif", "image/webp"].includes(file.type)
      && !/\.(pdf|txt|md|png|jpe?g|gif|webp)$/iu.test(file.name)) { setError("Поддерживаются PDF, TXT, MD, PNG, JPEG, GIF и WebP."); return; }
    setUploading(true); setError(null);
    try { const types: Record<string, string> = { pdf: "application/pdf", txt: "text/plain", md: "text/markdown", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp" }; const value = await api.uploadSupportFile(accountId, { idempotency_key: crypto.randomUUID(), name: file.name, media_type: file.type || types[file.name.split(".").pop()?.toLowerCase() ?? ""] || "application/octet-stream", data_base64: await fileBase64(file) }); setFiles(current => [...current, value]); }
    catch (reason) { setError(errorText(reason)); }
    finally { setUploading(false); if (input.current) input.current.value = ""; }
  };
  const download = async (file: SystemChatFile): Promise<void> => {
    try { const value = await api.downloadSupportFile(accountId, file.id); saveFile(value.name, value.data_base64); }
    catch (reason) { setError(errorText(reason)); }
  };
  return <section className="recruitment-screen">
    <div className="chat-back">
      <Action className="circle" onClick={() => navigate("/chats")} aria-label="Вернуться"><Icon name="back" /></Action>
      <div className="participant-name">
        <strong>{conversation?.email ?? "Диалог"}</strong>
        <small>{conversation ? roleLabel[conversation.role] : "Загружаем…"}</small>
      </div>
    </div>
    <div className="recruitment-thread">
      <div className="chat-history-viewport"><div className="recruitment-messages" aria-label="Диалог поддержки">
        {(loading || error) && <State loading={loading} error={error} />}
        {!loading && !items.length && <p className="empty-note">Сообщений пока нет.</p>}
        {items.map(item => <article key={item.id} className={`message-bubble ${item.type === "support.reply" ? "outgoing" : "incoming"}`}>
          <p>{item.text}</p>
          {(item.files ?? []).map(file => <button className="message-file" key={file.id} type="button" onClick={() => void download(file)}>{file.name} · {(file.size / 1024).toFixed(0)} КБ ↓</button>)}
          <time dateTime={item.created_at}>{time(item.created_at)}</time>
        </article>)}
      </div></div>
      <div className="communication-composer-wrap">
        {error && <p className="communication-error" role="alert">{error}</p>}
        {!!files.length && <div className="pending-files">{files.map(file => <button key={file.id} type="button" onClick={() => setFiles(current => current.filter(item => item.id !== file.id))}>{file.name} ×</button>)}</div>}
        <ChatComposer text={text} onTextChange={value => { setText(value); setError(null); }} onSubmit={event => void submit(event)}
          messageLabel="Ответ от имени Comit" sendLabel="Ответить как Comit" placeholder="Ответ от имени Comit" maxLength={2000}
          canSubmit={!sending && !uploading && (!!text.trim() || !!files.length)} sending={sending} uploading={uploading}
          attachDisabled={uploading || sending || files.length >= 8} onAttach={() => input.current?.click()} attachLabel="Прикрепить файл до 8 МБ">
          <input ref={input} type="file" accept=".pdf,.txt,.md,image/png,image/jpeg,image/gif,image/webp" hidden onChange={event => void upload(event.target.files?.[0])} />
        </ChatComposer>
      </div>
    </div>
  </section>;
}

/** Cookie-authenticated operator shell: login gate, then the conversation directory. */
export default function App() {
  const { route, navigate } = useRoute();
  const [operator, setOperator] = useState<SupportOperatorView | null>(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api.getSupportMe().then(value => { if (active) setOperator(value); })
      .catch(reason => { if (!(reason instanceof ApiError && reason.status === 401) && active) console.error(reason); })
      .finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
  }, []);
  const logout = async () => {
    if (busy) return;
    setBusy(true);
    try { await api.supportLogout(); } finally { setOperator(null); setBusy(false); navigate("/chats"); }
  };
  if (checking) return <main className="support-app comit-design"><State loading /></main>;
  if (!operator) return <main className="support-app comit-design"><LoginScreen onLogin={setOperator} /></main>;
  const chatMatch = /^\/chat\/([0-9a-f-]{36})$/.exec(route.split("?")[0]!);
  return <main className={`support-app comit-design${chatMatch ? " chat-page" : ""}`}>
    <header className="support-shell-header">
      <span className="support-brand"><Icon name="logo" /> Comit · Поддержка</span>
      <Action variant="glass" onClick={() => void logout()} disabled={busy}>Выйти</Action>
    </header>
    {chatMatch ? <ConversationScreen accountId={chatMatch[1]!} navigate={navigate} /> : <ChatsScreen navigate={navigate} />}
  </main>;
}

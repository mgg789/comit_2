import { useEffect, useRef, useState } from "react";
import type { ChatUnreadStatus, SystemChatPage, SystemChatReadStatus, SystemNotification } from "@comit/shared";
import { Action, Icon, State } from "./design";
import "./conversation.css";
import { systemNotificationPath } from "./system-navigation";

/** Caller binds account authority; no message-send operation exists for the system inbox. */
export interface SystemInboxTransport {
  getPage(after: number, limit: number): Promise<SystemChatPage>;
  markRead(sequence: number): Promise<SystemChatReadStatus>;
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

/** Permanent, read-only notifications conversation; receipts advance only for visible messages. */
export function SystemInbox({ transport, errorText, navigate }: { transport: SystemInboxTransport; errorText: (reason: unknown) => string; navigate: (path: string) => void }) {
  const [items, setItems] = useState<SystemNotification[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null);
  const [more, setMore] = useState(false), [visibleSequence, setVisibleSequence] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const cursor = useRef(0), read = useRef(0), fetching = useRef(false), history = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    cursor.current = 0; read.current = 0; setItems([]); setLoading(true); setError(null); setVisibleSequence(0);
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
    }, { threshold: 0.95 });
    history.current?.querySelectorAll("[data-sequence]").forEach(element => observer.observe(element));
    return () => observer.disconnect();
  }, [items]);
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
  return <div className="recruitment-thread system-inbox">
    <header className="participant-header"><span className="anonymous-avatar system-avatar"><Icon name="logo" /></span><div className="participant-name"><strong>Comit</strong><small>Уведомления платформы</small></div></header>
    <div className="recruitment-messages" ref={history} aria-label="Уведомления Comit">
      {(loading || error) && <State loading={loading} error={error} retry={() => setAttempt(value => value + 1)} />}
      {!loading && !error && !items.length && <p className="empty-note">Здесь появятся уведомления платформы.</p>}
      {items.map(item => {
        const path = item.target ? systemNotificationPath(item.target.path) : null;
        return <article className="message-bubble incoming" data-sequence={item.sequence} key={item.id}><p>{item.text}</p>{path && <Action onClick={() => navigate(path)}>Открыть</Action>}<time dateTime={item.created_at}>{new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(new Date(item.created_at))}</time></article>;
      })}
      {more && <Action disabled={loading} onClick={() => window.dispatchEvent(new Event("comit:system-load-more"))}>Другие уведомления</Action>}
    </div>
  </div>;
}

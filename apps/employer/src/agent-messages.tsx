import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AgentsOrganizationMessage, AgentsOrganizationMessagePage } from "@comit/shared";
import { Icon } from "./ui";
import { useEmployerPreferences } from "./preferences";

/** Public messages of one durable turn; private provider reasoning is never included. */
export interface AgentReplyGroup {
  id: string;
  messages: AgentsOrganizationMessage[];
  startedAt: string | null;
  attachments?: NonNullable<AgentsOrganizationMessage["attachments"]>;
}

/**
 * Initial `after_sequence` for agent history. The server returns rows strictly after the
 * cursor and numbers a new chat from 0, so starting at 0 would drop the first user message.
 */
export const AGENT_HISTORY_START = -1;

/** Advance past consumed rows; a null continuation ends paging even for a full page. */
export function agentHistoryProgress(page: AgentsOrganizationMessagePage, cursor: number) {
  return { cursor: page.next_sequence ?? page.items.at(-1)?.sequence ?? cursor,
    hasMore: page.next_sequence != null };
}

/** Project ordered history into user bubbles and one assistant bubble per turn. */
export function groupAgentMessages(messages: AgentsOrganizationMessage[]): Array<AgentsOrganizationMessage | AgentReplyGroup> {
  const rows: Array<AgentsOrganizationMessage | AgentReplyGroup> = [];
  const groups = new Map<string, AgentReplyGroup>();
  const starts = new Map(messages.filter((item) => item.role === "user" && item.turn_id)
    .map((item) => [item.turn_id, item.created_at || null]));
  for (const message of messages) {
    if (message.role === "user" && (message.content || message.attachments?.length)) rows.push(message);
    if (message.role !== "assistant" && !(message.role === "tool" && message.attachments?.length)) continue;
    const id = message.turn_id || message.message_id;
    let group = groups.get(id);
    if (!group) {
      group = { id, messages: [], startedAt: starts.get(id) || message.created_at || null };
      groups.set(id, group);
      rows.push(group);
    }
    if (message.role === "assistant") group.messages.push(message);
    for (const file of message.attachments || []) {
      group.attachments ||= [];
      if (!group.attachments.some(existing => existing.document_id === file.document_id)) group.attachments.push(file);
    }
  }
  return rows;
}

/** Russian elapsed-time label for observed processing, with correct plural forms. */
export function thoughtLabel(seconds: number, active = false): string {
  const n = Math.max(1, Math.round(seconds));
  const word = n % 10 === 1 && n % 100 !== 11 ? "секунду"
    : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "секунды" : "секунд";
  return `${active ? "Думаю" : "Думал"} ${n} ${word}`;
}

/** Render GFM safely: raw HTML and remote images are excluded, links use safe defaults. */
export function AgentMarkdown({ text }: { text: string }) {
  return <div className="agent-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml
    components={{ img: () => null, a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> }}>
    {text}
  </ReactMarkdown></div>;
}

async function copyText(text: string): Promise<void> {
  try {
    if (navigator.clipboard) { await navigator.clipboard.writeText(text); return; }
  } catch { /* Fall back for contexts without clipboard permission. */ }
  const previous = document.activeElement;
  const field = document.createElement("textarea");
  field.value = text;
  field.readOnly = true;
  field.style.position = "fixed";
  field.style.left = "-9999px";
  document.body.append(field);
  try { field.select(); if (!document.execCommand("copy")) throw new Error("copy_failed"); }
  finally { field.remove(); if (previous instanceof HTMLElement) previous.focus(); }
}

/** Copy raw message text with bounded, accessible success/failure feedback. */
export function CopyMessage({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const systemReduced = useReducedMotion();
  const { preferences } = useEmployerPreferences();
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const label = state === "copied" ? "Скопировано" : state === "failed" ? "Не удалось скопировать" : "Скопировать сообщение";
  return <motion.button type="button" className="copy-message" aria-label={label} title={label}
    whileTap={systemReduced || preferences.reducedMotion ? undefined : { scale: 0.9 }}
    onClick={async () => {
      try { await copyText(text); setState("copied"); } catch { setState("failed"); }
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setState("idle"), 1400);
    }}>
    <Icon name={state === "copied" ? "check" : "copy"} />
    <span className="copy-feedback" role="status">{state === "idle" ? "" : label}</span>
  </motion.button>;
}

/** Show a measured non-interactive time pill and the final answer, without intermediate narration. */
export function AgentReply({ group, active, now, failed = false, reducedMotion = false, onDownload }: {
  group: AgentReplyGroup; active: boolean; now: number; failed?: boolean; reducedMotion?: boolean;
  onDownload?: (documentId: string) => Promise<void>;
}) {
  const final = active ? undefined : [...group.messages].reverse().find((item) => !item.tool_calls?.length && item.content);
  const start = group.startedAt ? Date.parse(group.startedAt) : NaN;
  const finishedAt = final?.created_at || group.messages.at(-1)?.created_at;
  const finish = finishedAt ? Date.parse(finishedAt) : NaN;
  const seconds = Number.isFinite(start) ? ((active ? now : Number.isFinite(finish) ? finish : now) - start) / 1000 : 1;
  const label = Number.isFinite(start) && Number.isFinite(finish) ? thoughtLabel(seconds) : "Ход выполнения";
  return <>
    <div className="agent-reply-header">
      {active ? <div className="agent-thinking" role="status" aria-label="Агент думает">
        <span className="agent-think-dot" /><span className="agent-think-dot" /><span className="agent-think-dot" />
      </div> : Number.isFinite(start) && Number.isFinite(finish) ? <p className="agent-thought-label">{label}</p> : null}
    </div>
    {final && <motion.div className="agent-final-answer" initial={reducedMotion ? false : { opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: reducedMotion ? 0 : 0.28 }}>
      <AgentMarkdown text={final.content || ""} />
      <div className="agent-answer-actions">
        <CopyMessage text={final.content || ""} />
      </div>
    </motion.div>}
    {!active && onDownload && group.attachments?.map(file => <button className="message-file" key={file.document_id}
      onClick={() => { void onDownload(file.document_id); }}>{file.name} ↓</button>)}
    {failed && <p role="status">Не удалось завершить ответ. Часть действий могла сохраниться. Проверьте результат перед новым поручением.</p>}
  </>;
}

import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AgentsOrganizationMessage } from "@comit/shared";
import { asset } from "./runtime";
import { useEmployerPreferences } from "./preferences";

/** Public messages of one durable turn; private provider reasoning is never included. */
export interface AgentReplyGroup {
  id: string;
  messages: AgentsOrganizationMessage[];
  startedAt: string | null;
}

/** Project ordered history into user bubbles and one assistant bubble per turn. */
export function groupAgentMessages(messages: AgentsOrganizationMessage[]): Array<AgentsOrganizationMessage | AgentReplyGroup> {
  const rows: Array<AgentsOrganizationMessage | AgentReplyGroup> = [];
  const groups = new Map<string, AgentReplyGroup>();
  const starts = new Map(messages.filter((item) => item.role === "user" && item.turn_id)
    .map((item) => [item.turn_id, item.created_at || null]));
  for (const message of messages) {
    if (message.role === "user" && (message.content || message.attachments?.length)) rows.push(message);
    if (message.role !== "assistant") continue;
    const id = message.turn_id || message.message_id;
    let group = groups.get(id);
    if (!group) {
      group = { id, messages: [], startedAt: starts.get(id) || message.created_at || null };
      groups.set(id, group);
      rows.push(group);
    }
    group.messages.push(message);
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
    {state === "copied" ? <span aria-hidden="true">✓</span> : <img src={asset("copy.svg")} alt="" />}
    <span className="copy-feedback" role="status">{state === "idle" ? "" : label}</span>
  </motion.button>;
}

/** Collapse public tool-round narration and show only the final assistant answer normally. */
export function AgentReply({ group, active, now, failed = false }: {
  group: AgentReplyGroup; active: boolean; now: number; failed?: boolean;
}) {
  const final = [...group.messages].reverse().find((item) => !item.tool_calls?.length && item.content);
  const steps = group.messages.filter((item) => item !== final && item.content);
  const start = group.startedAt ? Date.parse(group.startedAt) : NaN;
  const finishedAt = final?.created_at || group.messages.at(-1)?.created_at;
  const finish = finishedAt ? Date.parse(finishedAt) : NaN;
  const seconds = Number.isFinite(start) ? ((active ? now : Number.isFinite(finish) ? finish : now) - start) / 1000 : 1;
  return <>
    {(active || steps.length > 0 || Number.isFinite(start)) && <details className={`agent-thought ${active ? "thinking" : ""}`}>
      <summary>{Number.isFinite(start) && (active || Number.isFinite(finish)) ? thoughtLabel(seconds, active) : active ? "Думаю" : "Ход выполнения"}{active && <span className="thinking-dots" aria-hidden="true">…</span>}</summary>
      {steps.length ? steps.map((step) => <AgentMarkdown key={step.message_id} text={step.content || ""} />)
        : <p>Шагов с публичным описанием пока нет</p>}
    </details>}
    {final && <><AgentMarkdown text={final.content || ""} /><CopyMessage text={final.content || ""} /></>}
    {failed && <p role="status">Не удалось завершить ответ. Можно отправить запрос ещё раз.</p>}
  </>;
}

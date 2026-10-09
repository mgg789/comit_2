import type { AgentsOrganizationBudget, AgentsOrganizationMessage, AgentsPendingAnswer } from "@comit/shared";
import { agentOrbUrl, AnimatePresence, Button, cx, droidjeIconUrl, fadeUp, Icon, MessageComposer, motion, REVEAL, Spinner, StateMessage, TAP } from "@comit/ui-kit";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, describeError, isUnauthorized, newIdempotencyKey } from "../api";
import { useSession } from "../auth/session";

/** Background reports and delegated-task updates arrive as messages; poll while the overlay is open. */
const MESSAGE_POLL_MS = 5000;
/** Presence heartbeat; the server treats the employer as absent after 120 s without one (docs/agents.md). */
const PRESENCE_MS = 30_000;
const TURN_POLL_MS = 1500;

function mergeMessages(previous: AgentsOrganizationMessage[], incoming: AgentsOrganizationMessage[]): AgentsOrganizationMessage[] {
  const byId = new Map(previous.map((message) => [message.message_id, message]));
  for (const message of incoming) byId.set(message.message_id, message);
  return [...byId.values()].sort((left, right) => left.sequence - right.sequence);
}

/** Micro-USD decimal strings → share of the rolling 7-day limit already spent or reserved, 0..1. */
export function budgetUsage(budget: AgentsOrganizationBudget): number {
  const limit = Number(budget.limit_micro_usd);
  if (!Number.isFinite(limit) || limit <= 0) return 0;
  const used = Number(budget.spent_micro_usd) + Number(budget.reserved_micro_usd);
  return Math.min(1, Math.max(0, used / limit));
}

const OPEN_EVENT = "comit:open-agent";

/** Open the global agent from anywhere in the cabinet, optionally with a draft request in the composer. */
export function openAgent(prompt?: string): void {
  window.dispatchEvent(new CustomEvent<string | undefined>(OPEN_EVENT, { detail: prompt }));
}

/** Floating orb button (bottom-right on every cabinet page) that opens the employer's global AI agent. */
export function AgentLauncher() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    const onOpen = (event: Event) => {
      const prompt = event instanceof CustomEvent && typeof event.detail === "string" ? event.detail : "";
      setDraft(prompt);
      setOpen(true);
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, []);

  return (
    <>
      <AnimatePresence>{open && <AgentOverlay initialDraft={draft} />}</AnimatePresence>
      <button
        type="button"
        aria-label={open ? "Закрыть AI-агента" : "Открыть AI-агента"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="glass fixed right-4 bottom-4 z-50 inline-flex size-20 cursor-pointer items-center justify-center rounded-full transition-[scale] duration-150 hover:scale-105 active:scale-95 lg:right-[2.4375rem] lg:bottom-[1.8125rem] lg:size-[6.5rem]"
      >
        <AnimatePresence mode="wait" initial={false}>
          {open ? (
            <motion.span key="close" initial={{ rotate: -90, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: 90, opacity: 0 }} transition={TAP} className="text-text">
              <Icon name="close" size={44} />
            </motion.span>
          ) : (
            <motion.img
              key="orb"
              src={agentOrbUrl}
              alt=""
              initial={{ scale: 0.6, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.6, opacity: 0 }}
              transition={TAP}
              className="size-14 lg:size-[3.9375rem]"
            />
          )}
        </AnimatePresence>
      </button>
    </>
  );
}

/** Global agent chat over a blurred cabinet (Figma «AI-АГЕНТ»). The agent advises and acts only through employer-authorized tools. */
function AgentOverlay({ initialDraft }: { initialDraft: string }) {
  const { expire } = useSession();
  const [messages, setMessages] = useState<AgentsOrganizationMessage[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<"budget" | null>(null);
  const cursor = useRef(0);
  const list = useRef<HTMLDivElement>(null);

  const pull = useCallback(async () => {
    for (;;) {
      const page = await api.getOrganizationMessages(cursor.current, 50);
      setMessages((previous) => mergeMessages(previous, page.items));
      const next = page.next_sequence ?? cursor.current;
      if (next <= cursor.current || page.items.length < 50) {
        cursor.current = Math.max(cursor.current, next);
        return;
      }
      cursor.current = next;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fail = (failure: unknown) => {
      if (cancelled) return;
      if (isUnauthorized(failure)) expire();
      setError(describeError(failure));
      setStatus("error");
    };
    void api
      .getOrganizationChat()
      .then(() => pull())
      .then(() => !cancelled && setStatus("ready"), fail);
    const poll = window.setInterval(() => void pull().catch(() => undefined), MESSAGE_POLL_MS);
    const beat = () => void api.setOrganizationPresence(true).catch(() => undefined);
    beat();
    const presence = window.setInterval(beat, PRESENCE_MS);
    return () => {
      cancelled = true;
      window.clearInterval(poll);
      window.clearInterval(presence);
      void api.setOrganizationPresence(false).catch(() => undefined);
    };
  }, [pull, expire]);

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight, behavior: "smooth" });
  }, [messages.length]);

  const visible = messages.filter((message) => (message.role === "user" || message.role === "assistant") && message.content?.trim());

  return (
    <motion.div
      role="dialog"
      aria-modal="true"
      aria-label="AI-агент компании"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={REVEAL}
      className="fixed inset-0 z-40 flex flex-col bg-black/85 px-4 pt-24 pb-6 backdrop-blur-[0.78rem] lg:pt-[5.375rem] lg:pb-[5rem]"
    >
      <div ref={list} aria-live="polite" className="mx-auto flex w-full max-w-[64.75rem] flex-1 flex-col gap-8 overflow-y-auto pb-6 lg:gap-[2.5rem]">
        {status === "loading" && <StateMessage kind="loading" title="Подключаем агента" />}
        {status === "error" && <StateMessage kind="error" title="Агент недоступен" description={error} />}
        {status === "ready" && visible.length === 0 && (
          <StateMessage
            kind="empty"
            title="Чем помочь?"
            description="Агент видит ваши вакансии и процессы найма: может разобрать ленту, подсказать по кандидатам или договориться о собеседовании — только с вашего разрешения."
          />
        )}
        <PendingQuestions />
        <AnimatePresence initial={false}>
          {visible.map((message) => (
            <motion.div key={message.message_id} variants={fadeUp} initial="hidden" animate="show" layout="position">
              {message.role === "user" ? (
                <div className="flex flex-col items-end gap-3">
                  <p className="max-w-[85%] rounded-[1.875rem] bg-light px-6 py-4 text-base font-medium text-on-light lg:rounded-[6.25rem] lg:px-[1.5rem] lg:py-[1.125rem] lg:text-[1.25rem]">
                    {message.content}
                  </p>
                  <CopyButton text={message.content ?? ""} />
                </div>
              ) : (
                <div className="flex gap-4 lg:gap-[1.25rem]">
                  <img src={agentOrbUrl} alt="" className="size-11 shrink-0 lg:size-[3.75rem]" />
                  <div className="flex min-w-0 flex-col items-start gap-4 lg:gap-[1.25rem]">
                    {message.turn_id && <ThinkingTime turnId={message.turn_id} />}
                    <AgentText text={message.content ?? ""} />
                    <CopyButton text={message.content ?? ""} />
                  </div>
                </div>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
      <div className="mx-auto w-full max-w-[44.1875rem]">
        <AgentComposer
          initialText={initialDraft}
          onQueued={() => void pull().catch(() => undefined)}
          onSettled={() => void pull().catch(() => undefined)}
        />
      </div>
      <div className="fixed right-4 bottom-28 flex items-end gap-3 lg:right-[3.75rem] lg:bottom-[9.6875rem]">
        <AnimatePresence>{panel === "budget" && <BudgetPopover />}</AnimatePresence>
        <div className="glass flex flex-col items-center gap-[1.125rem] rounded-pill px-[0.8125rem] py-[1.25rem]">
          <button
            type="button"
            aria-pressed={panel === "budget"}
            aria-label="Расход агента"
            title="Расход агента"
            onClick={() => setPanel((value) => (value === "budget" ? null : "budget"))}
            className={cx("inline-flex cursor-pointer rounded-full p-1 transition-[background-color,scale] duration-150 active:scale-90", panel === "budget" ? "bg-white/20" : "hover:bg-white/10")}
          >
            <Icon name="usage" size={32} />
          </button>
        </div>
      </div>
    </motion.div>
  );
}

/** Agent answer text; bullet lines are tinted like the design's highlighted list items. */
function AgentText({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-1 text-base font-medium tracking-[-0.017rem] whitespace-pre-wrap text-text lg:text-[1.25rem]">
      {text.split("\n").map((line, index) => (
        <p key={index} className={cx(/^\s*[•\-*]\s/.test(line) && "text-agent-accent", !line.trim() && "h-3")}>
          {line}
        </p>
      ))}
    </div>
  );
}

/** «Думал N секунд» pill: server turn duration (created → updated), loaded once per turn. */
function ThinkingTime({ turnId }: { turnId: string }) {
  const [seconds, setSeconds] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.getOrganizationTurn(turnId).then(
      (turn) => {
        if (cancelled || !turn.created_at || !turn.updated_at || turn.status !== "succeeded") return;
        setSeconds(Math.max(1, Math.round((Date.parse(turn.updated_at) - Date.parse(turn.created_at)) / 1000)));
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [turnId]);
  if (seconds === null) return null;
  return <span className="rounded-[6.25rem] bg-light px-5 py-3 text-sm font-medium text-black lg:px-[1.5rem] lg:py-[1rem] lg:text-[1rem]">Думал {seconds} с</span>;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={copied ? "Скопировано" : "Копировать"}
      title={copied ? "Скопировано" : "Копировать"}
      onClick={() =>
        void navigator.clipboard.writeText(text).then(
          () => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          },
          () => undefined,
        )
      }
      className="inline-flex cursor-pointer text-text/80 transition-[color,scale] duration-150 hover:text-text active:scale-90"
    >
      <Icon name={copied ? "check" : "copy"} size={24} />
    </button>
  );
}

/** Rolling 7-day spend of the agent against its limit (Figma usage bar, «20%»). */
function BudgetPopover() {
  const [budget, setBudget] = useState<AgentsOrganizationBudget | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.getOrganizationBudget().then(setBudget, (failure: unknown) => setError(describeError(failure)));
  }, []);
  const usage = budget ? budgetUsage(budget) : 0;
  return (
    <motion.div
      initial={{ opacity: 0, x: 12 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 12 }}
      transition={TAP}
      className="glass w-72 rounded-pill px-6 py-4"
    >
      {error && <p className="text-sm text-danger-soft">{error}</p>}
      {!budget && !error && <Spinner />}
      {budget && (
        <>
          <div className="flex items-center gap-4">
            <span className="text-xl font-semibold">{Math.round(usage * 100)}%</span>
            <div className="h-6 flex-1 overflow-hidden rounded-pill bg-black/40">
              <motion.div initial={{ width: 0 }} animate={{ width: `${Math.max(usage * 100, 4)}%` }} transition={REVEAL} className="h-full rounded-pill bg-light" />
            </div>
          </div>
          <p className="mt-2 text-xs text-muted">Расход агента за 7 дней от лимита аккаунта{budget.pricing_configured ? "" : " · тарифы не настроены"}</p>
        </>
      )}
    </motion.div>
  );
}

/** Questions the agent's background tasks are waiting on (e.g. interview slots). */
function PendingQuestions() {
  const [items, setItems] = useState<AgentsPendingAnswer[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(() => {
    api.getOrganizationPendingAnswers().then((answers) => setItems(answers.filter((answer) => !answer.answered)), () => undefined);
  }, []);
  useEffect(load, [load]);
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-3 rounded-[1.5rem] border border-warning/40 bg-warning/10 p-4">
      <h3 className="font-semibold">Агент ждёт вашего ответа</h3>
      {items.map((item) => (
        <form
          key={item.answer_id}
          className="flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const answer = drafts[item.answer_id]?.trim();
            if (!answer) return;
            setBusy(item.answer_id);
            api
              .answerOrganizationQuestion(item.answer_id, { answer })
              .then(load, () => undefined)
              .finally(() => setBusy(null));
          }}
        >
          <p className="text-sm whitespace-pre-wrap">{item.prompt}</p>
          <div className="flex gap-2">
            <input
              aria-label="Ответ агенту"
              value={drafts[item.answer_id] ?? ""}
              onChange={(event) => setDrafts((previous) => ({ ...previous, [item.answer_id]: event.target.value }))}
              className="min-w-0 flex-1 rounded-field bg-field px-3 py-2 text-text outline-none focus:ring-2 focus:ring-primary/70"
            />
            <Button type="submit" size="sm" loading={busy === item.answer_id}>
              Ответить
            </Button>
          </div>
        </form>
      ))}
    </section>
  );
}

function AgentComposer({ initialText, onQueued, onSettled }: { initialText: string; onQueued: () => void; onSettled: () => void }) {
  const { expire } = useSession();
  const [text, setText] = useState(initialText);
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingKey = useRef<string | null>(null);

  async function send() {
    const message = text.trim();
    if (!message || thinking) return;
    pendingKey.current ??= newIdempotencyKey();
    setThinking(true);
    setError(null);
    try {
      let turn = await api.sendOrganizationMessage({ message, idempotency_key: pendingKey.current });
      setText("");
      pendingKey.current = null;
      onQueued();
      while (turn.status === "queued" || turn.status === "processing" || turn.status === "retrying") {
        await new Promise((resolve) => window.setTimeout(resolve, TURN_POLL_MS));
        turn = await api.getOrganizationTurn(turn.turn_id);
      }
      if (turn.status === "failed") setError(turn.error_message ?? "Агент не смог ответить. Попробуйте переформулировать или повторить позже.");
    } catch (failure) {
      if (isUnauthorized(failure)) expire();
      setError(describeError(failure));
    } finally {
      setThinking(false);
      onSettled();
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <AnimatePresence>
        {thinking && (
          <motion.p initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={TAP} className="flex items-center justify-center gap-2 text-sm text-subtle">
            <ThinkingDots /> Агент думает
          </motion.p>
        )}
      </AnimatePresence>
      <MessageComposer
        value={text}
        onChange={(value) => {
          setText(value);
          pendingKey.current = null;
        }}
        onSubmit={() => void send()}
        busy={thinking}
        footer={
          <>
            <img src={droidjeIconUrl} alt="" className="h-4 w-auto" />
            Работает на Droidje AI · агент не принимает решений о найме
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

/** Three pulsing dots while a turn is queued or running. */
function ThinkingDots() {
  return (
    <span aria-hidden className="inline-flex gap-1">
      {[0, 1, 2].map((index) => (
        <motion.span
          key={index}
          className="size-1.5 rounded-full bg-current"
          animate={{ opacity: [0.25, 1, 0.25], y: [0, -3, 0] }}
          transition={{ duration: 0.9, repeat: Infinity, delay: index * 0.15, ease: "easeInOut" }}
        />
      ))}
    </span>
  );
}

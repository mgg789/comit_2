import type { AgentsOrganizationBudget, AgentsOrganizationMessage, AgentsPendingAnswer } from "@comit/shared";
import { Button, Icon, Spinner, StateMessage, agentOrbUrl, cx } from "@comit/ui-kit";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
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
      {open && <AgentOverlay initialDraft={draft} />}
      <button
        type="button"
        aria-label={open ? "Закрыть AI-агента" : "Открыть AI-агента"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="glass fixed right-4 bottom-4 z-50 inline-flex size-20 cursor-pointer items-center justify-center rounded-full transition-transform duration-150 hover:scale-105 sm:right-10 sm:bottom-10 sm:size-[6.5rem]"
      >
        {open ? <span className="text-4xl text-text">×</span> : <img src={agentOrbUrl} alt="" className="size-14 sm:size-[3.9375rem]" />}
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
    <div role="dialog" aria-modal="true" aria-label="AI-агент компании" className="fixed inset-0 z-40 flex flex-col bg-black/70 px-4 pt-24 pb-6 backdrop-blur-xl sm:pt-28">
      <div ref={list} aria-live="polite" className="mx-auto flex w-full max-w-[43.75rem] flex-1 flex-col gap-6 overflow-y-auto pb-4">
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
        {visible.map((message) =>
          message.role === "user" ? (
            <p key={message.message_id} className="max-w-[85%] self-end rounded-[1.5rem] bg-light px-5 py-3 text-lg text-on-light">
              {message.content}
            </p>
          ) : (
            <div key={message.message_id} className="flex gap-4">
              <img src={agentOrbUrl} alt="" className="size-11 shrink-0" />
              <div className="flex min-w-0 flex-col gap-2">
                <p className="text-lg whitespace-pre-wrap text-text">{message.content}</p>
                <CopyButton text={message.content ?? ""} />
              </div>
            </div>
          ),
        )}
      </div>
      <div className="mx-auto w-full max-w-[43.75rem]">
        <AgentComposer
          initialText={initialDraft}
          onQueued={() => void pull().catch(() => undefined)}
          onSettled={() => void pull().catch(() => undefined)}
        />
        <p className="mt-2 text-center text-xs text-muted">Работает на Droidje AI · агент не принимает решений о найме</p>
      </div>
      <div className="fixed right-4 bottom-28 flex flex-col items-end gap-3 sm:right-12 sm:bottom-40">
        {panel === "budget" && <BudgetPopover />}
        <div className="glass flex flex-col gap-1 rounded-pill p-1.5">
          <button
            type="button"
            aria-pressed={panel === "budget"}
            onClick={() => setPanel((value) => (value === "budget" ? null : "budget"))}
            className={cx("cursor-pointer rounded-full px-3 py-2 text-sm", panel === "budget" ? "bg-light text-on-light" : "text-text hover:bg-white/10")}
          >
            Бюджет
          </button>
        </div>
      </div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() =>
        void navigator.clipboard.writeText(text).then(
          () => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          },
          () => undefined,
        )
      }
      className="w-fit cursor-pointer text-xs text-muted hover:text-text"
    >
      {copied ? "Скопировано" : "Копировать"}
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
    <div className="glass w-64 rounded-[1.5rem] p-4">
      {error && <p className="text-sm text-danger-soft">{error}</p>}
      {!budget && !error && <Spinner />}
      {budget && (
        <>
          <div className="flex items-center gap-3">
            <span className="text-lg font-semibold">{Math.round(usage * 100)}%</span>
            <div className="h-5 flex-1 overflow-hidden rounded-pill bg-black/40">
              <div className="h-full rounded-pill bg-light" style={{ width: `${usage * 100}%` }} />
            </div>
          </div>
          <p className="mt-2 text-xs text-muted">Расход агента за 7 дней от лимита аккаунта{budget.pricing_configured ? "" : " · тарифы не настроены"}</p>
        </>
      )}
    </div>
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
          onSubmit={(event: FormEvent) => {
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

  async function send(event: FormEvent) {
    event.preventDefault();
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
    <form onSubmit={send} className="flex flex-col gap-2">
      {thinking && (
        <p className="flex items-center gap-2 text-sm text-subtle">
          <Spinner size={14} /> Агент думает…
        </p>
      )}
      <div className="glass flex items-center gap-3 rounded-pill p-1.5 pl-5">
        <label htmlFor="agent-input" className="sr-only">
          Сообщение агенту
        </label>
        <input
          id="agent-input"
          value={text}
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
          disabled={thinking || !text.trim()}
          className="glass inline-flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-full text-text hover:bg-white/10 disabled:opacity-50"
        >
          <Icon name="arrow-left" size={22} className="rotate-180" />
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

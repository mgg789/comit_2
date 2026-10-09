import type { RecruitmentEvent } from "@comit/shared";
import { AnimatePresence, Icon, SPRING, TAP, cx, motion } from "@comit/ui-kit";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { RECRUITMENT_STATE_LABEL } from "../recruitment/labels";

/** Per-browser cursor of the last recruitment event the employer has seen in this menu. */
const SEEN_KEY = "comit.employer.seen_event_sequence";
const POLL_MS = 30_000;
const MAX_SHOWN = 20;

function readSeen(): number {
  try {
    const value = Number(window.localStorage.getItem(SEEN_KEY));
    return Number.isSafeInteger(value) && value > 0 ? value : 0;
  } catch {
    return 0;
  }
}

function writeSeen(value: number): void {
  try {
    window.localStorage.setItem(SEEN_KEY, String(value));
  } catch {
    // Blocked storage only means the badge reappears after reload.
  }
}

/** Bell with the number of hiring status changes since the last look (contact-free events, docs/chat-events.md). */
export function NotificationsMenu() {
  const [events, setEvents] = useState<RecruitmentEvent[]>([]);
  const [seen, setSeen] = useState(readSeen);
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    // Read forward from the seen cursor, plus a short tail of already seen history for context.
    const page = await api.getRecruitmentEvents("employer", Math.max(0, readSeen() - MAX_SHOWN), 100);
    setEvents(page.items);
  }, []);

  useEffect(() => {
    void load().catch(() => undefined);
    const timer = window.setInterval(() => void load().catch(() => undefined), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  const unseen = events.filter((event) => event.sequence > seen).length;
  const latest = events.reduce((max, event) => Math.max(max, event.sequence), seen);

  function toggle() {
    setOpen((value) => !value);
    if (!open && latest > seen) {
      writeSeen(latest);
      setSeen(latest);
    }
  }

  const time = (iso: string) => new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
  return (
    <div ref={root} className="relative">
      <button type="button" aria-label={unseen ? `Уведомления: ${unseen} новых` : "Уведомления"} aria-expanded={open} onClick={toggle} className="relative inline-flex cursor-pointer text-text">
        <Icon name="bell" size={30} />
        <AnimatePresence>
        {unseen > 0 && (
          <motion.span
            key={unseen}
            initial={{ scale: 0.4, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.4, opacity: 0 }}
            transition={SPRING}
            className="absolute -top-[0.25rem] left-[1.0625rem] inline-flex h-[1.125rem] min-w-[1.625rem] items-center justify-center rounded-[1.25rem] bg-light px-[0.375rem] text-[1rem] leading-none font-medium tracking-[-0.05em] text-on-light">
            {unseen > 99 ? "99+" : unseen}
          </motion.span>
        )}
        </AnimatePresence>
      </button>
      <AnimatePresence>
      {open && (
        <motion.div
          role="dialog"
          aria-label="Уведомления"
          initial={{ opacity: 0, y: -6, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -6, scale: 0.98 }}
          transition={TAP}
          style={{ transformOrigin: "top right" }}
          className="glass absolute top-full right-0 z-40 mt-4 max-h-96 w-80 overflow-y-auto rounded-field p-2"
        >
          {events.length === 0 ? (
            <p className="px-3 py-4 text-sm text-muted">Событий найма пока нет.</p>
          ) : (
            <ul className="flex flex-col">
              {[...events]
                .reverse()
                .slice(0, MAX_SHOWN)
                .map((event) => (
                  <li key={event.event_id}>
                    <Link
                      to={`/chats/${event.process_id}`}
                      onClick={() => setOpen(false)}
                      className={cx("flex flex-col rounded-[0.625rem] px-3 py-2 hover:bg-white/10", event.sequence > seen ? "text-text" : "text-subtle")}
                    >
                      <span className="text-sm">{RECRUITMENT_STATE_LABEL[event.state]}</span>
                      <span className="text-xs text-muted">{time(event.created_at)}</span>
                    </Link>
                  </li>
                ))}
            </ul>
          )}
        </motion.div>
      )}
      </AnimatePresence>
    </div>
  );
}

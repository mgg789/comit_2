import { forwardRef, useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { RecruitmentEvent } from "@comit/shared";
import { api } from "./runtime";
import { Icon } from "./ui";
import { recruitmentStateNames } from "./communications";
import {
  badgeText,
  eventsCursor,
  latestSequence,
  readSeen,
  recentEvents,
  unseenCount,
  writeSeen,
} from "./notifications";
import "./notifications.css";

const POLL_MS = 30_000;

/**
 * Hiring status changes for the bell (contact-free events, docs/chat-events.md).
 * Polls while signed in; the «seen» cursor lives in this browser only.
 */
export function useRecruitmentNotifications(accountId: string | undefined) {
  const [events, setEvents] = useState<RecruitmentEvent[]>([]);
  const [seen, setSeen] = useState(() => readSeen());
  const load = useCallback(async () => {
    const page = await api.getRecruitmentEvents("employer", eventsCursor(readSeen()), 100);
    setEvents(page.items);
  }, []);
  useEffect(() => {
    if (!accountId) return;
    void load().catch(() => undefined);
    const timer = window.setInterval(() => void load().catch(() => undefined), POLL_MS);
    return () => window.clearInterval(timer);
  }, [accountId, load]);
  /** Mark everything loaded as seen; returns the previous cursor to highlight new rows. */
  const markSeen = useCallback(() => {
    const latest = latestSequence(events, seen);
    if (latest > seen) {
      writeSeen(latest);
      setSeen(latest);
    }
    return seen;
  }, [events, seen]);
  return { events, unseen: unseenCount(events, seen), markSeen };
}

/** Bell in the account capsule with the unread counter from the design. */
export function NotificationsButton({ unseen, open, onClick }: { unseen: number; open: boolean; onClick: () => void }) {
  return (
    <button
      className="plain-icon notifications-button"
      aria-label={unseen ? `Уведомления: ${unseen} новых` : "Уведомления"}
      aria-expanded={open}
      aria-controls="employer-notifications"
      onClick={onClick}
    >
      <Icon name="bell" />
      <AnimatePresence>
        {unseen > 0 && (
          <motion.span
            key={badgeText(unseen)}
            className="notifications-badge"
            aria-hidden="true"
            initial={{ scale: 0.4, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.4, opacity: 0 }}
            transition={{ type: "spring", stiffness: 520, damping: 30 }}
          >
            {badgeText(unseen)}
          </motion.span>
        )}
      </AnimatePresence>
    </button>
  );
}

const time = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** Dropdown under the account capsule; rows newer than `seenBefore` stay bright. */
export const NotificationsPanel = forwardRef<HTMLDivElement, {
  events: readonly RecruitmentEvent[];
  seenBefore: number;
  onSelect: (processId: string) => void;
}>(function NotificationsPanel({ events, seenBefore, onSelect }, ref) {
  const rows = recentEvents(events);
  return (
    <motion.div
      ref={ref}
      id="employer-notifications"
      className="notifications-menu glass"
      role="dialog"
      aria-label="Уведомления"
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
    >
      {rows.length === 0 ? (
        <p className="notifications-empty">Событий найма пока нет.</p>
      ) : (
        <ul>
          {rows.map((event) => (
            <li key={event.event_id}>
              <button
                className={event.sequence > seenBefore ? "is-new" : ""}
                onClick={() => onSelect(event.process_id)}
              >
                <span>{recruitmentStateNames[event.state]}</span>
                <time dateTime={event.created_at}>{time.format(new Date(event.created_at))}</time>
              </button>
            </li>
          ))}
        </ul>
      )}
    </motion.div>
  );
});

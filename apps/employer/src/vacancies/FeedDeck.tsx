import type { FeedCard, FeedPage } from "@comit/shared";
import { ApiError } from "@comit/api-client";
import { Button, Chip, MatchBadge, StateMessage, cx } from "@comit/ui-kit";
import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { api, describeError, errorDetail, isUnauthorized, newIdempotencyKey } from "../api";
import { useSession } from "../auth/session";
import { CLAIM_STATUS_LABEL, GRADE_LABEL, describeFeature, filterFactorLabels, matchPercent, scoreChannels } from "../lib/explain";
import { SPECIALIZATION_LABEL, formatSalaryRange } from "../lib/format";

/** Cards requested per portion; the next page is fetched when fewer than REFILL_AT remain. */
const PAGE_SIZE = 20;
const REFILL_AT = 3;
/** Horizontal drag distance (px) that commits a swipe. */
const SWIPE_THRESHOLD = 120;
/** Poll interval while the vacancy or its selection is still being computed. */
const PENDING_RETRY_MS = 5000;

const HISTORY_BADGE_LABEL: Record<string, string> = {
  previously_visited: "Уже был(а) в ленте",
  visited_updated: "Профиль обновился после пропуска",
};

type DeckState =
  | { kind: "loading" }
  | { kind: "pending"; reason: "vacancy" | "selection" }
  | { kind: "error"; message: string }
  | { kind: "ready" };

/** Classify feed-open failures: matching answers 404/409 while the vacancy or its top-k is still being built. */
function pendingReason(error: unknown): "vacancy" | "selection" | null {
  if (!(error instanceof ApiError)) return null;
  if (error.status === 404) return "vacancy";
  const detail = errorDetail(error) ?? "";
  if (error.status === 409 && /not ready|stale|missing|unavailable/.test(detail)) return "selection";
  return null;
}

interface FeedDeckProps {
  draftId: string;
  /** A like saved a candidate — the «Понравились в ленте» strip should refresh. */
  onLiked: () => void;
}

/**
 * Tinder-style feed of the vacancy (Figma «СТРАНИЦА ВАКАНСИИ», right column).
 * Every card is an immutable server exposure; the client acknowledges visibility once and records
 * one like/dislike per exposure with a UUID key that is reused on retries. Ordering, scores and
 * explanations come from matching-engine — the deck never reorders or re-scores.
 */
export function FeedDeck({ draftId, onLiked }: FeedDeckProps) {
  const { expire } = useSession();
  const [state, setState] = useState<DeckState>({ kind: "loading" });
  const [cards, setCards] = useState<FeedCard[]>([]);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const page = useRef<Pick<FeedPage, "session_id" | "next_cursor" | "has_more"> | null>(null);
  const decisionKeys = useRef(new Map<string, string>());
  const impressionKeys = useRef(new Map<string, string>());
  const acknowledged = useRef(new Set<string>());
  const loadingMore = useRef(false);

  const fail = useCallback(
    (error: unknown) => {
      if (isUnauthorized(error)) expire();
      const reason = pendingReason(error);
      setState(reason ? { kind: "pending", reason } : { kind: "error", message: describeError(error) });
    },
    [expire],
  );

  const open = useCallback(async () => {
    setState((previous) => (previous.kind === "pending" ? previous : { kind: "loading" }));
    try {
      const portion = await api.openFeed(draftId, { limit: PAGE_SIZE });
      page.current = portion;
      setCards(portion.items.filter((card) => card.decision === null));
      setState({ kind: "ready" });
    } catch (error) {
      fail(error);
    }
  }, [draftId, fail]);

  useEffect(() => {
    void open();
  }, [open]);

  // While matching is still building the vacancy or its selection, retry quietly.
  useEffect(() => {
    if (state.kind !== "pending") return;
    const timer = window.setTimeout(() => void open(), PENDING_RETRY_MS);
    return () => window.clearTimeout(timer);
  }, [state, open]);

  const top = cards[0];

  // Visible-impression receipt: once per exposure, same key on retry (server dedupes).
  useEffect(() => {
    if (!top || acknowledged.current.has(top.exposure_id)) return;
    let key = impressionKeys.current.get(top.exposure_id);
    if (!key) {
      key = newIdempotencyKey();
      impressionKeys.current.set(top.exposure_id, key);
    }
    api.acknowledgeFeedImpression(draftId, { exposure_id: top.exposure_id, idempotency_key: key }).then(
      () => acknowledged.current.add(top.exposure_id),
      () => undefined, // Next time the card is on top the same key is retried.
    );
  }, [top, draftId]);

  // Refill from the same session when the visible stack runs low.
  useEffect(() => {
    const current = page.current;
    if (state.kind !== "ready" || cards.length >= REFILL_AT || loadingMore.current) return;
    if (!current?.has_more || !current.next_cursor) return;
    loadingMore.current = true;
    api
      .getFeed(draftId, current.session_id, current.next_cursor, PAGE_SIZE)
      .then((portion) => {
        page.current = portion;
        setCards((previous) => {
          const known = new Set(previous.map((card) => card.exposure_id));
          return [...previous, ...portion.items.filter((card) => card.decision === null && !known.has(card.exposure_id))];
        });
      })
      .catch((error: unknown) => {
        if (errorDetail(error) === "feed session expired") void open();
        else if (isUnauthorized(error)) expire();
      })
      .finally(() => {
        loadingMore.current = false;
      });
  }, [cards.length, state.kind, draftId, open, expire]);

  const decide = useCallback(
    async (action: "like" | "dislike") => {
      if (!top || busy) return;
      let key = decisionKeys.current.get(top.exposure_id);
      if (!key) {
        key = newIdempotencyKey();
        decisionKeys.current.set(top.exposure_id, key);
      }
      setBusy(true);
      setActionError(null);
      try {
        await api.decideFeed(draftId, { exposure_id: top.exposure_id, action, idempotency_key: key });
        setCards((previous) => previous.filter((card) => card.exposure_id !== top.exposure_id));
        if (action === "like") onLiked();
      } catch (error) {
        if (isUnauthorized(error)) expire();
        if (errorDetail(error) === "feed session expired") {
          void open();
          return;
        }
        setActionError(`Реакция не сохранена: ${describeError(error)}`);
      } finally {
        setBusy(false);
      }
    },
    [top, busy, draftId, onLiked, open, expire],
  );

  if (state.kind === "loading") return <StateMessage kind="loading" title="Открываем ленту" />;
  if (state.kind === "pending") {
    return (
      <StateMessage
        kind="loading"
        title={state.reason === "vacancy" ? "Вакансия обрабатывается" : "Подбор готовится"}
        description={
          state.reason === "vacancy"
            ? "Matching строит профиль вакансии. Лента откроется автоматически."
            : "Комбайн отбирает кандидатов и сортирует подборку. Обновим через несколько секунд."
        }
      />
    );
  }
  if (state.kind === "error") {
    return (
      <StateMessage
        kind="error"
        title="Лента недоступна"
        description={state.message}
        action={
          <Button variant="secondary" onClick={() => void open()}>
            Повторить
          </Button>
        }
      />
    );
  }
  if (!top) {
    return (
      <StateMessage
        kind="empty"
        title="Лента на сегодня закончилась"
        description="Новые кандидаты появятся, когда пул пополнится или обновятся профили."
        action={
          <Button variant="secondary" onClick={() => void open()}>
            Проверить снова
          </Button>
        }
      />
    );
  }

  return (
    <section aria-label="Лента кандидатов" className="flex flex-col items-center gap-4">
      <div
        className="relative w-full max-w-[400px] outline-none"
        tabIndex={0}
        aria-keyshortcuts="ArrowLeft ArrowRight"
        onKeyDown={(event) => {
          if (event.key === "ArrowRight") void decide("like");
          if (event.key === "ArrowLeft") void decide("dislike");
        }}
      >
        {cards[2] && <div aria-hidden className="absolute inset-0 translate-x-3 -translate-y-4 rotate-6 rounded-card bg-surface-raised/80" />}
        {cards[1] && <div aria-hidden className="absolute inset-0 -translate-x-3 -translate-y-2 -rotate-6 rounded-card bg-surface-raised" />}
        <SwipeCard key={top.exposure_id} card={top} disabled={busy} onSwipe={(action) => void decide(action)} />
      </div>
      <div className="flex items-center gap-5">
        <button
          type="button"
          onClick={() => void decide("dislike")}
          disabled={busy}
          aria-label="Пропустить (←)"
          className="inline-flex size-14 cursor-pointer items-center justify-center rounded-field bg-surface-raised text-3xl text-text transition-colors duration-150 hover:bg-danger disabled:opacity-50"
        >
          ×
        </button>
        <a
          href={top.resume_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex h-14 items-center justify-center rounded-field bg-surface-raised px-5 font-semibold text-text transition-colors duration-150 hover:bg-white/15"
        >
          PDF резюме
        </a>
        <button
          type="button"
          onClick={() => void decide("like")}
          disabled={busy}
          aria-label="Нравится (→)"
          className="inline-flex size-14 cursor-pointer items-center justify-center rounded-field bg-surface-raised text-3xl text-text transition-colors duration-150 hover:bg-success-strong disabled:opacity-50"
        >
          +
        </button>
      </div>
      <p className="text-center text-xs text-muted">Свайп или ← / → · осталось в порции: {cards.length}</p>
      {actionError && (
        <p role="alert" className="text-center text-sm text-danger-soft">
          {actionError}
        </p>
      )}
    </section>
  );
}

/** Draggable top card; releases past SWIPE_THRESHOLD commit like (right) or dislike (left). */
function SwipeCard({ card, disabled, onSwipe }: { card: FeedCard; disabled: boolean; onSwipe: (action: "like" | "dislike") => void }) {
  const [dx, setDx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const start = useRef<number | null>(null);

  function onPointerDown(event: ReactPointerEvent<HTMLElement>) {
    if (disabled || (event.target as HTMLElement).closest("a, button")) return;
    start.current = event.clientX;
    setDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function onPointerMove(event: ReactPointerEvent<HTMLElement>) {
    if (start.current !== null) setDx(event.clientX - start.current);
  }
  function onPointerUp() {
    if (start.current === null) return;
    start.current = null;
    setDragging(false);
    if (Math.abs(dx) >= SWIPE_THRESHOLD) onSwipe(dx > 0 ? "like" : "dislike");
    setDx(0);
  }

  const { explanation, category } = card;
  const factors = filterFactorLabels(card.filter_factors);
  const features = explanation.top_matched_features.slice(0, 3);
  const fsp = card.external_evidence?.filter((item) => item.kind === "fsp") ?? [];
  return (
    <article
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      style={{ transform: `translateX(${dx}px) rotate(${dx / 20}deg)` }}
      className={cx(
        "relative flex touch-pan-y flex-col gap-3 overflow-hidden rounded-card border border-glass-border bg-surface p-5 select-none",
        !dragging && "transition-transform duration-200",
      )}
    >
      <div aria-hidden className="bg-accent-gradient pointer-events-none absolute inset-x-0 top-0 h-40 opacity-40" />
      <div className="relative flex items-start justify-between gap-3">
        <h3 className="font-display text-2xl leading-tight">{card.display_name}</h3>
        <MatchBadge percent={matchPercent(explanation)} shape="pill" className="shrink-0" />
      </div>
      <div className="relative flex flex-wrap gap-2">
        <Chip>{SPECIALIZATION_LABEL[category.specialization]}</Chip>
        <Chip>
          {GRADE_LABEL[category.grade]}
          {category.grade_status === "self_reported" && " · заявлен"}
        </Chip>
        <Chip>{card.city}</Chip>
        {card.history_badges.map((badge) => (
          <Chip key={badge} tone="info">
            {HISTORY_BADGE_LABEL[badge] ?? badge}
          </Chip>
        ))}
        {card.hypothesis_pair_id && <Chip tone="info">Сравнение предпочтений</Chip>}
      </div>

      <section className="relative rounded-field bg-surface-raised p-3">
        <h4 className="mb-2 text-sm font-semibold text-subtle">Почему в подборке</h4>
        <ul className="flex flex-wrap gap-1.5 text-xs">
          {factors.map((factor) => (
            <li key={factor} className="rounded-chip bg-white/10 px-2.5 py-1">
              {factor}
            </li>
          ))}
          {card.strict_filter_warnings?.map((warning) => (
            <li key={warning} className="rounded-chip bg-danger/30 px-2.5 py-1">
              {warning}
            </li>
          ))}
        </ul>
      </section>

      {features.length > 0 && (
        <section className="relative rounded-field bg-surface-raised p-3">
          <h4 className="mb-2 text-sm font-semibold text-subtle">Совпадение по требованиям</h4>
          <ul className="flex flex-col gap-1 text-sm">
            {features.map((feature) => (
              <li key={`${feature.group}:${feature.key}`}>{describeFeature(feature)}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="relative rounded-field bg-surface-raised p-3">
        <h4 className="mb-2 text-sm font-semibold text-subtle">Из чего сложилась оценка</h4>
        <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-sm">
          {scoreChannels(explanation).map((channel) => (
            <div key={channel.key} className="contents">
              <dt className="text-muted">{channel.label}</dt>
              <dd>+{Math.round(channel.value * 100)}</dd>
            </div>
          ))}
          <dt className="text-muted">Ожидания по ЗП</dt>
          <dd>{formatSalaryRange(card.salary.from_rub, card.salary.to_rub)}</dd>
          {fsp.map((item) => (
            <div key={item.subject} className="contents">
              <dt className="text-muted">ФСП: {item.subject}</dt>
              <dd>{CLAIM_STATUS_LABEL[item.status]}</dd>
            </div>
          ))}
        </dl>
        {explanation.semantic_unavailable_reason && (
          <p className="mt-2 text-xs text-muted" title={explanation.semantic_unavailable_reason}>
            Смысловая близость не учтена: нет описания или несовместимая модель эмбеддингов.
          </p>
        )}
      </section>
    </article>
  );
}

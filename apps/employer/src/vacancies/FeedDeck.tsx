import type { FeedCard, FeedPage } from "@comit/shared";
import { ApiError } from "@comit/api-client";
import { AnimatePresence, Button, Chip, EASE, Icon, MatchBadge, REVEAL, StateMessage, cx, motion } from "@comit/ui-kit";
import { useMotionValue, useTransform } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, describeError, errorDetail, isUnauthorized, newIdempotencyKey } from "../api";
import { useSession } from "../auth/session";
import { CLAIM_STATUS_LABEL, GRADE_LABEL, describeFeature, filterFactorLabels, matchPercent, scoreChannels } from "../lib/explain";
import { SPECIALIZATION_LABEL, candidateLabel, formatSalaryRange } from "../lib/format";
import { PortraitGlow } from "./PortraitGlow";

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
  /** +1 like (flies right), -1 dislike (flies left); read by the exiting card. */
  const [exitDirection, setExitDirection] = useState<1 | -1>(1);

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
      setExitDirection(action === "like" ? 1 : -1);
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
        className="relative w-full max-w-[31.8125rem] outline-none"
        tabIndex={0}
        aria-keyshortcuts="ArrowLeft ArrowRight"
        onKeyDown={(event) => {
          if (event.key === "ArrowRight") void decide("like");
          if (event.key === "ArrowLeft") void decide("dislike");
        }}
      >
        {/* Stack behind the top card (design: -12.75° and +8.5°); fades out as the portion runs low. */}
        <motion.div
          aria-hidden
          animate={{ opacity: cards[2] ? 1 : 0, rotate: -12.75 }}
          transition={REVEAL}
          className="glass-deck absolute inset-0 -translate-x-[1.5rem] -translate-y-[0.75rem] rounded-[1.25rem]"
        />
        <motion.div
          aria-hidden
          animate={{ opacity: cards[1] ? 1 : 0, rotate: 8.5 }}
          transition={REVEAL}
          className="glass-deck absolute inset-0 translate-x-[1.5rem] -translate-y-[0.5rem] rounded-[1.25rem]"
        />
        <AnimatePresence initial={false} custom={exitDirection} mode="popLayout">
          <SwipeCard key={top.exposure_id} card={top} disabled={busy} onSwipe={(action) => void decide(action)} />
        </AnimatePresence>
      </div>
      <p className="text-center text-sm text-muted">Свайп или ← / → · в порции ещё {cards.length - 1}</p>
      {actionError && (
        <p role="alert" className="text-center text-sm text-danger-soft">
          {actionError}
        </p>
      )}
    </section>
  );
}

/** Fly-out of a decided card in the swipe direction (`custom` = +1 right / -1 left). */
const cardVariants = {
  enter: { opacity: 0, scale: 0.94, y: 24 },
  center: { opacity: 1, scale: 1, y: 0, x: 0, rotate: 0, transition: REVEAL },
  exit: (direction: number) => ({ x: direction * 640, rotate: direction * 22, opacity: 0, transition: { duration: 0.35, ease: EASE } }),
};

/**
 * Draggable top card (design 509×603 glass): label, chips, three explanation boxes and the 78px
 * action row — skip, like (save to shortlist), PDF résumé. A drag past SWIPE_THRESHOLD or a fast
 * flick commits like (right) or dislike (left); otherwise the card springs back.
 */
function SwipeCard({ card, disabled, onSwipe }: { card: FeedCard; disabled: boolean; onSwipe: (action: "like" | "dislike") => void }) {
  const x = useMotionValue(0);
  const rotate = useTransform(x, [-300, 0, 300], [-14, 0, 14]);
  const likeOpacity = useTransform(x, [30, SWIPE_THRESHOLD], [0, 1]);
  const skipOpacity = useTransform(x, [-SWIPE_THRESHOLD, -30], [1, 0]);

  const { explanation, category } = card;
  const name = candidateLabel(card.display_name, card.candidate_id);
  const factors = filterFactorLabels(card.filter_factors);
  const features = explanation.top_matched_features.slice(0, 3);
  const fsp = card.external_evidence?.filter((item) => item.kind === "fsp") ?? [];
  const actionButton =
    "inline-flex size-16 cursor-pointer items-center justify-center rounded-[1.25rem] bg-surface-button text-text transition-[background-color,scale] duration-150 active:scale-90 disabled:opacity-50 lg:size-[4.9rem]";
  return (
    <motion.article
      custom={0}
      variants={cardVariants}
      initial="enter"
      animate="center"
      exit="exit"
      drag={disabled ? false : "x"}
      dragSnapToOrigin
      dragElastic={0.6}
      onDragEnd={(_, info) => {
        if (info.offset.x > SWIPE_THRESHOLD || info.velocity.x > 700) onSwipe("like");
        else if (info.offset.x < -SWIPE_THRESHOLD || info.velocity.x < -700) onSwipe("dislike");
      }}
      style={{ x, rotate }}
      whileDrag={{ cursor: "grabbing" }}
      className="glass-deck relative flex min-h-[32rem] cursor-grab touch-pan-y flex-col overflow-hidden rounded-[1.25rem] px-5 pt-6 pb-5 select-none lg:min-h-[37.6875rem] lg:px-[1.875rem] lg:pt-[2.5rem] lg:pb-[1.5rem]"
    >
      <PortraitGlow name={name} />
      <motion.span
        aria-hidden
        style={{ opacity: likeOpacity }}
        className="pointer-events-none absolute top-6 right-6 z-10 rotate-12 rounded-[0.75rem] border-4 border-success-strong px-3 py-1 font-display text-xl text-success-strong lg:text-[1.75rem]"
      >
        В шортлист
      </motion.span>
      <motion.span
        aria-hidden
        style={{ opacity: skipOpacity }}
        className="pointer-events-none absolute top-6 left-6 z-10 -rotate-12 rounded-[0.75rem] border-4 border-danger px-3 py-1 font-display text-xl text-danger lg:text-[1.75rem]"
      >
        Пропустить
      </motion.span>
      <div className="relative flex items-start justify-between gap-3">
        <h3 className="truncate font-display text-2xl leading-none lg:text-[2rem]">{name}</h3>
        <MatchBadge percent={matchPercent(explanation)} shape="pill" className="shrink-0" />
      </div>
      <div className="relative mt-4 flex flex-wrap gap-[0.625rem] lg:mt-[1.4rem]">
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

      <section className="relative mt-4 rounded-[1.25rem] bg-field p-4 lg:mt-[1.4rem] lg:min-h-[10.0625rem] lg:p-[1.25rem]">
        <h4 className="sr-only">Почему в подборке</h4>
        <ul className="flex flex-wrap gap-x-3 gap-y-1 text-sm font-medium text-white/60 lg:text-[1.125rem]">
          {factors.map((factor) => (
            <li key={factor}>• {factor}</li>
          ))}
          {card.strict_filter_warnings?.map((warning) => (
            <li key={warning} className="text-danger-soft">
              • {warning}
            </li>
          ))}
        </ul>
        {features.length > 0 && (
          <ul className="mt-2 flex flex-col gap-1 text-sm font-medium text-white/80 lg:text-[1.125rem]">
            {features.map((feature) => (
              <li key={`${feature.group}:${feature.key}`}>{describeFeature(feature)}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="relative mt-[0.5625rem] flex min-h-[3.5rem] items-center rounded-[1.25rem] bg-field px-4 text-sm font-medium text-white/60 lg:min-h-[4.4375rem] lg:px-[1.25rem] lg:text-[1.125rem]">
        <h4 className="sr-only">Из чего сложилась оценка</h4>
        <p>{scoreChannels(explanation).map((channel) => `${channel.label} +${Math.round(channel.value * 100)}`).join(" · ")}</p>
      </section>
      <section className="relative mt-[0.5625rem] flex min-h-[3.5rem] items-center rounded-[1.25rem] bg-field px-4 text-sm font-medium text-white/60 lg:min-h-[4.4375rem] lg:px-[1.25rem] lg:text-[1.125rem]">
        <h4 className="sr-only">Ожидания и подтверждения</h4>
        <p>
          Ожидания {formatSalaryRange(card.salary.from_rub, card.salary.to_rub)}
          {fsp.map((item) => ` · ФСП ${item.subject}: ${CLAIM_STATUS_LABEL[item.status]}`).join("")}
          {explanation.semantic_unavailable_reason && " · без смысловой близости"}
        </p>
      </section>

      <div className="relative mt-auto flex justify-center gap-[2.125rem] pt-5">
        <button type="button" onClick={() => onSwipe("dislike")} disabled={disabled} aria-label="Пропустить (←)" className={cx(actionButton, "hover:bg-danger")}>
          <Icon name="close" size={36} />
        </button>
        <button type="button" onClick={() => onSwipe("like")} disabled={disabled} aria-label="В шортлист (→)" className={cx(actionButton, "hover:bg-success-strong")}>
          <Icon name="plus" size={36} />
        </button>
        <a href={card.resume_url} target="_blank" rel="noreferrer" aria-label="PDF резюме" className={cx(actionButton, "hover:bg-white/15")}>
          <Icon name="resumes" size={36} />
        </a>
      </div>
    </motion.article>
  );
}

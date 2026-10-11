"""Verified FSP promotion bounded independently from the immutable main/R0 scores."""

import logging
import math
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, TypeVar
from uuid import UUID

from matching_engine.combine_models import CandidateCard, MatchCandidate
from matching_engine.models import Contract

logger = logging.getLogger(__name__)
POLICY_VERSION = "near-equivalent-promotion-v1"
FSP_BOOST_MAX = 0.02
RATING_CEILING = 2000
RANK_WEIGHTS = {"MS": 0.12, "КМС": 0.09, "I": 0.06, "II": 0.04, "III": 0.03}


@dataclass(frozen=True)
class PromotionPolicy:
    """Separate delivery policy; never modifies main policy or learned feature inputs."""

    mode: Literal["off", "shadow", "active"] = "shadow"
    allow_demo: bool = False
    max_base_gap: float = FSP_BOOST_MAX
    version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        if self.mode not in ("off", "shadow", "active") or self.max_base_gap != FSP_BOOST_MAX:
            raise ValueError("unsupported promotion policy")

    def snapshot(self) -> dict:
        """Return the policy identity included in every durable rerank generation."""
        return {**asdict(self), "reward_week": datetime.now(UTC).date().isocalendar()[:2]}


def promotion_policy() -> PromotionPolicy:
    """Read explicit rollout mode; demo facts are accepted only in development."""
    mode = os.getenv("MATCHING_PROMOTION_MODE", "shadow")
    if mode not in ("off", "shadow", "active"):
        raise ValueError("invalid MATCHING_PROMOTION_MODE")
    return PromotionPolicy(mode=mode, allow_demo=(os.getenv("APP_ENV") == "development"
                           and os.getenv("MATCHING_PROMOTION_ALLOW_DEMO") == "true"))


def compute_fsp_boost(profile: MatchCandidate, *, allow_demo: bool = False) -> dict:
    """Use confirmed server-projected facts only; malformed, absent or demo facts earn zero.

    Multiple evidence rows never stack: the strongest usable fact wins. No account
    identifiers, event descriptions or other private data enter explanations.
    """
    best = {"bonus": 0.0, "policy_version": POLICY_VERSION, "factors": {},
            "explanation": "fsp_absent_or_unverified"}
    for evidence in profile.external_evidence:
        value = evidence.verified_value
        if evidence.kind != "fsp" or not isinstance(value, dict):
            continue
        demo = value.get("demo_registry") is True
        if demo and not allow_demo:
            continue
        if evidence.status != "confirmed" and not (demo and allow_demo and evidence.status == "partial"):
            continue
        rating, rank = value.get("rating"), value.get("rank")
        if isinstance(rating, bool) or not isinstance(rating, (int, float)) or not math.isfinite(rating) or rating <= 0:
            continue
        normalized = min(1.0, rating / RATING_CEILING)
        rank_bonus = RANK_WEIGHTS.get(rank, 0.0) if isinstance(rank, str) else 0.0
        bonus = round(FSP_BOOST_MAX * (0.5 * normalized + 0.5 * rank_bonus), 8)
        if bonus > best["bonus"]:
            best = {"bonus": bonus, "policy_version": POLICY_VERSION,
                    "factors": {"normalized_rating": normalized, "rank_bonus": rank_bonus,
                                "demo_registry": value.get("demo_registry") is True},
                    "explanation": "fsp_verified_achievements"}
    return best


def compute_weekly_boost(profile: MatchCandidate, *, now: datetime | None = None) -> float:
    """Accept one server-earned reward for the current UTC week, expiring next Monday.

    Evidence comes from the authenticated API outbox, not editable metadata.
    UTC week is also part of durable ranking identity, invalidating expired runs.
    """
    now = now or datetime.now(UTC)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)
    for evidence in profile.external_evidence:
        value = evidence.verified_value
        if (evidence.kind != "weekly_challenge" or evidence.status != "confirmed" or not isinstance(value, dict)
                or value.get("provider") != "comit_weekly_challenge:v1"):
            continue
        try:
            completed = datetime.fromisoformat(str(value.get("completed_at"))).astimezone(UTC)
            expires = datetime.fromisoformat(str(value.get("expires_at"))).astimezone(UTC)
            if not str(value.get("completed_at")).endswith(("Z", "+00:00")) or not str(value.get("expires_at")).endswith(("Z", "+00:00")):
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        if week_start <= completed <= now < expires == week_end:
            return 0.005
    return 0.0


class PromotionExplanation(Contract):
    """Public bounded ordering factors, separate from additive model contributions."""

    policy_version: str
    mode: Literal["off", "shadow", "active"]
    fsp_bonus: float
    weekly_bonus: float
    combined_bonus: float
    max_base_gap: float
    demo_registry: bool


def promotion_explanation(profile: MatchCandidate, policy: PromotionPolicy) -> PromotionExplanation:
    """Describe eligible ordering bonuses without contact/provider account identifiers."""
    fsp = compute_fsp_boost(profile, allow_demo=policy.allow_demo)
    weekly = compute_weekly_boost(profile)
    return PromotionExplanation(policy_version=policy.version, mode=policy.mode,
        fsp_bonus=fsp["bonus"], weekly_bonus=weekly, combined_bonus=min(FSP_BOOST_MAX, fsp["bonus"] + weekly),
        max_base_gap=policy.max_base_gap, demo_registry=fsp["factors"].get("demo_registry", False))


class RankedItem(Protocol):
    """Minimal immutable rerank row interface; no new public candidate contract."""

    candidate_id: UUID
    kind: str


Item = TypeVar("Item", bound=RankedItem)


def promote(items: list[Item], candidates: list[MatchCandidate], cards: list[CandidateCard],
            policy: PromotionPolicy) -> list[Item]:
    """Reorder only near-equivalent existing rows within their original delivery kind.

    A row crosses another only through an adjacent swap and only if their main
    score gap is <= 0.02 and its bounded bonus compensates its base deficit.
    Hence no newly inverted pair can exceed 0.02, even across many swaps; strict
    exclusions, shortlist membership, score values and reserve membership persist.
    Equal bonuses preserve the complete previous order. Shadow logs counts only.
    """
    if policy.mode == "off":
        return items
    profiles = {row.candidate_id: row for row in candidates}
    base = {card.candidate_id: card.explanation.score for card in cards}
    if len(base) != len(cards) or len({row.candidate_id for row in items}) != len(items):
        raise ValueError("duplicate promotion candidate")
    bonuses = {row.candidate_id: compute_fsp_boost(profiles[row.candidate_id],
               allow_demo=policy.allow_demo)["bonus"] for row in items}
    bonuses = {key: min(FSP_BOOST_MAX, value + compute_weekly_boost(profiles[key]))
               for key, value in bonuses.items()}
    if not any(bonuses.values()):
        return items
    output = list(items)
    swaps = 0
    for index in range(1, len(output)):
        position = index
        while position > 0:
            left, right = output[position - 1], output[position]
            a, b = left.candidate_id, right.candidate_id
            if (left.kind != right.kind or bonuses[b] <= bonuses[a]
                    or abs(base[a] - base[b]) > policy.max_base_gap
                    or base[b] + bonuses[b] <= base[a] + bonuses[a]):
                break
            output[position - 1], output[position] = right, left
            swaps += 1
            position -= 1
    logger.info("candidate promotion mode=%s eligible=%d swaps=%d bound=%.3f",
                policy.mode, sum(value > 0 for value in bonuses.values()), swaps, policy.max_base_gap)
    return output if policy.mode == "active" else items

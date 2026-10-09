"""Draft FSP boost for the matching score: fully specified, deliberately not wired.

The component is complete - formula, explanation factors, bounds and tests -
so that connecting it after the hackathon is a one-line integration plus a
golden-snapshot regeneration. It is intentionally NOT imported by the live
combine scoring: without the real FSP registry and real reactions, plugging a
boost calibrated on two synthetic registry entries would risk degrading the
ranking while looking confident. The integration point is documented in
docs/integration.md; enabling it changes ScoringPolicy, which invalidates the
packaged R0 artifact by design (the artifact must be retrained against the
new policy, keeping the no-silent-drift guarantee).

Formula (fsp-boost-draft-v1): a bounded additive bonus to the main score,
applied after the standard components, within [0, FSP_BOOST_MAX]:

    normalized = clamp(rating / RATING_CEILING, 0, 1)          # 1876 -> ~0.94
    rank_bonus = RANK_WEIGHTS.get(rank, 0.0)                   # MS .12 ... III .03
    bonus = FSP_BOOST_MAX * (0.5 * normalized + 0.5 * rank_bonus)

Every candidate without a verified FSP block receives exactly 0.0, so the
mandatory "no FSP history" state (TZ §2) is a strict no-op. Contributions are
returned as structured explanation factors, never hand-written strings.
"""

import math
from typing import Any

POLICY_VERSION = "fsp-boost-draft-v1"
FSP_BOOST_MAX = 0.06
RATING_CEILING = 2000
RANK_WEIGHTS = {"MS": 0.12, "КМС": 0.09, "I": 0.06, "II": 0.04, "III": 0.03}
NOT_CONNECTED = True


def compute_fsp_boost(profile: Any) -> dict[str, Any]:
    """Return the bounded boost, its factors and a stable explanation.

    Accepts any object (or mapping) exposing fsp: {rank, rating} and None -
    candidates without a verified FSP block receive a zero boost with an
    explicit reason, which keeps the no-history case a strict no-op.
    """
    fsp = getattr(profile, "fsp", None)
    if fsp is None and isinstance(profile, dict):
        fsp = profile.get("fsp")
    if fsp is None:
        return {"bonus": 0.0, "policy_version": POLICY_VERSION, "connected": False,
                "factors": {}, "explanation": "fsp_absent_no_history"}
    rating = float(getattr(fsp, "rating", 0) or 0)
    rank = str(getattr(fsp, "rank", "") or "")
    normalized = max(0.0, min(1.0, rating / RATING_CEILING))
    rank_bonus = RANK_WEIGHTS.get(rank, 0.0)
    if not math.isfinite(normalized) or normalized <= 0.0:
        return {"bonus": 0.0, "policy_version": POLICY_VERSION, "connected": False,
                "factors": {"rank": rank, "rating": rating},
                "explanation": "fsp_rating_not_usable"}
    bonus = FSP_BOOST_MAX * (0.5 * normalized + 0.5 * rank_bonus)
    return {
        "bonus": round(bonus, 6),
        "policy_version": POLICY_VERSION,
        "connected": False,
        "factors": {"normalized_rating": round(normalized, 6),
                    "rank_bonus": rank_bonus, "rank": rank, "rating": rating},
        "explanation": "fsp_verified_achievements",
    }


__all__ = ["FSP_BOOST_MAX", "POLICY_VERSION", "RANK_WEIGHTS", "RATING_CEILING", "compute_fsp_boost"]

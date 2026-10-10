"""Tests for the draft FSP boost component (not wired into the live scoring)."""

from matching_engine.fsp_boost import (
    FSP_BOOST_MAX,
    POLICY_VERSION,
    compute_fsp_boost,
)


def _profile(rank: str | None = "I", rating: int | None = 1876) -> dict:
    """Plain snapshot mapping: the boost reads fsp from any mapping or object."""
    profile: dict = {"source_revision": 1, "city": "Москва", "fsp": None}
    if rank is not None and rating is not None:
        profile["fsp"] = {"fsp_id": "fsp-001", "rank": rank, "rating": rating,
                          "events": [{"title": "Кубок", "year": 2025, "place": 7}],
                          "certificates": ["Школа ФСП"], "verified_at": "2026-10-08T12:00:00+00:00"}
    return profile


def test_absent_fsp_is_a_strict_no_op() -> None:
    """The mandatory no-history state receives exactly zero boost."""
    result = compute_fsp_boost(_profile(rank=None, rating=None))
    assert result["bonus"] == 0.0
    assert result["explanation"] == "fsp_absent_no_history"
    assert result["connected"] is False


def test_boost_is_bounded_and_ordered_by_strength() -> None:
    """MS with a high rating caps near the maximum; weaker ranks stay below it."""
    strong = compute_fsp_boost(_profile(rank="MS", rating=2000))["bonus"]
    middle = compute_fsp_boost(_profile(rank="I", rating=1876))["bonus"]
    weak = compute_fsp_boost(_profile(rank="III", rating=600))["bonus"]
    assert 0.0 < weak < middle < strong <= FSP_BOOST_MAX
    assert abs(strong - 0.06 * (0.5 + 0.5 * 0.12)) < 1e-9


def test_unknown_rank_and_zero_rating_degrade_safely() -> None:
    """Unknown ranks contribute no rank bonus; zero ratings collapse to zero."""
    unknown = compute_fsp_boost(_profile(rank="X", rating=1500))
    assert unknown["factors"]["rank_bonus"] == 0.0 and unknown["bonus"] > 0
    zero = compute_fsp_boost(_profile(rank="I", rating=0))
    assert zero["bonus"] == 0.0 and zero["explanation"] == "fsp_rating_not_usable"


def test_component_is_declared_not_connected() -> None:
    """The draft namespace states its disconnected status in every output."""
    assert POLICY_VERSION == "fsp-boost-draft-v1"
    result = compute_fsp_boost(_profile())
    assert result["connected"] is False
    source = __import__("pathlib").Path(__file__).parents[1] / "src" / "matching_engine" / "fsp_boost.py"
    text = source.read_text(encoding="utf-8")
    assert "deliberately not wired" in text and "never imported" not in text
    assert "combine scoring" in text

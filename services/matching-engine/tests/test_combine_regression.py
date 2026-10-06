"""Golden regression of the deterministic combine scoring surface.

A fixed synthetic corpus (candidates x vacancies) is scored with the current
`ScoringPolicy` and compared with a committed golden snapshot
(`fixtures/scoring_regression_golden.json`): strict-filter reasons and factors,
full score breakdowns, filter counts and the exact top-k order. Any change in
weights, evidence confidence, semantic correction, gating or tie-breaks lands
here as an explicit diff instead of silently moving production rankings.

The corpus is built from the canonical strict registry (`initial_schema`), so an
append-only registry extension does not shift existing coordinates. Floats are
rounded to 9 decimals purely to absorb cross-platform last-bit noise; the
arithmetic itself is deterministic.

Regenerate deliberately after an intended scoring change and explain the diff
in the commit body (AGENTS section 6):

    UPDATE_GOLDEN=1 pytest services/matching-engine/tests/test_combine_regression.py
"""

import hashlib
import json
import math
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from matching_engine.combine_models import MatchCandidate, ScoringPolicy
from matching_engine.combine_scoring import (
    policy_hash,
    select_candidates,
    strict_filter,
)
from matching_engine.models import (
    Category,
    ClaimStatus,
    CoordinateEvidence,
    Direction,
    Grade,
    GradeStatus,
    SalaryRange,
)
from matching_engine.strict import initial_schema
from matching_engine.vacancy_models import VacancyReady, WorkType

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "scoring_regression_golden.json"
SCHEMA = initial_schema()
POSITIONS = {coordinate.key: coordinate.index for coordinate in SCHEMA.coordinates}
POLICY = ScoringPolicy()
SEMANTIC_SPACE = "regression-space-v1"
SEMANTIC_MODEL = "regression-semantic:v1"
PUBLISHED_AT = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
TOP_K = 50
OWNER_ID = uuid5(NAMESPACE_URL, "https://comit.regression/owner")

Claims = dict[str, tuple[int, ClaimStatus]]


def unit_vector(components: dict[int, float]) -> list[float]:
    """Build a deterministic 640D unit vector with non-zero entries only at fixed indices."""
    raw = [components.get(index, 0.0) for index in range(640)]
    norm = math.sqrt(sum(value * value for value in raw))
    return [value / norm for value in raw]


def strict_projection(claims: Claims) -> tuple[list[float], list[CoordinateEvidence]]:
    """Mirror the production pool projection: declared levels /7, rejected and absent stay zero."""
    vector = [0.0] * len(SCHEMA.coordinates)
    evidence = [
        CoordinateEvidence(index=coordinate.index, group=coordinate.group, key=coordinate.key,
                           status=None, level=0)
        for coordinate in SCHEMA.coordinates
    ]
    for key, (level, status) in claims.items():
        index = POSITIONS[key]
        evidence[index] = CoordinateEvidence(index=index, group=SCHEMA.coordinates[index].group,
                                             key=key, status=status, level=level)
        if status != "rejected":
            vector[index] = level / 7
    return vector, evidence


def candidate(name: str, *, claims: Claims | None = None, city: str = "Москва",
              work_types: list[WorkType] | None = None,
              salary: tuple[int, int] = (160000, 240000), rating: float = 0.5,
              grade: Grade = "middle", grade_status: GradeStatus = "approved",
              specialization: Direction = "backend", semantic: list[float] | None = None,
              semantic_space: str = SEMANTIC_SPACE, schema_version: int = 1,
              relocation_ready: bool | None = None,
              relocation_cities: list[str] | None = None) -> MatchCandidate:
    """Build one corpus candidate; defaults represent a passable onsite Moscow backend.

    Here `work_types=None` means every format is accepted; the unknown-formats
    case is modelled explicitly with `model_copy(update={"work_types": None})`.
    """
    vector, evidence = strict_projection(claims or {})
    return MatchCandidate(
        candidate_id=uuid5(NAMESPACE_URL, f"https://comit.regression/candidates/{name}"),
        profile_revision=1,
        display_name=f"Regression {name}",
        city=city,
        salary=SalaryRange(from_rub=salary[0], to_rub=salary[1]),
        category=Category(specialization=specialization, grade=grade, grade_status=grade_status),
        rank=5,
        confirmed_strength=7,
        strict_vector=vector,
        strict_evidence=evidence,
        strict_schema_version=schema_version,
        strict_schema_hash=SCHEMA.schema_hash,
        semantic_vector=semantic if semantic is not None else unit_vector({0: 1.0}),
        semantic_model_version=SEMANTIC_MODEL,
        semantic_space_id=semantic_space,
        published_at=PUBLISHED_AT,
        resume_url=f"/internal/candidates/{name}/resume.pdf",
        class_size=12,
        rating_score=rating,
        work_types=["onsite", "remote", "hybrid"] if work_types is None else work_types,
        relocation_ready=relocation_ready,
        relocation_cities=relocation_cities or [],
    )


def vacancy(name: str, *, city: str, work_type: WorkType, zp: tuple[int, int],
            class_key: Direction | None, targets: Claims | None = None,
            semantic: list[float] | None = None) -> VacancyReady:
    """Build one corpus vacancy; masked targets are explicit relative coordinates /7."""
    vector = [0.0] * len(SCHEMA.coordinates)
    mask = [False] * len(SCHEMA.coordinates)
    for key, (level, _status) in (targets or {}).items():
        index = POSITIONS[key]
        vector[index] = level / 7
        mask[index] = True
    payload = {
        "vacancy_id": uuid5(NAMESPACE_URL, f"https://comit.regression/vacancies/{name}"),
        "revision": 1, "source_revision": 1, "owner_id": OWNER_ID, "status": "open",
        "zp_min": zp[0], "zp_max": zp[1], "daily": "5/2", "type": work_type, "city": city,
        "class": class_key,
        "build_mode": "mixed" if class_key and targets else ("class_only" if class_key else "requirements_only"),
        "strict_vector": vector, "requirement_mask": mask,
        "strict_schema_version": 1, "strict_schema_hash": SCHEMA.schema_hash,
        "semantic_vector": semantic,
        "semantic_model_version": SEMANTIC_MODEL if semantic else None,
        "semantic_space_id": SEMANTIC_SPACE if semantic else None,
        "semantic_available": semantic is not None,
        "published_at": PUBLISHED_AT,
    }
    return VacancyReady.model_validate(payload)


EXACT: Claims = {"python": (6, "confirmed"), "go": (4, "confirmed"), "sql": (5, "confirmed")}
EXACT_TEAM: Claims = {**EXACT, "teamwork": (0, "confirmed")}

VACANCY_TARGETS: Claims = {"python": (6, "confirmed"), "go": (4, "confirmed"),
                           "sql": (5, "confirmed"), "teamwork": (0, "confirmed")}

VACANCIES: dict[str, VacancyReady] = {
    # Onsite Moscow with explicit targets including a zero target; semantic correction active.
    "onsite-mixed": vacancy("onsite-mixed", city="Москва", work_type="onsite", zp=(150000, 250000),
                            class_key="backend", targets=VACANCY_TARGETS,
                            semantic=unit_vector({0: 3.0, 1: 1.0})),
    # Remote requirements-only vacancy: no class constraint, no bio (semantic absent).
    "remote-requirements": vacancy("remote-requirements", city="Санкт-Петербург",
                                   work_type="remote", zp=(100000, 200000), class_key=None,
                                   targets={"sql": (3, "confirmed")}),
    # Hybrid relocation destination with a maximum-level target and semantic correction active.
    "hybrid-relocation": vacancy("hybrid-relocation", city="Новосибирск", work_type="hybrid",
                                 zp=(200000, 300000), class_key="backend",
                                 targets={"python": (7, "confirmed")},
                                 semantic=unit_vector({0: 1.0, 1: 1.0})),
    # Hybrid vacancy reached through the city alias registry; semantic absent.
    "hybrid-alias-city": vacancy("hybrid-alias-city", city="СПб", work_type="hybrid",
                                 zp=(180000, 260000), class_key="backend",
                                 targets={"python": (7, "confirmed")}),
}

CANDIDATES: list[MatchCandidate] = [
    candidate("backend-confirmed-match", claims=EXACT_TEAM, rating=0.8),
    candidate("backend-partial-lower",
              claims={"python": (4, "partial"), "go": (4, "confirmed"),
                      "sql": (5, "self_reported")}, rating=0.6),
    candidate("backend-self-reported-grade", claims=EXACT, rating=0.9, grade_status="self_reported"),
    candidate("backend-semantic-negative", claims=EXACT_TEAM, rating=0.7,
              semantic=unit_vector({0: -4.0, 1: 1.0})),
    candidate("backend-salary-edge-included", salary=(250000, 400000), rating=0.5,
              claims={"python": (5, "pending"), "go": (4, "confirmed"),
                      "sql": (5, "self_reported")}),
    candidate("backend-salary-disjoint", salary=(300000, 400000), rating=0.95, claims=EXACT,
              relocation_ready=True, relocation_cities=["Новосибирск"]),
    candidate("frontend-class-mismatch", specialization="frontend", grade="senior", rating=0.85,
              claims={"python": (6, "confirmed"), "sql": (5, "confirmed")}),
    candidate("worktype-remote-only", work_types=["remote"], rating=0.55, claims=EXACT),
    # Unknown work formats are a distinct strict-filter reason, never an implicit "any format".
    candidate("worktype-unknown", claims=EXACT, rating=0.5).model_copy(update={"work_types": None}),
    candidate("relocation-ready", city="Казань", relocation_ready=True,
              relocation_cities=["Новосибирск", "СПб"], rating=0.75, claims=EXACT_TEAM),
    candidate("relocation-unknown", city="Казань", rating=0.7, claims=EXACT),
    candidate("relocation-not-ready", city="Казань", relocation_ready=False, rating=0.65,
              claims=EXACT),
    candidate("relocation-destination-mismatch", city="Казань", relocation_ready=True,
              relocation_cities=["Сочи"], rating=0.6, claims=EXACT),
    candidate("city-prefix-normalized", city="г. Москва", rating=0.65, claims=EXACT),
    candidate("city-alias-spb", city="Saint Petersburg", salary=(190000, 250000), rating=0.7,
              claims={"python": (6, "confirmed")}),
    candidate("schema-v2-mismatch", schema_version=2, rating=0.9, claims=EXACT),
    candidate("semantic-space-legacy", semantic_space="legacy-space-v0", rating=0.8, claims=EXACT),
    candidate("zero-target-rejected",
              claims={**EXACT, "teamwork": (5, "rejected")}, rating=0.6),
    candidate("missing-all-evidence", claims={}, rating=0.4),
    candidate("excluded-elsewhere", claims=EXACT_TEAM, rating=0.9, city="Казань",
              relocation_ready=True, relocation_cities=["Новосибирск", "СПб"]),
    candidate("tie-alpha", claims=EXACT, grade="junior", rating=0.5),
    candidate("tie-beta", claims=EXACT, grade="junior", rating=0.5),
]

CANDIDATE_BY_NAME = {entry.display_name.removeprefix("Regression "): entry for entry in CANDIDATES}

# Only the onsite vacancy excludes a candidate: the same profile passes everywhere else.
EXCLUDED_IDS: dict[str, set[UUID]] = {
    "onsite-mixed": {CANDIDATE_BY_NAME["excluded-elsewhere"].candidate_id},
}


def rounded(value: object) -> object:
    """Round every float in a JSON-like structure to 9 decimals for stable comparison."""
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, list):
        return [rounded(item) for item in value]
    if isinstance(value, dict):
        return {key: rounded(item) for key, item in value.items()}
    return value


def corpus_digest() -> str:
    """Fingerprint the full corpus and policy so silent corpus edits cannot pass review."""
    payload = {
        "policy": POLICY.model_dump(mode="json"),
        "candidates": [entry.model_dump(mode="json") for entry in CANDIDATES],
        "vacancies": {name: entry.model_dump(mode="json") for name, entry in VACANCIES.items()},
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def selection_snapshot(name: str) -> dict:
    """Run the deterministic main selection for one corpus vacancy and round the result."""
    result = select_candidates(VACANCIES[name], CANDIDATES, POLICY, TOP_K,
                               excluded_ids=EXCLUDED_IDS.get(name))
    return rounded(result.model_dump(mode="json"))


def filter_decision_snapshot(name: str) -> dict[str, dict]:
    """Capture explicit filter decisions for every corpus candidate and one vacancy."""
    excluded = EXCLUDED_IDS.get(name, set())
    return {
        str(entry.candidate_id): rounded(strict_filter(
            VACANCIES[name], entry, excluded=entry.candidate_id in excluded).model_dump(mode="json"))
        for entry in CANDIDATES
    }


def build_golden() -> dict:
    """Assemble the complete golden document; must stay free of timestamps for determinism."""
    return {
        "meta": {
            "policy_version": POLICY.version,
            "policy_hash": policy_hash(POLICY),
            "corpus_digest": corpus_digest(),
            "strict_schema_version": SCHEMA.version,
            "strict_schema_hash": SCHEMA.schema_hash,
            "candidate_count": len(CANDIDATES),
            "top_k": TOP_K,
        },
        "vacancies": {
            name: {"selection": selection_snapshot(name),
                   "filter_decisions": filter_decision_snapshot(name)}
            for name in sorted(VACANCIES)
        },
    }


def first_difference(expected: object, actual: object, path: str) -> str | None:
    """Locate the first differing path between golden and freshly computed structures."""
    if type(expected) is not type(actual) or (
            isinstance(expected, dict) and set(expected) != set(actual)):
        return path
    if isinstance(expected, dict):
        for key in expected:
            difference = first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference:
                return difference
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path} (length {len(expected)} != {len(actual)})"
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual, strict=True)):
            difference = first_difference(expected_item, actual_item, f"{path}[{index}]")
            if difference:
                return difference
        return None
    return None if expected == actual else f"{path} ({expected!r} != {actual!r})"


@pytest.fixture(scope="module")
def golden() -> Iterator[dict]:
    """Load the committed golden snapshot, or regenerate it under UPDATE_GOLDEN=1."""
    if os.environ.get("UPDATE_GOLDEN") == "1":
        document = build_golden()
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        pytest.skip("golden regenerated; review the diff and explain it in the commit body")
    if not GOLDEN_PATH.exists():
        pytest.fail(f"golden snapshot is missing: {GOLDEN_PATH} (run with UPDATE_GOLDEN=1 once)")
    yield json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_golden_tracks_current_policy_and_corpus(golden: dict) -> None:
    """A scoring or corpus change must regenerate the golden explicitly, never silently."""
    meta = golden["meta"]
    assert meta["policy_hash"] == policy_hash(POLICY), (
        "ScoringPolicy changed: rerun with UPDATE_GOLDEN=1 and explain the ranking diff "
        "in the commit body (AGENTS section 6)")
    assert meta["corpus_digest"] == corpus_digest(), (
        "Regression corpus changed: rerun with UPDATE_GOLDEN=1 and review the new expectations")
    assert meta["policy_version"] == POLICY.version
    assert meta["strict_schema_hash"] == SCHEMA.schema_hash
    assert meta["candidate_count"] == len(CANDIDATES) and meta["top_k"] == TOP_K


@pytest.mark.parametrize("name", sorted(VACANCIES))
def test_main_selection_matches_golden(golden: dict, name: str) -> None:
    """Counts, filter reasons aggregate and the full ordered top-k must equal the golden."""
    snapshot = golden["vacancies"][name]["selection"]
    fresh = selection_snapshot(name)
    difference = first_difference(snapshot, fresh, name)
    assert difference is None, f"main selection regressed at {difference}"


@pytest.mark.parametrize("name", sorted(VACANCIES))
def test_filter_decisions_match_golden(golden: dict, name: str) -> None:
    """Every candidate must keep its explicit pass/reason/factor set for each vacancy."""
    snapshot = golden["vacancies"][name]["filter_decisions"]
    fresh = filter_decision_snapshot(name)
    difference = first_difference(snapshot, fresh, name)
    assert difference is None, f"strict filter decisions regressed at {difference}"

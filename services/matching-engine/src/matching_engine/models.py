"""Validated, contact-aware contracts for the candidate pool pipeline."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

Grade = Literal["intern", "junior", "middle", "senior"]
GradeStatus = Literal["approved", "self_reported"]
Direction = Literal["backend", "frontend", "fullstack", "mobile", "devops", "data", "qa", "ml"]
WorkType = Literal["onsite", "remote", "hybrid"]
ClaimStatus = Literal["confirmed", "partial", "self_reported", "pending", "rejected"]
CoordinateGroup = Literal["domain", "stack", "soft_skill"]
GRADE_RANK = {"intern": 0, "junior": 1, "middle": 2, "senior": 3}


class Contract(BaseModel):
    """Reject unknown fields instead of silently losing upstream profile data."""

    model_config = ConfigDict(extra="forbid")


class SalaryRange(Contract):
    """Inclusive salary bounds in RUB; zero is allowed for unpaid internships."""

    from_rub: int = Field(ge=0)
    to_rub: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self) -> "SalaryRange":
        """Reject reversed ranges before persistence."""
        if self.to_rub < self.from_rub:
            raise ValueError("to_rub must be greater than or equal to from_rub")
        return self


class Consent(Contract):
    """Explicit, versioned consent snapshot supplied by the trusted API."""

    processing: bool = False
    pool: bool = False
    training: bool = False
    training_version: Literal["training-v1"] | None = None
    training_accepted_at: AwareDatetime | None = None
    accepted_at: AwareDatetime | None = None
    version: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def training_requires_separate_acceptance(self) -> "Consent":
        """Training opt-in needs its own version/time; permission gates check all purposes."""
        if self.training and (self.training_version != "training-v1" or self.training_accepted_at is None):
            raise ValueError("training requires explicit versioned consent")
        if not self.training and (self.training_version is not None or self.training_accepted_at is not None):
            raise ValueError("withdrawn training consent cannot retain acceptance fields")
        return self


class GradeResult(Contract):
    """Upstream grade result, allowing a declared grade until testing confirms it.

    A real test session and approval timestamp are mandatory for an approved result.
    Self-reported grades deliberately keep both fields optional: the API must never
    manufacture a test session identifier for a candidate who has not tested yet.
    """

    grade: Grade
    status: Literal["approved", "self_reported", "pending", "rejected"]
    test_session_id: UUID | None = None
    approved_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def approved_requires_test_evidence(self) -> "GradeResult":
        """Reject approved grades without the immutable upstream test evidence."""
        if self.status == "approved" and (self.test_session_id is None or self.approved_at is None):
            raise ValueError("approved grade requires test_session_id and approved_at")
        return self


class Skill(Contract):
    """A registry key and ordinal level, retaining confirmation and provenance."""

    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")
    level: int = Field(ge=0, le=7)
    status: ClaimStatus
    provenance: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=30
    )


NarrativeRole = Literal["developer", "lead", "architect", "analyst", "designer", "researcher", "qa", "devops", "other"]
DurationMonths = Annotated[int, Field(strict=True, ge=0, le=1200)]
StrictBoolean = Annotated[bool, Field(strict=True)]


class NarrativeReference(Contract):
    """Explicit domain/stack reference, independently verified; provenance stays private."""

    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")
    status: ClaimStatus
    provenance: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=30)


class NarrativeValue[FactValue](Contract):
    """A typed scalar fact with its own upstream evidence state and private provenance."""

    value: FactValue
    status: ClaimStatus
    provenance: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=30)


class NarrativeFacts(Contract):
    """Optional structured facts; absence means unknown, never inferred from prose."""

    domains: list[NarrativeReference] = Field(default_factory=list, max_length=1000)
    stacks: list[NarrativeReference] = Field(default_factory=list, max_length=1000)
    duration_months: NarrativeValue[DurationMonths] | None = None
    role: NarrativeValue[NarrativeRole] | None = None
    production_support: NarrativeValue[StrictBoolean] | None = None

    @model_validator(mode="after")
    def unique_references(self) -> "NarrativeFacts":
        """Reject duplicate references within a group without conflating status and level."""
        for refs in (self.domains, self.stacks):
            if len({ref.key for ref in refs}) != len(refs):
                raise ValueError("duplicate narrative reference within group")
        return self


class NarrativeClaim(Contract):
    """Project, experience or achievement text plus the upstream evidence state."""

    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=10000)
    status: ClaimStatus
    facts: NarrativeFacts | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)
    provenance: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=30
    )


class CandidateProfile(Contract):
    """Complete or draft input; contacts remain private until downstream acceptance."""

    source_revision: int = Field(ge=1)
    display_name: str = Field(default="", max_length=300)
    email: str | None = Field(default=None, max_length=320)
    email_verified: bool = False
    phone: str | None = Field(default=None, max_length=100)
    city: str = Field(default="", max_length=300)
    work_types: list[WorkType] | None = Field(default=None, max_length=3)
    relocation_ready: bool | None = Field(default=None, strict=True)
    relocation_cities: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(
        default_factory=list, max_length=100
    )
    salary: SalaryRange | None = None
    primary_direction: Direction | None = None
    grade_result: GradeResult | None = None
    consents: Consent = Field(default_factory=Consent)
    domains: list[Skill] = Field(default_factory=list, max_length=1000)
    stacks: list[Skill] = Field(default_factory=list, max_length=1000)
    soft_skills: list[Skill] = Field(default_factory=list, max_length=1000)
    description: str = Field(default="", max_length=10000)
    projects: list[NarrativeClaim] = Field(default_factory=list, max_length=100)
    experience: list[NarrativeClaim] = Field(default_factory=list, max_length=100)
    achievements: list[NarrativeClaim] = Field(default_factory=list, max_length=100)
    preferences: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=100
    )
    metadata: dict[str, JsonValue] = Field(default_factory=dict, max_length=100)

    @model_validator(mode="after")
    def unique_skills(self) -> "CandidateProfile":
        """Each coordinate occurs once within its registry group."""
        for skills in (self.domains, self.stacks, self.soft_skills):
            keys = [skill.key for skill in skills]
            if len(set(keys)) != len(keys):
                raise ValueError("duplicate skill key within group")
        if self.work_types is not None and len(set(self.work_types)) != len(self.work_types):
            raise ValueError("duplicate work type")
        if any(not city.strip() for city in self.relocation_cities):
            raise ValueError("relocation city must not be blank")
        return self


class Category(Contract):
    """Deterministic specialization and grade with its upstream evidence status."""

    specialization: Direction
    grade: Grade
    grade_status: GradeStatus = "approved"


class Coordinate(Contract):
    """An immutable index in the append-only strict vector registry."""

    index: int = Field(ge=0)
    group: CoordinateGroup
    key: str


class StrictSchema(Contract):
    """Version and hash identify both ordering and normalization of coordinates."""

    version: int
    schema_hash: str
    coordinates: list[Coordinate]


class CoordinateAddition(Contract):
    """Authorized registry extension; old coordinates cannot be reordered."""

    group: CoordinateGroup
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")


class CandidateStatus(Contract):
    """Pipeline state for one candidate without private input data or error details."""

    candidate_id: UUID
    profile_revision: int
    state: Literal["draft", "queued", "processing", "retrying", "failed", "ready"]
    missing_requirements: list[str]
    category: Category | None = None
    strict_schema_version: int | None = None
    attempt_count: int = 0
    error_code: str | None = None


class PoolEntry(Contract):
    """Contact-free combine snapshot; published only with both vectors and a PDF."""

    candidate_id: UUID
    profile_revision: int
    display_name: str
    city: str
    salary: SalaryRange
    category: Category
    rank: int
    confirmed_strength: int
    strict_vector: list[float]
    strict_evidence: list["CoordinateEvidence"]
    strict_schema_version: int
    strict_schema_hash: str
    semantic_vector: list[float]
    semantic_model_version: str
    semantic_space_id: str
    published_at: datetime
    resume_url: str


class CoordinateEvidence(Contract):
    """Status and declared level, without arbitrary private provenance strings."""

    index: int
    group: CoordinateGroup
    key: str
    status: ClaimStatus | None
    level: int


PoolEntry.model_rebuild()


class PoolPage(Contract):
    """Bounded pool page; ranks are global within each direction."""

    items: list[PoolEntry]
    limit: int
    offset: int


def missing_requirements(profile: CandidateProfile) -> list[str]:
    """Return the precise checklist; drafts are persisted but never published."""
    missing: list[str] = []
    for key, present in (
        ("display_name", bool(profile.display_name.strip())),
        ("email_verified", bool(profile.email and profile.email_verified)),
        ("city", bool(profile.city.strip())),
        ("salary", profile.salary is not None),
        ("primary_direction", profile.primary_direction is not None),
        ("processing_consent", profile.consents.processing),
        ("pool_consent", profile.consents.pool),
        ("consent_timestamp", profile.consents.accepted_at is not None),
        ("consent_version", bool(profile.consents.version and profile.consents.version.strip())),
        (
            "approved_grade_result",
            profile.grade_result is not None
            and profile.grade_result.status in ("approved", "self_reported"),
        ),
    ):
        if not present:
            missing.append(key)
    return missing


def category_for(profile: CandidateProfile) -> Category:
    """Derive category from an eligible declaration or accepted test with explicit grade evidence."""
    if missing_requirements(profile) or profile.grade_result is None:
        raise ValueError("profile is not eligible for the pool")
    assert profile.primary_direction is not None
    return Category(specialization=profile.primary_direction, grade=profile.grade_result.grade,
                    grade_status=("self_reported" if profile.grade_result.status == "self_reported"
                                  else "approved"))


def confirmed_strength(profile: CandidateProfile) -> int:
    """Stable tie-breaker: confirmed skill levels and confirmed narrative facts."""
    return sum(
        skill.level
        for skill in (*profile.domains, *profile.stacks, *profile.soft_skills)
        if skill.status == "confirmed"
    ) + sum(
        1
        for claim in (*profile.projects, *profile.experience, *profile.achievements)
        if claim.status == "confirmed"
    )

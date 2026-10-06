"""Contact-free employer showcase contracts independent of a vacancy."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from matching_engine.models import (
    Category,
    ClaimStatus,
    Contract,
    Direction,
    Grade,
    NarrativeRole,
    SalaryRange,
    WorkType,
)


class SkillFilter(Contract):
    """Require one coordinate, a minimum declared level and allowed evidence states."""
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")
    min_level: int = Field(default=0, ge=0, le=7, strict=True)
    statuses: list[ClaimStatus] = Field(default_factory=lambda: ["confirmed", "partial", "self_reported", "pending"], min_length=1, max_length=5)


class SkillGroups(Contract):
    """Conjunctive coordinate requirements within each independent registry group."""
    domains: list[SkillFilter] = Field(default_factory=list, max_length=100)
    stacks: list[SkillFilter] = Field(default_factory=list, max_length=100)
    soft_skills: list[SkillFilter] = Field(default_factory=list, max_length=100)


class SearchFilters(Contract):
    """Explicit public-profile filters; salary overlap includes both endpoints."""
    classes: list[Direction] = Field(default_factory=list, max_length=8)
    grades: list[Grade] = Field(default_factory=list, max_length=4)
    cities: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(default_factory=list, max_length=100)
    salary: SalaryRange | None = None
    work_types: list[WorkType] = Field(default_factory=list, max_length=3)
    relocation_ready: bool | None = Field(default=None, strict=True)
    groups: SkillGroups = Field(default_factory=SkillGroups)

    @model_validator(mode="after")
    def valid_cities(self) -> "SearchFilters":
        """Reject blank cities and characters PostgreSQL text cannot represent."""
        if any(not city.strip() or "\x00" in city for city in self.cities):
            raise ValueError("cities must be nonblank and contain no NUL")
        return self


class SearchRequest(Contract):
    """Bounded independent employer query with an opaque continuation cursor."""
    q: str = Field(default="", max_length=500)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: int = Field(default=50, ge=1, le=100, strict=True)
    cursor: str | None = Field(default=None, max_length=4096)


class PublicSkill(Contract):
    """Published skill declaration with no evidence or provenance strings."""
    key: str
    level: int
    status: ClaimStatus


class PublicReference(Contract):
    """Typed narrative coordinate without private provenance."""
    key: str
    status: ClaimStatus


class PublicScalar(Contract):
    """One published typed scalar with its independently supplied status."""
    value: int | bool | NarrativeRole
    status: ClaimStatus


class PublicFacts(Contract):
    """Allowlisted narrative facts; arbitrary evidence never crosses this boundary."""
    domains: list[PublicReference] = Field(default_factory=list)
    stacks: list[PublicReference] = Field(default_factory=list)
    duration_months: PublicScalar | None = None
    role: PublicScalar | None = None
    production_support: PublicScalar | None = None


class PublicNarrative(Contract):
    """Sanitized public text and typed facts only."""
    title: str
    description: str
    status: ClaimStatus
    facts: PublicFacts | None = None


class PublicProfile(Contract):
    """Versioned, stable public projection without identity or contact fields."""
    projection_version: Literal[1] = 1
    display_name: str
    city: str
    salary: SalaryRange
    description: str = ""
    work_types: list[WorkType] | None = None
    relocation_ready: bool | None = None
    relocation_cities: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    domains: list[PublicSkill] = Field(default_factory=list)
    stacks: list[PublicSkill] = Field(default_factory=list)
    soft_skills: list[PublicSkill] = Field(default_factory=list)
    projects: list[PublicNarrative] = Field(default_factory=list)
    experience: list[PublicNarrative] = Field(default_factory=list)
    achievements: list[PublicNarrative] = Field(default_factory=list)


class SearchFactors(Contract):
    """Transparent reciprocal-rank fusion channel contributions (k=60)."""
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    rrf_score: float = 0.0


class PublicCandidateCard(Contract):
    """Public identity-free card with global class rank and protected PDF URL."""
    candidate_id: UUID
    profile_revision: int
    display_name: str
    city: str
    salary: SalaryRange
    category: Category
    rank: int
    confirmed_strength: int
    resume_url: str
    search_factors: SearchFactors = Field(default_factory=SearchFactors)


class PublicCandidateDetail(PublicCandidateCard):
    """Current published revision and its safe allowlisted profile."""
    public_profile: PublicProfile


class SearchPage(Contract):
    """Bounded search result with explicit degradation and freshness-aware pagination."""
    items: list[PublicCandidateCard]
    total: int
    limit: int
    has_more: bool
    next_cursor: str | None = None
    mode: Literal["browse", "lexical", "hybrid"]
    fallback_reason: str | None = None

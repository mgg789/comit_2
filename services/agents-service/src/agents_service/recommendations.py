"""Bounded DeepSeek synthesis from privacy-thresholded categorical counts only."""

import json
import re
from collections.abc import Callable
from typing import Annotated, Literal, Self
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_service.database import connect
from agents_service.providers import DeepSeekClient, ProviderError
from agents_service.store import Store

FEEDBACK_CATEGORIES = {"technical_skills", "experience", "communication", "salary", "work_format", "location", "other"}
MATCHING_CATEGORIES = {"selection_state_excluded", "class_mismatch", "salary_disjoint", "work_type_unknown",
    "work_type_mismatch", "location_unknown", "relocation_unknown", "relocation_not_ready",
    "relocation_destination_mismatch", "strict_schema_mismatch", "main_top_k_omission", "unranked_by_reranker", "reranker_reserve", "system_fallback"}


class Count(BaseModel):
    """A canonical category count, never arbitrary prose or an individual observation."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source: Literal["employer_feedback", "matching"]
    category: str = Field(max_length=64)
    count: int = Field(ge=3, le=1000000)

    @model_validator(mode="after")
    def safe_category(self) -> Self:
        """Require canonical vocabulary and stricter distinct-vacancy threshold."""
        known = FEEDBACK_CATEGORIES if self.source == "employer_feedback" else MATCHING_CATEGORIES
        if self.category not in known or self.source == "matching" and self.count < 4:
            raise ValueError("invalid recommendation evidence")
        return self


class RecommendationInput(BaseModel):
    """Only bounded counts are accepted; names, IDs and raw feedback are forbidden."""

    model_config = ConfigDict(extra="forbid")
    evidence: list[Count] = Field(min_length=1, max_length=21)

    @model_validator(mode="after")
    def unique_categories(self) -> Self:
        """Reject duplicate categories instead of double-counting observations."""
        if len({(item.source, item.category) for item in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate categories")
        return self


class RecommendationOutput(BaseModel):
    """Validated bounded text and exact category references, excluding private reasoning."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=40, max_length=3000)
    referenced_categories: list[str] = Field(min_length=1, max_length=21)


def generate(request: RecommendationInput, provider: DeepSeekClient) -> RecommendationOutput:
    """Call the existing official provider without sending any subject identity."""
    instruction = """Составь краткие рекомендации кандидату по агрегатам за неделю на русском языке.
Данные содержат только категории и число независимых наблюдений. Не выдумывай конкретные отзывы,
компании, вакансии, навыки, оценки, вероятность найма или причинность. Отзывы отражают мнения работодателей,
а не доказанные недостатки. Предлагай проверить профиль, уточнить предпочтения или подготовиться.
class_mismatch означает несовпадение специализации; salary_disjoint — диапазонов зарплаты;
work_type/relocation/location — формата работы и местоположения, не квалификации.
main_top_k_omission: кандидат прошёл строгий фильтр, но не вошёл в top-k основного отбора;
unranked_by_reranker: после успешного rerank подтверждено, что кандидат вне main top-k не оценивался reranker;
reranker_reserve: кандидат в резерве выдачи. Они не доказывают дефицит навыков.
selection_state_excluded — уже обработанный контакт, не недостаток кандидата.
strict_schema_mismatch и system_fallback — системные ограничения, не вина кандидата.
Верни только JSON {"text":"...", "referenced_categories":["точные категории из входа"]}.
Текст 40–3000 символов, без ссылок, контактов и идентификаторов. Не обещай результат."""
    result = provider.complete([
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(request.model_dump(), ensure_ascii=False, sort_keys=True)},
    ], [], max_tokens=1024)
    try:
        message = result["choices"][0]["message"]
        if message.get("tool_calls"):
            raise ValueError()
        output = RecommendationOutput.model_validate_json(message["content"])
        allowed = {item.category for item in request.evidence}
        if not set(output.referenced_categories) <= allowed or len(set(output.referenced_categories)) != len(output.referenced_categories):
            raise ValueError()
        if re.search(r"https?://|www\.|\S+@\S+|[0-9a-f]{8}-[0-9a-f]{4}-", output.text, re.IGNORECASE):
            raise ValueError()
        return output
    except (ValueError, KeyError, IndexError, TypeError):
        raise ProviderError("invalid_recommendation_response", False) from None


def _consent(database_url: str, candidate_id: UUID) -> None:
    with connect(database_url) as connection:
        row = connection.execute("""SELECT 1 FROM public.candidate_profiles WHERE candidate_id=%s
            AND processing_consent AND pool_consent AND training_consent AND training_consent_version='training-v2'""", (candidate_id,)).fetchone()
    if row is None:
        raise HTTPException(409, "recommendation_consent_required")


def register_recommendation_routes(app: FastAPI, owner: Callable[..., UUID], database: Callable[..., Store], provider: DeepSeekClient | None = None) -> None:
    """Reuse the trusted owner/token guard and recheck source consent around I/O."""
    Owner = Annotated[UUID, Depends(owner)]
    Database = Annotated[Store, Depends(database)]
    generator = provider or DeepSeekClient()

    @app.post("/internal/candidate-recommendations/generate", response_model=RecommendationOutput)
    def recommendation(request: RecommendationInput, owner_id: Owner, db: Database) -> RecommendationOutput:
        _consent(db.database_url, owner_id)
        output = generate(request, generator)
        _consent(db.database_url, owner_id)
        return output

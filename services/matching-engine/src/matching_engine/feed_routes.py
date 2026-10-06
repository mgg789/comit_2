"""Trusted employer-feed routes scoped to one owned vacancy."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from matching_engine.feed_models import (
    FeedCard,
    FeedDecision,
    FeedDecisionRequest,
    FeedImpression,
    FeedImpressionRequest,
    FeedListPage,
    FeedPage,
    FeedRequest,
    SavedCard,
    ShowcaseLikeReceipt,
    ShowcaseLikeRequest,
    VisitedCard,
)
from matching_engine.feed_store import FeedStore


def feed_router(repository: FeedStore | None, require_internal: Callable) -> APIRouter:
    """Build internal service routes without creating database side effects."""
    router = APIRouter(tags=["employer-feed"])

    def owner_store(
        _authenticated: Annotated[object, Depends(require_internal)],
        x_owner_id: Annotated[UUID, Header(alias="X-Owner-ID")],
    ) -> tuple[FeedStore, UUID]:
        """Require trusted service auth and a syntactically valid employer identity."""
        if repository is None:
            raise HTTPException(status_code=503, detail="feed database is not configured")
        return repository, x_owner_id

    @router.post("/internal/vacancies/{vacancy_id}/showcase-likes", response_model=ShowcaseLikeReceipt)
    def showcase_like(vacancy_id: UUID, payload: ShowcaseLikeRequest,
                      services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)]) -> ShowcaseLikeReceipt:
        """Like one explicit public candidate for an owned open vacancy without a ranked feed."""
        store, owner_id = services
        try:
            return store.showcase_like(owner_id, vacancy_id, payload)
        except (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled) as error:
            raise HTTPException(status_code=503, detail="showcase temporarily busy; retry the same key",
                                headers={"Retry-After": "1"}) from error

    @router.get("/internal/vacancies/{vacancy_id}/showcase-likes/{idempotency_key}", response_model=ShowcaseLikeReceipt)
    def showcase_receipt(vacancy_id: UUID, idempotency_key: UUID,
                         services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)]) -> ShowcaseLikeReceipt:
        """Read a manual like receipt before checking fresh candidate data."""
        store, owner_id = services
        return store.showcase_receipt(owner_id, vacancy_id, idempotency_key)

    @router.post("/internal/vacancies/{vacancy_id}/feed", response_model=FeedPage)
    def open_feed(vacancy_id: UUID, payload: FeedRequest,
                  services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)]) -> FeedPage:
        """Open a fresh 24-hour session for the owned vacancy."""
        store, owner_id = services
        try:
            return store.open(owner_id, payload, vacancy_id)
        except ValueError as exception:
            raise HTTPException(status_code=422, detail=str(exception)) from exception

    @router.post("/internal/vacancies/{vacancy_id}/feed/decisions", response_model=FeedDecision)
    def decide(vacancy_id: UUID, payload: FeedDecisionRequest,
               services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)]) -> FeedDecision:
        """Record one like or dislike and return a replay-safe receipt."""
        store, owner_id = services
        return store.decision(owner_id, vacancy_id, payload)

    @router.get("/internal/vacancies/{vacancy_id}/feed/decisions/{idempotency_key}", response_model=FeedDecision)
    def read_receipt(vacancy_id: UUID, idempotency_key: UUID,
                     services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)]) -> FeedDecision:
        """Read a prior receipt before a source freshness precheck."""
        store, owner_id = services
        return store.receipt(owner_id, vacancy_id, idempotency_key)

    @router.post("/internal/vacancies/{vacancy_id}/feed/impressions", response_model=FeedImpression)
    def impression(vacancy_id: UUID, payload: FeedImpressionRequest,
                   services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)]) -> FeedImpression:
        """Acknowledge client visibility separately from server delivery and explicit feedback."""
        store, owner_id = services
        return store.impression(owner_id, vacancy_id, payload)

    @router.get("/internal/vacancies/{vacancy_id}/feed/impressions/{idempotency_key}", response_model=FeedImpression)
    def impression_receipt(vacancy_id: UUID, idempotency_key: UUID,
                           services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)]) -> FeedImpression:
        """Read this owner's prior acknowledgement before checking current source freshness."""
        store, owner_id = services
        return store.impression_receipt(owner_id, vacancy_id, idempotency_key)

    @router.get("/internal/vacancies/{vacancy_id}/feed/exposures/{exposure_id}", response_model=FeedCard)
    def read_exposure(vacancy_id: UUID, exposure_id: UUID,
                      services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)]) -> FeedCard:
        """Validate current source membership for action and redacted-PDF access."""
        store, owner_id = services
        return store.exposure(owner_id, vacancy_id, exposure_id)

    @router.get("/internal/vacancies/{vacancy_id}/feed/{session_id}", response_model=FeedPage)
    def continue_feed(vacancy_id: UUID, session_id: UUID,
                      services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)],
                      cursor: str | None = Query(default=None),
                      limit: int = Query(default=50, ge=1, le=100)) -> FeedPage:
        """Continue one immutable session with an opaque stable position cursor."""
        store, owner_id = services
        try:
            return store.page(owner_id, vacancy_id, session_id, cursor, limit)
        except ValueError as exception:
            raise HTTPException(status_code=422, detail=str(exception)) from exception

    @router.get("/internal/vacancies/{vacancy_id}/saved", response_model=FeedListPage[SavedCard])
    def saved(vacancy_id: UUID, services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)],
              limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> FeedListPage[SavedCard]:
        """List saved candidates for this employer and vacancy."""
        store, owner_id = services
        return store.saved(owner_id, vacancy_id, limit, offset)

    @router.get("/internal/vacancies/{vacancy_id}/visited", response_model=FeedListPage[VisitedCard])
    def visited(vacancy_id: UUID, services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)],
                limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> FeedListPage[VisitedCard]:
        """List visited baselines and history badges for this vacancy."""
        store, owner_id = services
        return store.visited(owner_id, vacancy_id, limit, offset)

    @router.get("/internal/vacancies/{vacancy_id}/saved/{candidate_id}", response_model=SavedCard)
    def saved_candidate(vacancy_id: UUID, candidate_id: UUID,
                        services: Annotated[tuple[FeedStore, UUID], Depends(owner_store)]) -> SavedCard:
        """Expose one owned saved membership as the post-selection handoff contract."""
        store, owner_id = services
        return store.saved_candidate(owner_id, vacancy_id, candidate_id)

    return router

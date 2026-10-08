"""Mock FSP registry used for demo and integration tests.

This is a stand-in for the future FSP ID / Keycloak-backed registry. The shape
below is our proposal: rank, rating and event history are the minimum an employer
needs to weigh a candidate's sport background (context/50, fspBoost).
"""

from typing import Any

MOCK_PARTICIPANTS: dict[str, dict[str, Any]] = {
    "fsp-001": {
        "fsp_id": "fsp-001",
        "display_name": "Иван Иванов",
        "rank": "I",
        "rating": 1876,
        "events": [
            {"title": "Всероссийская олимпиада по программированию, финал", "year": 2025, "place": 7},
            {"title": "Кубок ФСП, осенний этап", "year": 2026, "place": 3},
        ],
        "certificates": ["Школа ФСП: алгоритмы, 2024"],
        "provider": "fsp_hackathon_registry",
        "provider_version": "demo-2026-10",
        "demo": True,
        "account_ref": "demo-person-001",
        "achievements": [
            {"claim_id": "fsp-001-achievement-2025-final", "title": "Всероссийская олимпиада по программированию, финал", "year": 2025, "place": 7},
            {"claim_id": "fsp-001-achievement-2026-cup", "title": "Кубок ФСП, осенний этап", "year": 2026, "place": 3},
        ],
    },
    "fsp-002": {
        "fsp_id": "fsp-002",
        "display_name": "Мария Петрова",
        "rank": "КМС",
        "rating": 2043,
        "events": [
            {"title": "Чемпионат ФСП, командный зачёт", "year": 2026, "place": 1},
        ],
        "certificates": [],
        "provider": "fsp_hackathon_registry",
        "provider_version": "demo-2026-10",
        "demo": True,
        "account_ref": "demo-person-002",
        "achievements": [
            {"claim_id": "fsp-002-achievement-2026-team", "title": "Чемпионат ФСП, командный зачёт", "year": 2026, "place": 1},
        ],
    },
}


def get_participant(fsp_id: str) -> dict[str, Any] | None:
    return MOCK_PARTICIPANTS.get(fsp_id)

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
    },
}


def get_participant(fsp_id: str) -> dict[str, Any] | None:
    return MOCK_PARTICIPANTS.get(fsp_id)

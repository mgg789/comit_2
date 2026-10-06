"""Source-consent concurrency regression on the isolated Harrier acceptance contour."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from matching_engine.database import connect
from matching_engine.learning_store import LearningStore, pseudonym

pytestmark = pytest.mark.integration


def test_operational_export_holds_authoritative_consent_until_commit(tmp_path: Path, monkeypatch) -> None:
    """A source withdrawal cannot commit halfway through writing a consented export."""
    url = os.environ.get("LEARNING_ACCEPTANCE_DATABASE_URL")
    if not url:
        pytest.skip("isolated real-card acceptance database required")
    config = conninfo_to_dict(url)
    assert config["dbname"].startswith("comit_cards_learning_")
    assert config["host"] in ("127.0.0.1", "localhost", "::1")
    with connect(url) as connection:
        row = connection.execute("""SELECT s.candidate_id FROM learning_pipeline.samples s
            JOIN learning_pipeline.cohorts c USING(main_run_id)
            JOIN public.candidate_profiles p USING(candidate_id)
            WHERE c.source='operational' AND s.features IS NOT NULL AND s.withdrawn_at IS NULL
              AND p.processing_consent AND p.pool_consent LIMIT 1""").fetchone()
        assert row is not None
    entered, release = threading.Event(), threading.Event()

    def paused(key: bytes, namespace: str, value: object) -> str:
        """Pause file writing without replacing any feature, credential or consent check."""
        if namespace == "main" and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return pseudonym(key, namespace, value)

    monkeypatch.setattr("matching_engine.learning_store.pseudonym", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(LearningStore(url).export, tmp_path / "export", b"synthetic-consent-lock-test-key-32")
        try:
            assert entered.wait(10)
            with pytest.raises(psycopg.errors.QueryCanceled), connect(url) as connection:
                connection.execute("SET LOCAL statement_timeout='200ms'")
                connection.execute("UPDATE public.candidate_profiles SET pool_consent=false WHERE candidate_id=%s",
                                   (row["candidate_id"],))
        finally:
            release.set()
        assert result.result(timeout=10)["rows"] > 0

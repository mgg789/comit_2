"""Authoritative consent guards against a synthetic acceptance database only."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from matching_engine.database import connect
from matching_engine.learning_store import pseudonym
from matching_engine.preference_dataset import current_consent
from psycopg.conninfo import conninfo_to_dict

KEY = b"synthetic-only-preference-test-key-32"


@pytest.fixture
def source():
    """Require explicit loopback synthetic acceptance; never inspect live users."""
    url = os.environ.get("LEARNING_ACCEPTANCE_DATABASE_URL")
    if not url:
        pytest.skip("isolated learning acceptance database not configured")
    config = conninfo_to_dict(url)
    assert config.get("host") in ("127.0.0.1", "localhost", "::1")
    assert config["dbname"].startswith("comit_cards_learning_")
    with connect(url) as connection:
        row = connection.execute("""SELECT c.candidate_id FROM candidate_pipeline.candidates c
            JOIN public.candidate_profiles p USING(candidate_id)
            WHERE p.processing_consent AND p.pool_consent
              AND (c.profile->'consents'->>'pool')::boolean IS TRUE LIMIT 1""").fetchone()
    assert row is not None
    summary = {"source": "operational", "pseudonym_key_fingerprint": hashlib.sha256(KEY).hexdigest()}
    return url, row["candidate_id"], summary


def test_current_consent_revalidated_without_matching_projection(source):
    """Historical exports cannot fit after withdrawal or with a different private key."""
    url, candidate, summary = source
    with current_consent([{"candidate_key": pseudonym(KEY, "candidate", candidate)}], summary, url, KEY) as result:
        assert result == "authoritative_api_and_matching_locked"
    with connect(url) as connection:
        revoked = connection.execute("""SELECT c.candidate_id FROM candidate_pipeline.candidates c
            JOIN public.candidate_profiles p USING(candidate_id)
            WHERE NOT p.pool_consent AND (c.profile->'consents'->>'pool')::boolean IS TRUE LIMIT 1""").fetchone()
    assert revoked is not None, "source-only revocation acceptance fixture is required"
    with (pytest.raises(ValueError, match="consent changed"),
          current_consent([{"candidate_key": pseudonym(KEY, "candidate", revoked["candidate_id"])}], summary, url, KEY)):
        pytest.fail("source withdrawal must block fit before matching projection")
    with (pytest.raises(ValueError, match="consent changed"),
          current_consent([{"candidate_key": "0" * 64}], summary, url, KEY)):
        pytest.fail("missing candidate must be rejected")
    with (pytest.raises(ValueError, match="key mismatch"),
          current_consent([], summary, url, b"different-synthetic-private-key-32")):
        pytest.fail("different HMAC key must be rejected")


def test_consent_cannot_commit_during_fit_and_result_write(source):
    """API consent write blocks while the training guard owns source row locks; update rolls back."""
    url, candidate, summary = source

    def revoke_attempt():
        with connect(url) as connection:
            connection.execute("SET LOCAL lock_timeout='200ms'")
            connection.execute("UPDATE public.candidate_profiles SET pool_consent=false WHERE candidate_id=%s", (candidate,))

    with (current_consent([{"candidate_key": pseudonym(KEY, "candidate", candidate)}], summary, url, KEY),
          ThreadPoolExecutor(max_workers=1) as executor, pytest.raises(psycopg.errors.LockNotAvailable)):
        executor.submit(revoke_attempt).result(timeout=3)
    with connect(url) as connection:
        assert connection.execute("SELECT pool_consent FROM public.candidate_profiles WHERE candidate_id=%s", (candidate,)).fetchone()["pool_consent"]

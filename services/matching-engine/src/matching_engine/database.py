"""Explicit checksum-verified migrations and short-lived PostgreSQL connections."""

import hashlib
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from matching_engine.strict import initial_schema

MIGRATION_LOCK = 1440130171
PIPELINE_LOCK = 1440130172


def connect(database_url: str) -> psycopg.Connection:
    """Open a transaction-capable connection; never print its credentials."""
    return psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5)


def migrate(database_url: str) -> list[str]:
    """Apply packaged SQL once under a DB lock, refusing changed applied files."""
    applied: list[str] = []
    migration_files = sorted(
        (item for item in files("matching_engine").joinpath("migrations").iterdir()
         if item.name.endswith(".sql")), key=lambda item: item.name
    )
    with connect(database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        connection.execute("CREATE SCHEMA IF NOT EXISTS candidate_pipeline")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS candidate_pipeline.migrations (
                name text PRIMARY KEY, checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        for migration_file in migration_files:
            sql = migration_file.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            existing = connection.execute(
                "SELECT checksum FROM candidate_pipeline.migrations WHERE name = %s",
                (migration_file.name,),
            ).fetchone()
            if existing:
                if existing["checksum"] != checksum:
                    raise RuntimeError(f"applied migration changed: {migration_file.name}")
                continue
            if migration_file.name in ("007_employer_feed.sql", "008_real_card_learning.sql", "009_live_preferences.sql", "010_preference_event_clock.sql", "011_hypothesis_exploration.sql", "011_candidate_showcase.sql"):
                # Match intake/feedback lock order before taking migration table locks.
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (PIPELINE_LOCK,))
            connection.execute(sql)
            if migration_file.name == "007_employer_feed.sql":
                # Capture legacy visited baselines now, before future profile events.
                # This projection strips prose/contacts and records its transparent origin.
                from matching_engine.feed_store import FeedStore
                FeedStore(database_url).seed_legacy_manualvisited(connection)
            if migration_file.name == "011_candidate_showcase.sql":
                # Expand existing public artifacts under the same lock/transaction as the index.
                # Never index private metadata, contact fields or arbitrary evidence payloads.
                from matching_engine.models import CandidateProfile
                from matching_engine.showcase_projection import public_profile
                rows = connection.execute("""SELECT p.candidate_id,c.profile
                    FROM candidate_pipeline.pool p JOIN candidate_pipeline.candidates c
                    ON c.candidate_id=p.candidate_id AND c.revision=p.revision""").fetchall()
                for row in rows:
                    projection = public_profile(CandidateProfile.model_validate(row["profile"]), row["candidate_id"])
                    connection.execute("UPDATE candidate_pipeline.pool SET public_profile=%s WHERE candidate_id=%s",
                                       (Jsonb(projection), row["candidate_id"]))
            connection.execute(
                "INSERT INTO candidate_pipeline.migrations(name, checksum) VALUES (%s,%s)",
                (migration_file.name, checksum),
            )
            applied.append(migration_file.name)
        schema = initial_schema()
        connection.execute(
            """INSERT INTO candidate_pipeline.strict_schemas(version,schema_hash,coordinates)
               VALUES (%s,%s,%s) ON CONFLICT (version) DO NOTHING""",
            (schema.version, schema.schema_hash, Jsonb([c.model_dump() for c in schema.coordinates])),
        )
        connection.execute(
            """INSERT INTO candidate_pipeline.settings(singleton,active_schema_version)
               VALUES (true,1) ON CONFLICT (singleton) DO NOTHING"""
        )
    return applied

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
            connection.execute(sql)
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

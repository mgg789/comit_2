"""PostgreSQL connection and checksum-verified migration helpers."""

from __future__ import annotations

import hashlib
from importlib.resources import files

import psycopg
from psycopg.rows import dict_row

MIGRATION_LOCK = 1440130181


def connect(database_url: str) -> psycopg.Connection:
    """Open a short-lived transaction-capable connection without logging credentials."""
    return psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5)


def migrate(database_url: str) -> list[str]:
    """Apply packaged agents migrations atomically and reject changed applied files."""
    applied: list[str] = []
    migration_dir = files("agents_service").joinpath("migrations")
    migration_files = sorted(
        (entry for entry in migration_dir.iterdir() if entry.name.endswith(".sql")),
        key=lambda entry: entry.name,
    )
    with connect(database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        connection.execute("CREATE SCHEMA IF NOT EXISTS agents_service")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agents_service.migrations (
                name text PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        for migration_file in migration_files:
            sql = migration_file.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            previous = connection.execute(
                "SELECT checksum FROM agents_service.migrations WHERE name = %s",
                (migration_file.name,),
            ).fetchone()
            if previous is not None:
                if previous["checksum"] != checksum:
                    raise RuntimeError(f"applied migration changed: {migration_file.name}")
                continue
            connection.execute(sql)
            connection.execute(
                "INSERT INTO agents_service.migrations(name, checksum) VALUES (%s, %s)",
                (migration_file.name, checksum),
            )
            applied.append(migration_file.name)
        connection.execute(
            """
            INSERT INTO agents_service.settings(singleton)
            VALUES (true)
            ON CONFLICT (singleton) DO NOTHING
            """
        )
    return applied

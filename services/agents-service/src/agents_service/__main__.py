"""Explicit service, migration and worker commands; serving never migrates."""

import argparse
import logging
import os
import signal
import threading
from typing import Protocol

import psycopg
import uvicorn

from agents_service.database import migrate
from agents_service.main import create_app
from agents_service.store import Store

logger = logging.getLogger(__name__)


class PollingWorker(Protocol):
    """Common durable polling interface for both agent workers."""

    def run(self, stop: threading.Event, poll_seconds: float = 2.0) -> None:
        """Poll until shutdown; caller retries transient database disconnections."""
        ...


def _run_with_database_retry(worker: PollingWorker, stop: threading.Event, poll_seconds: float) -> None:
    """Resume durable polling after transient PostgreSQL connection failures."""
    retry_seconds = 1.0
    while not stop.is_set():
        try:
            worker.run(stop, poll_seconds=poll_seconds)
            return
        except (psycopg.OperationalError, psycopg.InterfaceError):
            logger.warning("worker_database_temporarily_unavailable; retrying in %ss", retry_seconds)
            if stop.wait(retry_seconds):
                return
            retry_seconds = min(retry_seconds * 2, 30.0)


def main() -> None:
    """Run one selected mode without printing credentials or provider payloads."""
    parser = argparse.ArgumentParser(description="Comit agents and vacancy drafts")
    parser.add_argument("mode", nargs="?", default="serve", choices=("serve", "migrate", "worker", "organization-worker"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.mode == "serve":
        uvicorn.run(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8104")))
        return
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be configured")
    if args.mode == "migrate":
        for name in migrate(database_url):
            print("Applied migration:", name)
        return
    if args.mode == "organization-worker":
        from agents_service.organization_store import OrganizationStore
        from agents_service.organization_worker import OrganizationWorker
        worker = OrganizationWorker(OrganizationStore(database_url), lease_seconds=int(os.getenv("AGENTS_LEASE_SECONDS", "300")))
        if args.once:
            worker.process_once()
            return
        stop = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda _signal, _frame: stop.set())
        _run_with_database_retry(worker, stop, poll_seconds=float(os.getenv("AGENTS_POLL_SECONDS", "2")))
        return
    from agents_service.worker import Worker
    worker = Worker(Store(database_url), lease_seconds=int(os.getenv("AGENTS_LEASE_SECONDS", "300")))
    if args.once:
        worker.process_once()
        return
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signal, _frame: stop.set())
    _run_with_database_retry(worker, stop, poll_seconds=float(os.getenv("AGENTS_POLL_SECONDS", "2")))


if __name__ == "__main__":
    main()

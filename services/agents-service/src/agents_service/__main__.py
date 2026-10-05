"""Explicit service, migration and worker commands; serving never migrates."""

import argparse
import os
import signal
import threading

import uvicorn

from agents_service.database import migrate
from agents_service.main import create_app
from agents_service.store import Store


def main() -> None:
    """Run one selected mode without printing credentials or provider payloads."""
    parser = argparse.ArgumentParser(description="Comit agents and vacancy drafts")
    parser.add_argument("mode", nargs="?", default="serve", choices=("serve", "migrate", "worker"))
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
    from agents_service.worker import Worker
    worker = Worker(Store(database_url), lease_seconds=int(os.getenv("AGENTS_LEASE_SECONDS", "300")))
    if args.once:
        worker.process_once()
        return
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signal, _frame: stop.set())
    worker.run(stop, poll_seconds=float(os.getenv("AGENTS_POLL_SECONDS", "2")))


if __name__ == "__main__":
    main()

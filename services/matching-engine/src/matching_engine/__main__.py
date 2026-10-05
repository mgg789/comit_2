"""Explicit serve/migrate/worker entry points; serving never mutates the schema."""

import argparse
import os
import signal
import threading

import uvicorn

from matching_engine.database import migrate
from matching_engine.main import create_app
from matching_engine.store import Store


def main() -> None:
    """Run one chosen mode, using environment configuration without exposing credentials."""
    parser = argparse.ArgumentParser(description="Comit candidate pipeline")
    parser.add_argument("mode", nargs="?", default="serve", choices=("serve", "migrate", "worker"))
    parser.add_argument("--once", action="store_true", help="Process one available worker job")
    args = parser.parse_args()
    if args.mode == "serve":
        uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8101")))
        return
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be configured")
    if args.mode == "migrate":
        for name in migrate(database_url):
            print(f"Applied migration: {name}")
        return
    from matching_engine.worker import Worker
    worker = Worker(Store(database_url),
                    lease_seconds=int(os.environ.get("PIPELINE_LEASE_SECONDS", "300")),
                    max_attempts=int(os.environ.get("PIPELINE_MAX_ATTEMPTS", "5")),
                    retry_base_seconds=int(os.environ.get("PIPELINE_RETRY_BASE_SECONDS", "30")))
    if args.once:
        worker.process_once()
        return
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signal, _frame: stop.set())
    worker.run(stop, poll_seconds=float(os.environ.get("PIPELINE_POLL_SECONDS", "2")))


if __name__ == "__main__":
    main()

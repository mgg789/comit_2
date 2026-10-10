"""Explicit local export of real-card observations; never auto-train or contact a provider."""

import argparse
import json
import os
from pathlib import Path

from matching_engine.learning_store import LearningStore


def main() -> None:
    """Export to a new private directory using a stable private pseudonym key."""
    parser = argparse.ArgumentParser(description="Export consented card observations")
    parser.add_argument("mode", nargs="?", choices=("status", "export"), default="export")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--source", choices=("operational", "synthetic"), default="operational")
    parser.add_argument("--limit", type=int, default=10000)
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL must be configured")
    if args.mode == "status":
        print(json.dumps(LearningStore(database_url).status(), sort_keys=True))
        return
    if args.directory is None or args.key_file is None:
        parser.error("export requires --directory and --key-file")
    if args.key_file.stat().st_mode & 0o077:
        parser.error("pseudonym key file must be private (mode 0600)")
    report = LearningStore(database_url).export(args.directory, args.key_file.read_bytes(),
                                              source=args.source, limit=args.limit)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()

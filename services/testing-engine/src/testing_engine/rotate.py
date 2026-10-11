"""Rotation CLI: retire expired instances and refill the bank (offline pass).

Run: PYTHONPATH=src python -m testing_engine.rotate [--dry-run]
Uses DEEPSEEK_API_KEY / DEEPSEEK_MODEL when generation is possible; without a
key it only performs retirement and prints the deficit report.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from testing_engine.bank_seed import FAMILIES
from testing_engine.generator import (
    MIN_STOCK_PER_SLOT,
    DeepSeekGenerator,
    refill,
)
from testing_engine.store import TestingStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate and refill the testing item bank")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("TESTING_DATA_DIR", Path.cwd() / "data" / "testing")))
    parser.add_argument("--daily-limit", type=int, default=50)
    parser.add_argument("--min-stock", type=int, default=MIN_STOCK_PER_SLOT)
    parser.add_argument("--dry-run", action="store_true",
                        help="retire and report deficits without generating")
    args = parser.parse_args()

    store = TestingStore(args.data_dir)
    retired = store.retire_expired()
    deficits = []
    for family_row in FAMILIES:
        family = family_row["family"]
        for level in (1, 2, 3, 4):
            active = store.count_active(family, level)
            if active < args.min_stock:
                deficits.append({"slot": f"{family}-L{level}", "active": active,
                                 "deficit": args.min_stock - active})
    if args.dry_run:
        print(json.dumps({"retired": retired, "deficits": deficits,
                          "mode": "dry_run"}, ensure_ascii=False, indent=1))
        return
    generator = DeepSeekGenerator()
    if not generator.available():
        print(json.dumps({"retired": retired, "deficits": deficits,
                          "generation": "unavailable: configured LLM API key not set"},
                         ensure_ascii=False, indent=1))
        return
    report = refill(store, generator.generate_item, data_dir=args.data_dir,
                    min_stock=args.min_stock, daily_limit=args.daily_limit)
    print(json.dumps({"retired": retired, "report": report}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

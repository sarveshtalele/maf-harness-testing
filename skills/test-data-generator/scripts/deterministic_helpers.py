#!/usr/bin/env python3
"""
Deterministic ID and constant generator for test-data fixtures.

This script produces shareable constants (IDs, dates, codes) from a seed
so that the test-data-generator skill produces repeatable output across runs.
It is *not* a full fixture generator — Claude remains the fixture author.
Use this script as a source of deterministic building blocks.

Usage:
    python scripts/deterministic_helpers.py [--seed 42] [--count 5]

Output (JSON to stdout):
    {
      "seed": 42,
      "ids": ["id_00001", "id_00002", "id_00003", "id_00004", "id_00005"],
      "customer_ids": ["cust_00001", "cust_00002", "cust_00003", "cust_00004", "cust_00005"],
      "timestamps": ["2025-01-01T00:00:00Z", "2025-01-02T03:12:48Z", ...],
      "summary": "All values derived from seed 42 — deterministic across runs."
    }
"""

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from typing import List


def deterministic_values(
    seed: int,
    count: int,
    prefixes: List[str] = None,
    base_date: str = "2025-01-01",
) -> dict:
    """Return a dict of deterministic constant arrays derived from *seed*."""
    rng = random.Random(seed)
    base = datetime.fromisoformat(base_date).replace(tzinfo=timezone.utc)

    if prefixes is None:
        prefixes = ["id", "cust", "ord", "pay"]

    result = {"seed": seed, "count": count}
    for prefix in prefixes:
        # Hash the prefix with the seed so each prefix gets its own sequence
        hasher = hashlib.sha256(f"{seed}:{prefix}".encode())
        offset = int(hasher.hexdigest()[:8], 16)
        result[f"{prefix}_ids"] = [
            f"{prefix}_{offset + i:05d}" for i in range(count)
        ]

    # Deterministic timestamps: one per day starting from base_date
    result["timestamps"] = [
        (base + timedelta(days=i)).isoformat().replace("+00:00", "Z")
        for i in range(count)
    ]

    # Deterministic sample strings for common payload fields
    names = [
        "alice", "bob", "carol", "dave", "eve",
        "frank", "grace", "hank", "iris", "jack",
    ]
    shuffled = names[:]
    rng.shuffle(shuffled)
    result["usernames"] = [f"{shuffled[i % len(shuffled)]}_{seed}" for i in range(count)]

    domains = ["example.com", "test.org", "sample.net", "demo.io", "mock.co"]
    result["emails"] = [
        f"{shuffled[i % len(shuffled)]}@{domains[(seed + i) % len(domains)]}"
        for i in range(count)
    ]

    result["summary"] = f"All values derived from seed {seed} — deterministic across runs."
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate deterministic constants for test-data fixtures."
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Seed value (default: 42). Match the skill's deterministic_seed config."
    )
    parser.add_argument(
        "--count", type=int, default=5,
        help="Number of values per prefix (default: 5)."
    )
    parser.add_argument(
        "--prefixes", type=str, nargs="*",
        default=["id", "cust", "ord", "pay"],
        help="ID prefixes to generate sequences for."
    )
    parser.add_argument(
        "--base-date", type=str, default="2025-01-01",
        help="Base date for timestamp generation (YYYY-MM-DD)."
    )
    args = parser.parse_args()

    result = deterministic_values(
        seed=args.seed,
        count=args.count,
        prefixes=args.prefixes,
        base_date=args.base_date,
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
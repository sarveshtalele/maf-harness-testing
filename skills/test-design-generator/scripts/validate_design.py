#!/usr/bin/env python3
"""Validate and finalize a test-design JSON document without writing files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from maf_test_harness.skill_contracts import finalize_design
except ImportError:
    repository_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repository_root / "src"))
    from maf_test_harness.skill_contracts import finalize_design


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--repo-url", required=True)
    args = parser.parse_args()
    with args.input.open(encoding="utf-8") as handle:
        document = json.load(handle)
    with args.source.open(encoding="utf-8") as handle:
        source = json.load(handle)
    skills_root = Path(__file__).resolve().parents[2]
    finalized = finalize_design(document, source, args.repo_url, skills_root)
    json.dump(finalized, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

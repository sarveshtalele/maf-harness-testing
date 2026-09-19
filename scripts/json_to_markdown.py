#!/usr/bin/env python3
"""Convert a saved harness JSON artifact into Markdown.

Usage:
    uv run python scripts/json_to_markdown.py artifact.json
    uv run python scripts/json_to_markdown.py artifact.json --output artifact.md --title "Test Design"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from maf_test_harness.artifact_presenter import json_to_markdown


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert harness JSON artifacts to readable Markdown.")
    parser.add_argument("input", type=Path, help="Input JSON file")
    parser.add_argument("--output", "-o", type=Path, help="Output Markdown file")
    parser.add_argument("--title", help="Optional Markdown document title")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    title = args.title or args.input.stem.replace("_", " ").title()
    markdown = json_to_markdown(payload, title=title)
    output = args.output or args.input.with_suffix(".md")
    output.write_text(markdown, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

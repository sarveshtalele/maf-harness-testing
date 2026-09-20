"""CLI entry point for the MAF Agent building-blocks reference."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from harness import ReferenceHarness

DEFAULT_PROMPT = (
    "Review this repository and give a brief implementation guide explaining how it separates Agent Skills, "
    "function tools, middleware hooks, the Agent Harness, and workflow orchestration. Cite repository file paths."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Microsoft Agent Framework building-blocks reference.")
    parser.add_argument("--repo", default=".", help="Local repository path to review.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Question for the reference harness.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL"))
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL"))
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required. Source the root .env or export the variable.")
    if not args.model:
        raise SystemExit("OPENAI_MODEL (or --model) is required.")

    harness = ReferenceHarness(
        repo_root=Path(args.repo),
        api_key=api_key,
        model=args.model,
        base_url=args.base_url,
    )
    result = await harness.run(args.prompt)

    print("\n=== ANSWER ===\n")
    print(result["answer"])
    print("\n=== TOOL CALLS ===\n")
    print(", ".join(result["tool_calls"]) or "(none)")
    print("\n=== AUDIT ===\n")
    print(json.dumps(result["audit"], indent=2))


if __name__ == "__main__":
    asyncio.run(run())

#!/usr/bin/env python3
"""
Requirement Analysis Skill — CLI Entry Point

Given a free-text feature/change request, deterministically answers:
  - Which existing file(s) should be updated (or a new file created)?
  - Where exactly (which function/class/route/line)?
  - What existing pattern should the new code follow?
  - What security, compliance, and formatting rules apply?

Usage
-----
python requirement_analysis_skill.py --repo-path . \
  --requirement "Add a discount_code field to the Order model and expose it on the orders API"

python requirement_analysis_skill.py --repo-path . \
  --requirement "Fix the login endpoint to reject expired tokens" \
  --output ./requirement-analysis-output --json-only
"""
import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from engine.codebase_indexer import CodebaseIndexer
from engine.requirement_parser import RequirementParser
from engine.location_resolver import LocationResolver
from engine.pattern_finder import PatternFinder
from engine.security_compliance_checker import SecurityComplianceChecker
from engine.formatting_analyzer import FormattingAnalyzer
from engine.plan_generator import PlanGenerator

_DIVIDER = "=" * 64


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Requirement Analysis Skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--repo-path", default=".",
        help="Root of the repository to analyse (default: current directory)",
    )
    p.add_argument(
        "--requirement", required=True,
        help="Free-text description of the feature/change to implement",
    )
    p.add_argument(
        "--top-n", type=int, default=5,
        help="Maximum number of candidate files to return (default: 5)",
    )
    p.add_argument(
        "--output", default=None,
        help="Output directory (default: <repo-root>/requirement-analysis-output/)",
    )
    p.add_argument(
        "--json-only", action="store_true",
        help="Emit the JSON result to stdout and exit (no files written)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    repo_path = Path(args.repo_path).resolve()

    if not repo_path.exists():
        print(f"[error] Repository path not found: {repo_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output) if args.output else repo_path / "requirement-analysis-output"

    if not args.json_only:
        print(f"\n{_DIVIDER}")
        print("  REQUIREMENT ANALYSIS SKILL  v1.0.0")
        print(_DIVIDER)
        print(f"  Repository : {repo_path}")
        print(f"  Requirement: {args.requirement}")
        print(f"  Output     : {output_dir}")
        print(_DIVIDER)

    # ── Phase 1: Parse the requirement ────────────────────────────────
    _step(1, 6, "Parsing requirement", args.json_only)
    parser = RequirementParser()
    intent = parser.parse(args.requirement)
    _log(
        f"Action: {intent.action} | Types: {intent.target_types or '-'} | "
        f"Entities: {intent.entities or '-'} | Domain: {intent.domain_tags or '-'}",
        args.json_only,
    )

    # ── Phase 2: Index the codebase ───────────────────────────────────
    _step(2, 6, "Indexing codebase", args.json_only)
    indexer = CodebaseIndexer(repo_path)
    file_indexes = indexer.build()
    total_symbols = sum(len(fi.symbols) for fi in file_indexes)
    _log(f"Files indexed: {len(file_indexes)}, Symbols extracted: {total_symbols}", args.json_only)

    # ── Phase 3: Resolve target locations ─────────────────────────────
    _step(3, 6, "Resolving target file(s) and location(s)", args.json_only)
    resolver = LocationResolver(repo_path)
    location = resolver.resolve(intent, file_indexes, top_n=args.top_n)
    _log(f"Candidates found: {len(location['candidates'])}", args.json_only)

    # ── Phase 4: Find a structural pattern to follow ──────────────────
    _step(4, 6, "Finding implementation pattern", args.json_only)
    pattern_finder = PatternFinder()
    pattern = pattern_finder.find(intent, file_indexes)

    # ── Phase 5: Security & compliance checklist ──────────────────────
    _step(5, 6, "Building security & compliance checklist", args.json_only)
    checker = SecurityComplianceChecker()
    checklist = checker.check(intent)
    _log(f"Checklist items: {len(checklist)}", args.json_only)

    # ── Phase 6: Formatting analysis ──────────────────────────────────
    _step(6, 6, "Analysing formatting & linting setup", args.json_only)
    formatter = FormattingAnalyzer(repo_path)
    candidate_paths = [c["path"] for c in location["candidates"]]
    formatting = formatter.analyze(candidate_paths)
    _log(f"Formatter/linter configs detected: {len(formatting['detected_tools'])}", args.json_only)

    # ── Assemble result ────────────────────────────────────────────────
    result = {
        "repo_path": str(repo_path),
        "intent": vars(intent),
        "location": location,
        "pattern": pattern,
        "security_compliance": checklist,
        "formatting": formatting,
    }

    # ── JSON-only mode ────────────────────────────────────────────────
    if args.json_only:
        print(json.dumps(result, indent=2, default=str))
        return 0

    # ── Write output files ────────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    print("\n  Generating implementation plan...")
    plan_gen = PlanGenerator(output_dir)
    plan_path = plan_gen.generate_markdown(result)
    json_path = plan_gen.generate_json(result)
    print(f"  [ok] Implementation Plan : {plan_path}")
    print(f"  [ok] Analysis JSON       : {json_path}")

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{_DIVIDER}")
    print(f"  Action      : {intent.action}")
    print(f"  Target type : {', '.join(intent.target_types) or 'n/a'}")
    if location["candidates"]:
        top = location["candidates"][0]
        print(f"  Top file    : {top['path']} (score {top['score']})")
    else:
        print("  Top file    : none found")
    if location["new_file_suggestion"]:
        print(f"  New file    : {location['new_file_suggestion']['suggested_path']}")
    print(f"  Checklist   : {len(checklist)} item(s)")
    print(_DIVIDER + "\n")

    return 0


def _step(n: int, total: int, label: str, silent: bool) -> None:
    if not silent:
        print(f"[{n}/{total}] {label}...")


def _log(msg: str, silent: bool) -> None:
    if not silent:
        print(f"      {msg}")


if __name__ == "__main__":
    sys.exit(main())

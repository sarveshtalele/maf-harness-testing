"""Replay a finished run's generated tests against seeded-bug copies of its repository.

Detection: a mutant is killed when a test that passed on the clean copy fails (an assertion, not an error) on the
mutant. False fails: generated tests that fail on the clean copy, before triage (raw) and the ones the run's own
triage still calls product defects (after triage, from qa_report.json). One model run, many mutants.

    python scripts/benchmark_seeded.py RUN_DIR benchmarks/seeded/<repo>.json --python <env python> --tests tests/x
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from maf_test_harness.envs.execution import read_junit_cases, run_command


def apply(root: Path, mutant: dict) -> None:
    path = root / mutant["file"]
    text = path.read_text(encoding="utf-8")
    if text.count(mutant["find"]) != 1:
        raise ValueError(
            f"{mutant['id']}: find string occurs {text.count(mutant['find'])} times in {mutant['file']}"
        )
    path.write_text(text.replace(mutant["find"], mutant["replace"]), encoding="utf-8")


def trial(repo: Path, tests: list[str], python: str, work: Path, mutant: dict | None, timeout: float) -> dict:
    root = work / "repo"
    shutil.copytree(
        repo, root, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache")
    )
    if mutant:
        apply(root, mutant)
    result = run_command(
        [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml=results.xml", *tests],
        root,
        work / "process",
        timeout=timeout,
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONPATH": str(root)},
    )
    junit = root / "results.xml"
    cases = read_junit_cases([junit]) if junit.is_file() else []
    return {
        "exit_code": result["exit_code"],
        "outcomes": {f"{c['classname']}::{c['name']}": c["status"] for c in cases},
    }


def evaluate(run_dir: Path, spec: dict, python: str, tests: list[str], timeout: float) -> dict:
    repo = run_dir / "repo"
    with tempfile.TemporaryDirectory() as tmp:
        base = trial(repo, tests, python, Path(tmp) / "baseline", None, timeout)
        passing = {k for k, v in base["outcomes"].items() if v == "passed"}
        mutants = []
        for mutant in spec["mutants"]:
            observed = trial(repo, tests, python, Path(tmp) / mutant["id"], mutant, timeout)
            killers = sorted(k for k in passing if observed["outcomes"].get(k) == "failed")
            mutants.append(
                {"id": mutant["id"], "bug": mutant["bug"], "killed": bool(killers), "killed_by": killers}
            )
    qa = run_dir / "qa_report.json"
    triage = []
    if qa.is_file():
        triage = json.loads(qa.read_text(encoding="utf-8")).get("triage") or []
    raw_fails = sorted(k for k, v in base["outcomes"].items() if v in {"failed", "error"})
    killed = sum(m["killed"] for m in mutants)
    return {
        "repo": spec.get("repo"),
        "tests": len(base["outcomes"]),
        "baseline_passed": len(passing),
        "raw_false_fails": raw_fails,
        "product_defects_after_triage": [t for t in triage if t.get("classification") == "product_defect"],
        "mutants": mutants,
        "detection_rate": round(killed / len(mutants), 3) if mutants else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--python", default=sys.executable, help="interpreter with the target's dependencies")
    parser.add_argument("--tests", nargs="+", required=True, help="generated test paths relative to the repo")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    report = evaluate(args.run_dir, spec, args.python, args.tests, args.timeout)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

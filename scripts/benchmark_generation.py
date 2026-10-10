"""Score submitted pytest suites against source-backed targets and seeded defects.

Default reference suites calibrate the evaluator; they are not model output.
The harness's sanitized local subprocess boundary is not a security sandbox.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from maf_test_harness.envs.execution import read_junit_cases, run_command

CORPUS = Path(__file__).resolve().parents[1] / "benchmarks" / "generation"


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
            digest.update(b"\0")
    return digest.hexdigest()


def trial(source: Path, tests: Path, destination: Path, timeout: float) -> dict:
    workspace = destination / "workspace"
    workspace.mkdir(parents=True)
    shutil.copyfile(source, workspace / "target.py")
    shutil.copytree(tests, workspace / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Do not inherit the harness's testpaths or external pytest plugins.
    (workspace / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    result = run_command(
        [sys.executable, "-m", "pytest", "-q", "-c", "pytest.ini", "--junitxml=results.xml", "tests"],
        workspace,
        destination / "process",
        timeout=timeout,
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONPATH": str(workspace)},
    )
    junit = workspace / "results.xml"
    report_error = None
    try:
        cases = read_junit_cases([junit]) if junit.is_file() else []
    except (ET.ParseError, OSError) as exc:
        cases = []
        report_error = str(exc)
    counts = {
        status: sum(case["status"] == status for case in cases)
        for status in ("passed", "failed", "error", "skipped")
    }
    valid = result["exit_code"] == 0 and bool(cases) and counts["passed"] == len(cases)
    return {
        "valid": valid,
        "exit_code": result["exit_code"],
        "status": result["status"],
        "counts": counts,
        "tests": len(cases),
        "identities": sorted([case["classname"], case["name"]] for case in cases),
        "report_error": report_error,
    }


def detected(baseline: dict, observed: dict) -> bool:
    # Only test-phase failures count; setup/collection errors and crashes do not.
    return (
        baseline["valid"]
        and observed["exit_code"] == 1
        and observed["counts"]["failed"] > 0
        and observed["counts"]["error"] == 0
        and observed["counts"]["skipped"] == 0
        and observed["tests"] == baseline["tests"]
        and observed["identities"] == baseline["identities"]
    )


def evaluate(candidate_dir: Path | None, output: Path, *, timeout: float = 30) -> dict:
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for task in manifest["tasks"]:
        fixture = CORPUS / task["id"]
        tests = candidate_dir / task["id"] if candidate_dir else fixture / "reference"
        entry = {
            "id": task["id"],
            "candidate_sha256": fingerprint(tests) if tests.is_dir() else None,
            "mutations": [],
        }
        if not tests.is_dir() or not list(tests.rglob("test_*.py")):
            entry.update(valid=False, reason="missing_candidate_tests")
        else:
            baseline = trial(fixture / "target.py", tests, output / task["id"] / "baseline", timeout)
            entry.update(valid=baseline["valid"], baseline=baseline)
        for mutation in task["mutations"]:
            if entry["valid"]:
                source = fixture / "mutations" / (mutation + ".py")
                observed = trial(source, tests, output / task["id"] / mutation, timeout)
                entry["mutations"].append(
                    {"id": mutation, "killed": detected(entry["baseline"], observed), "trial": observed}
                )
            else:
                entry["mutations"].append({"id": mutation, "killed": False, "reason": "invalid_baseline"})
        results.append(entry)
    total = sum(len(task["mutations"]) for task in manifest["tasks"])
    killed = sum(mutation["killed"] for entry in results for mutation in entry["mutations"])
    valid_tasks = sum(entry["valid"] for entry in results)
    report = {
        "schema_version": 1,
        "mode": "submitted_candidates" if candidate_dir else "reference_calibration",
        "corpus_sha256": fingerprint(CORPUS),
        "python": sys.version.split()[0],
        "tasks": results,
        "metrics": {
            "tasks": len(results),
            "valid_tasks": valid_tasks,
            "baseline_validity": valid_tasks / len(results),
            "mutations": total,
            "killed": killed,
            "effective_mutation_score": killed / total,
        },
        "limits": [
            "Local processes are not a security sandbox.",
            "Reference calibration does not measure model generation quality.",
            "Seeded defects cover this corpus only; cost and browser quality are not measured.",
        ],
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, help="One pytest suite subdirectory per corpus task")
    parser.add_argument("--output", type=Path, help="New evidence directory; defaults to temporary storage")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    output = args.output or Path(tempfile.mkdtemp(prefix="generation-benchmark-")) / "results"
    candidates = args.candidate_dir.resolve() if args.candidate_dir else None
    report = evaluate(candidates, output.resolve(), timeout=args.timeout)
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "metrics": report["metrics"],
                "report": str(output.resolve() / "report.json"),
            },
            indent=2,
        )
    )
    metrics = report["metrics"]
    return 0 if metrics["baseline_validity"] == 1 and metrics["effective_mutation_score"] == 1 else 1


if __name__ == "__main__":
    sys.exit(main())

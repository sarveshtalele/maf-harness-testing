"""Integration: measured line and branch coverage with the pinned coverage tooling in a real isolated venv.

Opt-in like the other target checks (run with `pytest tests/targets`). It needs network access to install the pins;
without it the test is skipped with the reason."""

import subprocess
import sys

import pytest

from maf_test_harness.envs import platform_compat, runners
from maf_test_harness.envs.runners import (
    COVERAGE_ARGV,
    COVERAGE_INI,
    COVERAGE_JSON,
    COVERAGE_PACKAGES,
    RunPlan,
    Step,
    execute_plan,
)
from maf_test_harness.qa.report import build_qa_report


def _make_venv(root):
    venv = root / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=300)
    python = platform_compat.venv_python(venv)
    result = subprocess.run(
        [str(python), "-m", "pip", "install", "-q", "pytest", *COVERAGE_PACKAGES],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        pytest.skip("pip could not install the pinned coverage tooling: " + result.stderr.strip()[-300:])
    return python


def test_pinned_coverage_measures_source_lines_and_branches_only(tmp_path):
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "service.py").write_text(
        "def classify(n):\n    if n > 0:\n        return 'pos'\n    return 'neg'\n", encoding="utf-8"
    )
    (ws / "tests" / "test_service.py").write_text(
        "from service import classify\n\n\ndef test_pos():\n    assert classify(1) == 'pos'\n",
        encoding="utf-8",
    )
    (ws / ".harness-reports").mkdir()
    (ws / COVERAGE_INI).write_text(runners.COVERAGE_INI_TEXT, encoding="utf-8")
    python = _make_venv(tmp_path)
    step = Step(
        "pytest",
        [
            str(python),
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--junitxml=.harness-reports/junit.xml",
            "tests/test_service.py",
        ],
        "test",
        env={"PYTHONPATH": str(ws)},
        parser="junit",
        report_glob=".harness-reports/junit.xml",
        optional_argv=list(COVERAGE_ARGV),
        coverage_report=COVERAGE_JSON,
    )
    report = execute_plan(RunPlan("python-pytest", [step]), ws, tmp_path / "out")
    assert report["status"] == "passed", report.get("error")
    assert report["coverage"]["status"] == "measured"
    service = report["coverage"]["files"]["service.py"]
    assert service["num_statements"] > 0 and service["num_branches"] == 2 and service["covered_branches"] == 1
    qa = build_qa_report(
        repo={"source_files": ["service.py"]},
        requirements={},
        design={"positive_tests": [], "traceability_matrix": {}},
        generation={"files": []},
        execution=report,
    )
    assert qa["measured_coverage"]["status"] == "measured"
    assert qa["measured_coverage"]["totals"]["branch_percent"] == 50.0

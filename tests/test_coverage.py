"""Measured coverage (accuracy 7.6): pinned coverage tooling, the optional argv, the pure reader and the real run.

The real subprocess test runs only where pytest-cov is importable in the harness venv (it is not a harness dependency);
tests/targets/test_coverage_measured.py builds an isolated venv with the pins instead."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from maf_test_harness.envs import runners
from maf_test_harness.envs.runners import (
    COVERAGE_ARGV,
    COVERAGE_INI,
    COVERAGE_JSON,
    COVERAGE_PACKAGES,
    RunPlan,
    Step,
    build_plan,
    execute_plan,
)
from maf_test_harness.qa import coverage
from maf_test_harness.qa.report import build_qa_report, render_markdown
from maf_test_harness.repo.stack import StackProfile

SERVICE = "def classify(n):\n    if n > 0:\n        return 'pos'\n    return 'neg'\n"
TEST = "from service import classify\n\n\ndef test_pos():\n    assert classify(1) == 'pos'\n"


def _report(files):
    return {"files": files}


def test_pins_are_exact_and_test_only():
    assert COVERAGE_PACKAGES == ("coverage==7.10.7", "pytest-cov==7.0.0")


def test_plan_installs_pinned_coverage_optionally_and_adds_argv_to_the_pytest_step(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "requirements.txt").write_text("requests\n", encoding="utf-8")
    plan = build_plan(
        StackProfile(primary="python"),
        ws,
        [{"path": "tests/test_a.py", "content": "def test_a(): pass\n", "case_ids": ["TC-1"]}],
        cache_dir=tmp_path / "c",
    )
    install = next(s for s in plan.steps if s.name == "install-coverage")
    assert install.argv[-2:] == list(COVERAGE_PACKAGES) and install.continue_on_failure
    pytest_step = plan.steps[-1]
    assert pytest_step.optional_argv == COVERAGE_ARGV
    assert pytest_step.coverage_report == COVERAGE_JSON
    ini = (ws / COVERAGE_INI).read_text(encoding="utf-8")
    assert "branch = True" in ini and "*/tests/*" in ini and ".harness-*" in ini


def test_file_counts_and_summary_are_measured_per_file_and_per_requirement():
    report = _report(
        {
            "./service.py": {
                "summary": {
                    "covered_lines": 3,
                    "num_statements": 4,
                    "covered_branches": 1,
                    "num_branches": 2,
                }
            },
            "other.py": {
                "summary": {"covered_lines": 1, "num_statements": 1, "covered_branches": 0, "num_branches": 0}
            },
            "broken.py": {"summary": "nope"},
        }
    )
    files = coverage.file_counts(report)
    assert set(files) == {"service.py", "other.py"}
    summary = coverage.summarize(files, {"REQ-1": ["service.py", "missing.py"], "REQ-2": ["nothing.py"]})
    assert summary["status"] == "measured" and summary["tool"] == "coverage.py"
    assert summary["totals"]["line_percent"] == 80.0
    assert summary["totals"]["branch_percent"] == 50.0
    assert summary["files"][0]["file"] == "other.py" and summary["files"][0]["branch_percent"] is None
    req1 = next(r for r in summary["requirements"] if r["id"] == "REQ-1")
    assert req1["measured_files"] == ["service.py"] and req1["line_percent"] == 75.0
    req2 = next(r for r in summary["requirements"] if r["id"] == "REQ-2")
    assert req2["line_percent"] is None and req2["measured_files"] == []


def test_a_missing_plugin_is_reported_as_not_measured_with_its_reason(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "service.py").write_text(SERVICE, encoding="utf-8")
    (ws / "tests").mkdir()
    (ws / "tests" / "test_service.py").write_text(TEST, encoding="utf-8")
    step = Step(
        "pytest",
        [
            sys.executable,
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
    if importlib.util.find_spec("pytest_cov"):
        pytest.skip("pytest-cov is installed in this interpreter; the missing-plugin path cannot run here")
    out = tmp_path / "out"
    report = execute_plan(RunPlan("python-pytest", [step]), ws, out)
    assert report["status"] == "passed"
    assert report["coverage"]["status"] == "not_measured"
    assert "install-coverage failed" in report["coverage"]["reason"]
    qa = build_qa_report(
        repo={"source_files": ["service.py"]},
        requirements={},
        design={"positive_tests": [], "traceability_matrix": {}},
        generation={"files": []},
        execution=report,
    )
    assert qa["measured_coverage"]["status"] == "not_measured"
    assert "Line and branch coverage: **not measured**" in render_markdown(qa)


def test_non_python_stacks_report_the_stack_they_were_not_measured_for():
    qa = build_qa_report(
        repo={"source_files": []},
        requirements={},
        design={"positive_tests": [], "traceability_matrix": {}},
        generation={"files": []},
        execution={"status": "passed", "adapter": "node-jest", "cases": []},
    )
    assert qa["measured_coverage"] == {
        "status": "not_measured",
        "tool": None,
        "reason": "coverage: not measured for node",
    }


def test_measured_run_reports_line_and_branch_numbers_for_the_source_only(tmp_path):
    pytest.importorskip(
        "pytest_cov", reason="pytest-cov is not installed in the harness venv (test-only tooling)"
    )
    ws = tmp_path / "ws"
    (ws / "tests").mkdir(parents=True)
    (ws / "service.py").write_text(SERVICE, encoding="utf-8")
    (ws / "tests" / "test_service.py").write_text(TEST, encoding="utf-8")
    (ws / ".harness-reports").mkdir()
    (ws / COVERAGE_INI).write_text(runners.COVERAGE_INI_TEXT, encoding="utf-8")
    step = Step(
        "pytest",
        [
            sys.executable,
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
    assert report["coverage"]["status"] == "measured"
    files = report["coverage"]["files"]
    assert "service.py" in files and not any(name.startswith("tests/") for name in files)
    assert files["service.py"]["num_branches"] == 2 and files["service.py"]["covered_branches"] == 1
    evidence = Path(report["coverage"]["runs"][0]["evidence"])
    assert json.loads(evidence.read_text(encoding="utf-8"))["files"]

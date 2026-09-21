"""The error classifier is used where errors actually happen: install steps, the build gate and the QA report."""

from __future__ import annotations

from pathlib import Path

from error_cases import MSVC, SRE

from maf_test_harness import build_gate, platform_compat, runners
from maf_test_harness.qa_report import build_qa_report
from maf_test_harness.stack import StackProfile


def _fake_run(log: str, exit_code: int = 1, status: str = "failed"):
    def run(argv, cwd, out, **kwargs):
        out.mkdir(parents=True)
        (out / "stdout.log").write_text("")
        (out / "stderr.log").write_text(log)
        return {"status": status, "exit_code": exit_code}

    return run


def test_failed_install_step_carries_a_diagnosis_and_a_hint(tmp_path, monkeypatch):
    step = runners.Step("install-requirements", ["python", "-m", "pip", "install", "x"], "setup")
    monkeypatch.setattr(runners, "run_command", _fake_run(MSVC))
    report = runners.execute_setup([step], tmp_path, tmp_path / "out")
    diagnosis = report["steps"][0]["diagnoses"][0]
    assert diagnosis["code"] == "ENV-PY-001" and diagnosis["category"] == "environment"
    assert report["status"] == "setup_failed"
    assert "ENV-PY-001" in report["error"] and "Build Tools" in report["error"]


def test_optional_step_failure_warning_names_the_error_code(tmp_path, monkeypatch):
    step = runners.Step(
        "install-requirements", ["python", "-m", "pip", "install", "x"], "setup", continue_on_failure=True
    )
    monkeypatch.setattr(runners, "run_command", _fake_run("ERROR: No matching distribution found for foo==1"))
    report = runners.execute_setup([step], tmp_path, tmp_path / "out")
    assert report["status"] == "ready" and "ENV-PY-002" in report["warnings"][0]


def test_blocked_step_is_understood_from_the_runners_own_status(tmp_path, monkeypatch):
    step = runners.Step("npm-install", ["npm", "install"], "setup")

    def run(argv, cwd, out, **kwargs):
        out.mkdir(parents=True)
        return {"status": "blocked", "error": "[WinError 2] The system cannot find the file specified"}

    monkeypatch.setattr(runners, "run_command", run)
    report = runners.execute_setup([step], tmp_path, tmp_path / "out")
    assert report["steps"][0]["diagnoses"][0]["code"] == "ENV-COMMON-010"


def test_build_gate_reports_an_environment_problem_instead_of_a_test_failure(tmp_path, monkeypatch):
    python = Path(platform_compat.venv_python(tmp_path / ".harness-venv"))
    python.parent.mkdir(parents=True)
    python.write_text("")
    monkeypatch.setattr(build_gate, "run_command", _fake_run(SRE))
    files = [{"path": "tests/test_a.py", "content": "def test_a():\n    assert True\n", "case_ids": ["TC-1"]}]
    report = build_gate.try_build(
        StackProfile(primary="python"), tmp_path, files, cache_dir=tmp_path / "c", out_root=tmp_path / "o"
    )
    assert report["status"] == "skipped" and report["errors"] == []
    assert report["environment_issues"][0]["code"] == "ENV-PY-006"
    assert "ENV-PY-006" in report["reason"] and "not a test defect" in report["reason"]


def test_build_gate_still_fails_a_genuine_test_defect(tmp_path, monkeypatch):
    python = Path(platform_compat.venv_python(tmp_path / ".harness-venv"))
    python.parent.mkdir(parents=True)
    python.write_text("")
    monkeypatch.setattr(
        build_gate, "run_command", _fake_run("E   ModuleNotFoundError: No module named 'engine'")
    )
    files = [{"path": "tests/test_a.py", "content": "from engine import x\n", "case_ids": ["TC-1"]}]
    report = build_gate.try_build(
        StackProfile(primary="python"), tmp_path, files, cache_dir=tmp_path / "c", out_root=tmp_path / "o"
    )
    assert report["status"] == "failed" and report["errors"]


def test_qa_report_turns_environment_diagnoses_into_environment_actions():
    execution = {
        "status": "no_test_evidence",
        "steps": [],
        "cases": [],
        "diagnoses": [
            {
                "code": "ENV-PY-001",
                "category": "environment",
                "title": "Package needs a C/C++ compiler",
                "hint": "Install Build Tools.",
            },
            {"code": "EXEC-101", "category": "test_defect", "title": "bad import", "hint": "fix"},
        ],
    }
    report = build_qa_report(
        repo={"name": "r", "source_files": []},
        requirements={"requirement_model": {"requirements": []}},
        design={},
        generation={"files": []},
        execution=execution,
    )
    actions = report["updates"]["environment_actions"]
    assert [a["tool"] for a in actions] == ["ENV-PY-001"] and "Install Build Tools" in actions[0]["action"]

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_all", ROOT / "scripts" / "check_all.py")
check_all = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_all  # dataclasses looks the module up by name
_spec.loader.exec_module(check_all)


def test_gate_runs_in_the_documented_order():
    names = [s.name for s in check_all.steps()]
    assert names == [
        "ruff check",
        "ruff format --check",
        "architecture map current",
        "version files agree",
        "skills lint (strict)",
        "pytest",
        "frontend build",
        "frontend typecheck",
        "frontend playwright",
        "desktop shell tests",
    ]


def test_groups_match_the_skip_flags():
    groups = {s.name: s.group for s in check_all.steps()}
    assert groups["frontend playwright"] == "ui" and groups["frontend build"] == "ui"
    assert groups["desktop shell tests"] == "desktop"
    assert groups["pytest"] == "core"


def test_skip_flags_skip_without_running(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(check_all, "run_step", lambda step: ran.append(step.name) or ("PASS", 0.0, ""))
    assert check_all.main(["--skip-ui", "--skip-desktop"]) == 0
    assert "frontend playwright" not in ran and "desktop shell tests" not in ran
    out = capsys.readouterr().out
    assert "SKIP  frontend build" in out and "PASSED" in out


def test_any_failure_fails_the_gate_and_every_step_still_runs(monkeypatch, capsys):
    ran = []

    def fake(step):
        ran.append(step.name)
        return ("FAIL", 0.0, "exit 1") if step.name == "pytest" else ("PASS", 0.0, "")

    monkeypatch.setattr(check_all, "run_step", fake)
    assert check_all.main(["--skip-ui", "--skip-desktop"]) == 1
    assert ran[-1] == "pytest" and "ruff check" in ran  # ran in order, failures do not stop the summary
    assert "FAILED (1 of" in capsys.readouterr().out

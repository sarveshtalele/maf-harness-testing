"""Installed packages that contradict each other (pip check) are an environment fact, detected and repaired by the harness.

Found on a live run on personal-finance-using-RAG: `pip install -r requirements.txt` failed on one package that needs a C++
compiler, the harness retried package by package, and `streamlit>=1.32.0` pulled `starlette 1.6.0` next to `fastapi==0.110.0`
(which needs `starlette<0.37`). pip printed the conflict and nothing read it; every generated test that built the app then
failed with `TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'` and was regenerated three times.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from maf_test_harness.envs import env_check, platform_compat, runners
from maf_test_harness.envs.runners import Step
from maf_test_harness.repo.stack import StackProfile
from maf_test_harness.tools import build_gate
from maf_test_harness.tools.registry import ToolContext
from maf_test_harness.tools.typed import default_registry

PIP_CHECK = """fastapi 0.110.0 has requirement starlette<0.37.0,>=0.36.3, but you have starlette 1.6.0.
foo 1.0 requires bar, which is not installed.
"""
CONFLICT = {
    "package": "fastapi",
    "version": "0.110.0",
    "requirement": "starlette<0.37.0,>=0.36.3",
    "dependency": "starlette",
    "installed": "1.6.0",
}
TYPE_ERROR = "E   TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'\n"


def problems(conflicts, unmet=()):
    return {"conflicts": list(conflicts), "unmet": list(unmet)}


def workspace_with_venv(tmp_path):
    ws = tmp_path / "ws"
    python = Path(platform_compat.venv_python(ws / ".harness-venv"))
    python.parent.mkdir(parents=True)
    python.write_text("")
    return ws


def fake_run(log="", exit_code=1, status="failed", record=None):
    def run(argv, cwd, out, **kwargs):
        out.mkdir(parents=True)
        (out / "stdout.log").write_text("")
        (out / "stderr.log").write_text(log)
        if record is not None:
            record.append(list(argv))
        return {"status": status, "exit_code": exit_code}

    return run


# --- detection ---------------------------------------------------------------------------------------------


def test_pip_check_output_becomes_structured_conflicts_and_ignores_missing_packages():
    assert env_check.parse_pip_check(PIP_CHECK) == [CONFLICT]
    assert env_check.parse_pip_check("No broken requirements found.\n") == []
    assert env_check.parse_pip_check("") == []


def test_dependency_conflicts_asks_pip_check_in_the_venv(tmp_path, monkeypatch):
    ws = workspace_with_venv(tmp_path)
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return SimpleNamespace(stdout=PIP_CHECK, stderr="", returncode=1)

    monkeypatch.setattr(env_check.subprocess, "run", run)
    assert env_check.dependency_conflicts(ws) == [CONFLICT]
    assert seen[0][1:4] == ["-m", "pip", "check"]
    assert env_check.dependency_conflicts(tmp_path / "no-venv") == []


# --- classification and the build gate -------------------------------------------------------------------


def test_the_starlette_signature_is_environmental_even_without_pip_check():
    from maf_test_harness import errors

    found = errors.classify(TYPE_ERROR, "python")
    assert found[0].code == "ENV-PY-009" and errors.is_environmental(found)


def test_the_build_gate_does_not_blame_the_test_when_the_environment_is_inconsistent(tmp_path, monkeypatch):
    ws = workspace_with_venv(tmp_path)
    monkeypatch.setattr(
        build_gate, "run_command", fake_run("E   TypeError: f() got an unexpected keyword argument 'x'")
    )
    monkeypatch.setattr(env_check, "dependency_conflicts", lambda workspace: [CONFLICT])
    files = [{"path": "tests/test_a.py", "content": "def test_a():\n    assert True\n", "case_ids": ["TC-1"]}]
    report = build_gate.try_build(
        StackProfile(primary="python"), ws, files, cache_dir=tmp_path / "c", out_root=tmp_path / "o"
    )
    assert report["status"] == "skipped" and report["errors"] == []
    assert report["environment_issues"][0]["code"] == "ENV-PY-009"
    assert "not a test defect" in report["reason"]


def test_the_same_error_in_a_consistent_environment_is_still_a_test_defect(tmp_path, monkeypatch):
    ws = workspace_with_venv(tmp_path)
    monkeypatch.setattr(
        build_gate, "run_command", fake_run("E   TypeError: f() got an unexpected keyword argument 'x'")
    )
    monkeypatch.setattr(env_check, "dependency_conflicts", lambda workspace: [])
    files = [{"path": "tests/test_a.py", "content": "def test_a():\n    assert True\n", "case_ids": ["TC-1"]}]
    report = build_gate.try_build(
        StackProfile(primary="python"), ws, files, cache_dir=tmp_path / "c", out_root=tmp_path / "o"
    )
    assert report["status"] == "failed" and report["errors"]


def test_the_build_gate_asks_pip_check_at_most_once_per_check(tmp_path, monkeypatch):
    ws = workspace_with_venv(tmp_path)
    calls = []
    monkeypatch.setattr(
        build_gate, "run_command", fake_run("E   TypeError: f() got an unexpected keyword argument 'x'")
    )
    monkeypatch.setattr(env_check, "dependency_conflicts", lambda workspace: calls.append(1) or [CONFLICT])
    files = [
        {"path": f"tests/test_{n}.py", "content": "def test_a():\n    assert True\n", "case_ids": [f"TC-{n}"]}
        for n in range(3)
    ]
    build_gate.try_build(
        StackProfile(primary="python"), ws, files, cache_dir=tmp_path / "c", out_root=tmp_path / "o"
    )
    assert calls == [1]


# --- repair ---------------------------------------------------------------------------------------------------


def test_repair_resolves_jointly_with_the_declared_requirements_in_one_round(tmp_path, monkeypatch):
    ws = workspace_with_venv(tmp_path)
    state = {"conflicts": [CONFLICT]}
    ran = []

    def run(argv, cwd, out, **kwargs):
        ran.append(list(argv))
        state["conflicts"] = []  # the joint resolution backtracks to a consistent set
        return {"status": "command_succeeded", "exit_code": 0}

    monkeypatch.setattr(runners, "run_command", run)
    monkeypatch.setattr(env_check, "dependency_problems", lambda workspace: problems(state["conflicts"]))
    monkeypatch.setattr(
        env_check, "installed_distributions", lambda workspace: {"fastapi", "streamlit", "uvicorn"}
    )
    specs = ["fastapi==0.110.0", "streamlit>=1.32.0", "uvicorn[standard]==0.29.0", "chromadb==0.4.24"]
    summary = runners.repair_conflicts(ws, tmp_path / "out", specs)
    assert summary["remaining"] == [] and len(summary["repaired"]) == 1 and summary["found"] == [CONFLICT]
    installed = ran[0][ran[0].index("-q") + 1 :]
    assert installed == [
        "fastapi==0.110.0",
        "streamlit>=1.32.0",
        "uvicorn[standard]==0.29.0",
        "starlette<0.37.0,>=0.36.3",
    ]
    assert "chromadb==0.4.24" not in installed  # it never installed, so it is not asked for again


def test_repair_never_loosens_a_pin_and_never_repeats_itself(tmp_path, monkeypatch):
    ws = workspace_with_venv(tmp_path)
    ran = []
    monkeypatch.setattr(
        runners,
        "run_command",
        lambda argv, cwd, out, **k: ran.append(argv) or {"status": "command_succeeded"},
    )
    monkeypatch.setattr(
        env_check, "dependency_problems", lambda workspace: problems([CONFLICT])
    )  # nothing can fix it
    monkeypatch.setattr(env_check, "installed_distributions", lambda workspace: set())
    summary = runners.repair_conflicts(ws, tmp_path / "out", [])
    assert summary["remaining"] == [CONFLICT]
    assert len(ran) == 1  # round 2 would be the same install, so it is skipped: bounded, no ping-pong


def test_repair_is_a_noop_on_a_consistent_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(env_check, "dependency_problems", lambda workspace: problems([]))
    monkeypatch.setattr(
        runners, "run_command", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    )
    assert runners.repair_conflicts(tmp_path, tmp_path / "out", ["x==1"])["found"] == []


# --- the install step ------------------------------------------------------------------------------------------


def test_a_failed_bulk_install_is_followed_by_one_joint_resolution_and_a_conflict_check(
    tmp_path, monkeypatch
):
    ws = workspace_with_venv(tmp_path)
    (ws / "requirements.txt").write_text(
        "fastapi==0.110.0\nstreamlit>=1.32.0\nchromadb==0.4.24\n", encoding="utf-8"
    )
    ran = []

    def run(argv, cwd, out, **kwargs):
        out.mkdir(parents=True)
        (out / "stdout.log").write_text("")
        (out / "stderr.log").write_text(
            "ERROR: Failed building wheel for chroma-hnswlib"
            if "chromadb==0.4.24" in argv or "-r" in argv
            else ""
        )
        ran.append(list(argv))
        bad = "chromadb==0.4.24" in argv or "-r" in argv
        return {"status": "failed" if bad else "command_succeeded", "exit_code": 1 if bad else 0}

    monkeypatch.setattr(runners, "run_command", run)
    conflicts = iter([[CONFLICT], []])
    monkeypatch.setattr(env_check, "dependency_problems", lambda workspace: problems(next(conflicts)))
    monkeypatch.setattr(env_check, "installed_distributions", lambda workspace: {"fastapi", "streamlit"})
    py = str(platform_compat.venv_python(ws / ".harness-venv"))
    step = Step(
        "install-requirements",
        [py, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
        "setup",
        continue_on_failure=True,
    )
    report = runners.execute_setup([step], ws, tmp_path / "out")
    assert report["status"] == "ready"
    assert report["steps"][0]["fallback"]["installed"] == ["fastapi==0.110.0", "streamlit>=1.32.0"]
    assert report["steps"][0]["fallback"]["failed"] == ["chromadb==0.4.24"]
    joint = [a for a in ran if a[-2:] == ["fastapi==0.110.0", "streamlit>=1.32.0"]]
    assert joint, "the specs that installed are resolved together in one pip run"
    assert (
        report["dependency_conflicts"]["found"] == [CONFLICT]
        and report["dependency_conflicts"]["remaining"] == []
    )
    assert any("repaired" in w and "starlette" in w for w in report["warnings"])


def test_a_clean_install_reports_no_conflicts(tmp_path, monkeypatch):
    ws = workspace_with_venv(tmp_path)
    monkeypatch.setattr(runners, "run_command", fake_run("", 0, "command_succeeded"))
    monkeypatch.setattr(env_check, "dependency_problems", lambda workspace: problems([]))
    py = str(platform_compat.venv_python(ws / ".harness-venv"))
    report = runners.execute_setup(
        [Step("install-x", [py, "-m", "pip", "install", "x"], "setup")], ws, tmp_path / "out"
    )
    assert report["status"] == "ready" and "dependency_conflicts" not in report


# --- tools -----------------------------------------------------------------------------------------------------


def test_the_typed_tool_reports_conflicts_with_the_error_code(tmp_path, monkeypatch):
    monkeypatch.setattr(env_check, "dependency_problems", lambda workspace: problems([CONFLICT]))
    registry = default_registry(ToolContext(workspace=tmp_path))
    result = registry.call("list_dependency_conflicts", {})
    assert result.ok and result.code == "ENV-PY-009" and result.category == "environment"
    assert "fastapi needs starlette<0.37.0,>=0.36.3, has 1.6.0" in result.evidence
    assert registry.call("list_dependency_conflicts", {}).data["conflicts"] == [CONFLICT]
    monkeypatch.setattr(env_check, "dependency_problems", lambda workspace: problems([]))
    assert registry.call("list_dependency_conflicts", {}).code == "OK"
    assert default_registry(ToolContext()).call("list_dependency_conflicts", {}).code == "NO-WORKSPACE"


def test_the_central_environment_gaps_tool_now_lists_conflicts_too(tmp_path, monkeypatch):
    import asyncio
    import json

    from maf_test_harness.tools.agent_tools import HarnessToolbox
    from maf_test_harness.tools.code_intel import RepoContext

    monkeypatch.setattr(env_check, "missing_packages", lambda workspace: ["chromadb"])
    monkeypatch.setattr(env_check, "dependency_conflicts", lambda workspace: [CONFLICT])
    box = HarnessToolbox(lambda: RepoContext(root=tmp_path, source_files=[], workspace=tmp_path))
    tool = box._tools()["environment_gaps"]
    payload = json.loads(asyncio.run(tool.invoke(arguments={}))[0].text)
    assert payload == {"missing": ["chromadb"], "conflicts": [CONFLICT]}

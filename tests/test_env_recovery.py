"""Bounded automatic recovery of setup failures: the chooser (pure) and execute_setup with real subprocesses."""

import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from maf_test_harness.envs import platform_compat, recovery
from maf_test_harness.envs.runners import VENV_DIR, Step, execute_setup


def diag(code):
    return [SimpleNamespace(code=code)]


def workspace_of(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ws


# --- the chooser ----------------------------------------------------------------------------------------------


def test_timeout_doubles_up_to_the_cap(tmp_path):
    step = Step("install", ["x"], "setup", timeout=100)
    action, considered = recovery.choose(step, diag("ENV-COMMON-001"), [], workspace_of(tmp_path))
    assert action.recipe == "retry-longer" and action.step.timeout == 200 and step.timeout == 100
    assert considered[-1]["status"] == "applied"
    capped = Step("install", ["x"], "setup", timeout=recovery.MAX_TIMEOUT)
    action, considered = recovery.choose(capped, diag("ENV-COMMON-001"), [], workspace_of(tmp_path))
    assert action is None and considered[-1]["status"] == "not_applicable"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["mvn", "test-compile"], ["mvn", "-U", "test-compile"]),
        (["C:/tools/mvn.cmd", "-q", "verify"], ["C:/tools/mvn.cmd", "-U", "-q", "verify"]),
        (["./gradlew", "test"], ["./gradlew", "test", "--refresh-dependencies"]),
    ],
)
def test_retry_update_forces_dependency_refresh(tmp_path, argv, expected):
    action, _ = recovery.choose(
        Step("build", argv, "setup"), diag("ENV-JAVA-003"), [], workspace_of(tmp_path)
    )
    assert action.recipe == "retry-update" and action.step.argv == expected


def test_retry_update_is_not_applied_twice_or_to_other_tools(tmp_path):
    ws = workspace_of(tmp_path)
    already = Step("build", ["mvn", "-U", "test"], "setup")
    assert recovery.choose(already, diag("ENV-JAVA-003"), [], ws)[0] is None
    assert recovery.choose(Step("build", ["npm", "ci"], "setup"), diag("ENV-JAVA-003"), [], ws)[0] is None


def test_retry_restore_only_for_dotnet(tmp_path):
    ws = workspace_of(tmp_path)
    action, _ = recovery.choose(
        Step("restore", ["dotnet", "restore"], "setup"), diag("ENV-DOTNET-003"), [], ws
    )
    assert action.recipe == "retry-restore" and action.step.argv == ["dotnet", "restore"]
    assert recovery.choose(Step("restore", ["npm", "ci"], "setup"), diag("ENV-DOTNET-003"), [], ws)[0] is None


def test_a_recipe_is_never_tried_twice_for_a_step(tmp_path):
    step = Step("install", ["x"], "setup", timeout=10)
    action, considered = recovery.choose(
        step, diag("ENV-COMMON-001"), ["retry-longer"], workspace_of(tmp_path)
    )
    assert action is None and considered == []


def test_max_attempts_bounds_the_recipes_per_step(tmp_path):
    step = Step("build", ["mvn", "x"], "setup")
    action, considered = recovery.choose(
        step, diag("ENV-JAVA-003"), ["something-else"], workspace_of(tmp_path)
    )
    assert action is None and considered[0]["status"] == "attempts_exhausted"


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("ENV-JAVA-002", "needs_approval"),  # ask: a JDK download
        ("ENV-PY-001", "manual"),  # needs a compiler: only explained
        ("ENV-COMMON-002", "manual"),  # disk full: no recipe at all
    ],
)
def test_ask_and_manual_diagnoses_are_reported_not_acted_on(tmp_path, code, status):
    action, considered = recovery.choose(Step("s", ["x"], "setup"), diag(code), [], workspace_of(tmp_path))
    assert action is None and considered and all(c["status"] == status for c in considered)


def test_no_diagnosis_or_unknown_code_means_no_action(tmp_path):
    step = Step("s", ["x"], "setup")
    assert recovery.choose(step, [], [], workspace_of(tmp_path)) == (None, [])
    assert recovery.choose(step, diag("NOPE-000"), [], workspace_of(tmp_path)) == (None, [])


def test_recreate_venv_only_ever_deletes_the_harness_venv(tmp_path):
    ws = workspace_of(tmp_path)
    (ws / VENV_DIR).mkdir()
    (ws / VENV_DIR / "marker").write_text("x")
    (ws / "src").mkdir()
    action, _ = recovery.choose(Step("install", ["pip"], "setup"), diag("ENV-PY-004"), [], ws)
    assert action.recipe == "recreate-venv" and action.rewind_to == "create-venv"
    action.prepare()
    assert not (ws / VENV_DIR).exists() and (ws / "src").exists()
    same_step, _ = recovery.choose(Step("create-venv", ["python"], "setup"), diag("ENV-PY-008"), [], ws)
    assert same_step.rewind_to is None


def test_preview_describes_without_doing(tmp_path):
    plan = recovery.preview(["mvn", "test"], "Could not resolve dependencies for project", "java", tmp_path)
    assert plan["diagnosis"]["code"] == "ENV-JAVA-003"
    assert plan["would_apply"]["recipe"] == "retry-update" and plan["would_apply"]["argv"][:2] == [
        "mvn",
        "-U",
    ]
    assert recovery.preview(["x"], "all fine", None)["would_apply"] is None


# --- execute_setup with real processes --------------------------------------------------------------------------


def script(ws, name, body):
    path = ws / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return [sys.executable, str(path)]


def test_a_timed_out_step_is_retried_once_with_a_longer_limit(tmp_path):
    ws = workspace_of(tmp_path)
    argv = script(
        ws,
        "slow.py",
        """
        import pathlib, time
        marker = pathlib.Path("started")
        first = not marker.exists()
        marker.write_text("x")
        if first:
            time.sleep(30)
        """,
    )
    report = execute_setup([Step("slow-install", argv, "setup", timeout=1.5)], ws, tmp_path / "out")
    assert report["status"] == "ready"
    assert [r["recipe"] for r in report["recoveries"]] == ["retry-longer"]
    first, second = report["steps"]
    assert first["status"] == "timeout" and second["status"] == "command_succeeded" and second["attempt"] == 2
    assert first["recovery"][-1]["status"] == "applied"
    assert (tmp_path / "out" / "01-slow-install-retry1").is_dir()  # each attempt keeps its own evidence
    assert any("retry-longer" in w for w in report["warnings"])
    assert json.loads((tmp_path / "out" / "report.json").read_text())["recoveries"]


def test_recreate_venv_rewinds_to_create_venv_and_then_succeeds(tmp_path):
    ws = workspace_of(tmp_path)
    venv_python = Path(platform_compat.venv_python(ws / VENV_DIR))
    make = script(
        ws,
        "make_venv.py",
        f"""
        import pathlib
        target = pathlib.Path({str(venv_python)!r})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stub")
        counter = pathlib.Path("creates")
        counter.write_text(str(int(counter.read_text() or 0) + 1) if counter.exists() else "1")
        """,
    )
    install = script(
        ws,
        "install.py",
        """
        import pathlib, sys
        if int(pathlib.Path("creates").read_text()) < 2:
            sys.stderr.write("error: externally-managed-environment\\n")
            sys.exit(1)
        """,
    )
    steps = [Step("create-venv", make, "setup"), Step("install", install, "setup")]
    report = execute_setup(steps, ws, tmp_path / "out")
    assert report["status"] == "ready", report
    assert [r["recipe"] for r in report["recoveries"]] == ["recreate-venv"]
    assert [(s["name"], s["status"]) for s in report["steps"]] == [
        ("create-venv", "command_succeeded"),
        ("install", "failed"),
        ("create-venv", "command_succeeded"),
        ("install", "command_succeeded"),
    ]
    assert venv_python.exists() and (ws / "creates").read_text() == "2"


def test_an_unrecoverable_failure_still_fails_with_the_reason_and_what_was_considered(tmp_path):
    ws = workspace_of(tmp_path)
    argv = script(
        ws,
        "fail.py",
        "import sys; sys.stderr.write('ERROR: Package requires a different Python: 3.9 not in >=3.12'); sys.exit(1)",
    )
    report = execute_setup([Step("pip-install", argv, "setup")], ws, tmp_path / "out")
    assert report["status"] == "setup_failed" and "ENV-PY-003" in report["error"]
    assert report["recoveries"] == []
    assert (
        report["steps"][0]["recovery"][0]["status"] == "needs_approval"
    )  # a Python download waits for a human


def test_recovery_can_be_switched_off(tmp_path):
    ws = workspace_of(tmp_path)
    argv = script(ws, "slow.py", "import time; time.sleep(30)")
    report = execute_setup([Step("s", argv, "setup", timeout=1)], ws, tmp_path / "out", recover=False)
    assert (
        report["status"] == "setup_failed"
        and len(report["steps"]) == 1
        and "recovery" not in report["steps"][0]
    )


def test_total_recoveries_per_run_are_capped(tmp_path, monkeypatch):
    from maf_test_harness.envs import runners

    monkeypatch.setattr(runners, "MAX_RECOVERIES", 1)
    ws = workspace_of(tmp_path)
    argv = script(ws, "slow.py", "import time; time.sleep(30)")
    steps = [Step("a", argv, "setup", timeout=1), Step("b", argv, "setup", timeout=1)]
    report = execute_setup(steps, ws, tmp_path / "out")
    assert report["status"] == "setup_failed" and len(report["recoveries"]) == 1


def test_a_successful_setup_is_unchanged_by_recovery(tmp_path):
    ws = workspace_of(tmp_path)
    report = execute_setup([Step("ok", script(ws, "ok.py", "pass"), "setup")], ws, tmp_path / "out")
    assert report["status"] == "ready" and report["recoveries"] == [] and "attempt" not in report["steps"][0]


def test_the_workflow_recovers_a_timed_out_install_and_reports_it(tmp_path, monkeypatch):
    """End to end through the real workflow: gate approval, real subprocess, recovery, review artifact, report."""
    from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
    from test_execution_stage import local_pytest_plan

    import maf_test_harness.workflow as wf
    from maf_test_harness.monitoring.report import build_report
    from maf_test_harness.ui.runtime_service import RuntimeService

    slow = tmp_path / "slow.py"
    slow.write_text(
        "import pathlib, time\n"
        "m = pathlib.Path('started')\n"
        "first = not m.exists()\n"
        "m.write_text('x')\n"
        "time.sleep(30) if first else None\n",
        encoding="utf-8",
    )

    def plan(stack, root, cache_dir, timeout=900, needs=None):
        base = local_env_plan(stack, root, cache_dir, timeout, needs)
        base["steps"][0].update(name="slow-install", argv=[sys.executable, str(slow)], timeout=1.5)
        return base

    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    monkeypatch.setattr(wf, "build_plan", local_pytest_plan)
    monkeypatch.setattr(wf, "build_environment_plan", plan)
    service = RuntimeService()
    try:
        host = wf.Harness(
            tmp_path / "run",
            skills_root=Path("skills"),
            runtime=ScriptedStageRuntime(),
            cache_dir=tmp_path / "c",
        )
        service.submit(host.start("https://github.com/acme/demo")).result(15)
        for _ in range(4):  # clone, repo, requirements, approve the install
            service.submit(host.respond(host.view["pending"]["id"], {"decision": "approve"})).result(120)
        artifact = host.view["pending"]["details"]["artifact"]
        assert artifact["status"] == "ready"
        assert [r["recipe"] for r in artifact["report"]["recoveries"]] == ["retry-longer"]
        automation = build_report(host.run_dir)["automation"]
        assert any(r.get("recipe") == "retry-longer" and "note" in r for r in automation["recoveries"])
        assert automation["hooks"]["events"]["StageGate"] == 4
    finally:
        service.close()

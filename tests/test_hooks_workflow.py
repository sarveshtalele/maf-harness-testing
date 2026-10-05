"""Hooks in the real workflow: SessionStart, StageGate (a veto leaves the gate pending), OnEnvError, OnExecError, Stop."""

import json
from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
from test_execution_stage import local_pytest_plan, to_execution_gate

import maf_test_harness.workflow as wf
from maf_test_harness.envs.toolchains import ToolchainError
from maf_test_harness.hooks import (
    ON_ENV_ERROR,
    ON_EXEC_ERROR,
    SESSION_START,
    STAGE_GATE,
    STOP,
    Decision,
    HookConfigError,
    HookEngine,
    HookSpec,
)
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow_steps import execution as execution_steps


class Recorder:
    """A hook engine that records every observer event and can veto gates."""

    def __init__(self, veto=None):
        self.events, self.veto = [], veto
        names = (SESSION_START, STAGE_GATE, ON_ENV_ERROR, ON_EXEC_ERROR, STOP)
        self.engine = HookEngine([HookSpec(name, self.handle, "recorder") for name in names])

    def handle(self, event):
        self.events.append((event.name, event.stage, dict(event.data)))
        if event.name == STAGE_GATE and self.veto and self.veto(event):
            return Decision("deny", "policy says not now")
        return Decision()

    def names(self):
        return [name for name, _, _ in self.events]


@pytest.fixture
def drive(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    monkeypatch.setattr(execution_steps, "build_plan", local_pytest_plan)
    monkeypatch.setattr(execution_steps, "build_environment_plan", local_env_plan)
    service = RuntimeService()

    def make(runtime, hooks=None):
        host = wf.Harness(
            tmp_path / "run",
            skills_root=Path("skills"),
            requirements_mode="model",
            runtime=runtime,
            cache_dir=tmp_path / "cache",
            hooks=hooks,
        )
        service.submit(host.start("https://github.com/acme/demo")).result(15)

        def answer(**response):
            return service.submit(host.respond(host.view["pending"]["id"], response)).result(120)

        return host, answer

    yield make
    service.close()


def audit_names(host):
    return [json.loads(line)["event"] for line in (host.run_dir / "audit.jsonl").read_text().splitlines()]


def test_session_start_fires_on_construction(tmp_path):
    first = Recorder()
    wf.Harness(tmp_path / "run", skills_root=Path("skills"), hooks=first.engine)
    assert first.events[0][0] == SESSION_START and first.events[0][2] == {"run": "run", "resumed": False}


def test_the_runtime_shares_the_hook_engine(drive):
    runtime = ScriptedStageRuntime()
    host, _ = drive(runtime)
    assert runtime.hooks is host.hooks


def test_gates_and_stop_reach_hooks_across_a_whole_run(drive):
    recorder = Recorder()
    host, answer = drive(ScriptedStageRuntime(), recorder.engine)
    to_execution_gate(host, answer)
    answer(decision="approve")  # run the tests
    final = answer(decision="approve")
    assert final["status"] == "complete"
    gates = [
        (stage, data["kind"], data["decision"]) for name, stage, data in recorder.events if name == STAGE_GATE
    ]
    assert gates[0] == ("repo_analysis", "tool", "approve")
    assert gates[-1] == ("test_execution", "review", "approve")
    assert len(gates) == 10
    assert recorder.names()[0] == SESSION_START and recorder.names()[-1] == STOP
    assert recorder.events[-1][2] == {"reason": "complete"}
    assert ON_ENV_ERROR not in recorder.names() and ON_EXEC_ERROR not in recorder.names()


def test_a_stage_gate_veto_keeps_the_approval_pending_until_the_hook_allows_it(drive):
    recorder = Recorder(veto=lambda e: e.stage == "environment_setup" and e.data["kind"] == "tool")
    host, answer = drive(ScriptedStageRuntime(), recorder.engine)
    for _ in range(6):  # clone, repo, requirements
        answer(decision="approve")
    pending = host.view["pending"]
    assert pending["stage"] == "environment_setup" and pending["kind"] == "tool"
    with pytest.raises(PermissionError, match="Blocked by hook .*policy says not now"):
        answer(decision="approve")
    assert host.view["pending"]["id"] == pending["id"]  # not consumed, still the same gate
    assert pending["id"] not in host.view["consumed"] and not (host.run_dir / "environment").exists()
    assert not host.hooks.context.get("allow_downloads")  # no consent before the environment approval
    recorder.veto = None  # the policy is lifted
    answer(decision="approve")
    assert host.hooks.context["allow_downloads"] is True  # the approved plan covers its toolchain downloads
    assert (host.run_dir / "environment" / "attempt-1" / "report.json").exists()


def test_a_veto_only_blocks_what_the_hook_vetoed(drive):
    recorder = Recorder(veto=lambda e: e.data["decision"] == "approve")
    host, answer = drive(ScriptedStageRuntime(), recorder.engine)
    with pytest.raises(PermissionError):
        answer(decision="approve")
    assert answer(decision="stop")["status"] == "stopped"


def test_a_failed_environment_install_fires_on_env_error(drive, monkeypatch):
    def refuse(*args, **kwargs):
        raise ToolchainError("jdk 17 is not installed and downloading was not approved.")

    monkeypatch.setattr(execution_steps, "provision_for", refuse)
    recorder = Recorder()
    host, answer = drive(ScriptedStageRuntime(), recorder.engine)
    for _ in range(6):
        answer(decision="approve")
    answer(decision="approve")  # install
    env = [(stage, data) for name, stage, data in recorder.events if name == ON_ENV_ERROR]
    assert len(env) == 1 and env[0][0] == "environment_setup"
    assert env[0][1]["status"] == "setup_failed" and "jdk 17" in env[0][1]["error"]
    assert env[0][1]["attempt"] == 1


def test_a_failing_test_run_fires_on_exec_error(drive):
    recorder = Recorder()
    host, answer = drive(ScriptedStageRuntime(test_body="assert add(1, 2) == 4"), recorder.engine)
    to_execution_gate(host, answer)
    answer(decision="approve")
    execution = [(stage, data) for name, stage, data in recorder.events if name == ON_EXEC_ERROR]
    assert len(execution) == 1 and execution[0][0] == "test_execution"
    assert execution[0][1]["status"] != "passed" and execution[0][1]["attempt"] == 1


def test_the_default_engine_audits_gates(drive):
    host, answer = drive(ScriptedStageRuntime())
    answer(decision="approve")
    gates = [
        json.loads(line)
        for line in (host.run_dir / "audit.jsonl").read_text().splitlines()
        if '"hook_event"' in line
    ]
    assert gates and gates[0]["hook_event"] == STAGE_GATE and gates[0]["stage"] == "repo_analysis"
    assert "state" not in gates[0]["data"]


def test_the_default_engine_carries_the_workspace_as_the_only_writable_root(tmp_path):
    host = wf.Harness(tmp_path / "run", skills_root=Path("skills"))
    assert host.hooks.context["writable_roots"] == [str(host.workspace)]
    assert host.hooks.context["allow_downloads"] is False
    decision, _ = host.hooks.before_tool("write_test_file", {"path": "../escape.py"})
    assert decision.blocked


def test_harness_hooks_off_disables_every_hook_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "HOOKS", "off")
    host = wf.Harness(tmp_path / "run", skills_root=Path("skills"))
    assert host.hooks.hooks == [] and "hooks_disabled" in audit_names(host)


def test_a_malformed_hooks_file_stops_the_run_before_it_starts(tmp_path, monkeypatch):
    bad = tmp_path / "hooks.json"
    bad.write_text('{"hooks": [{"event": "PreToolUse"}]}', encoding="utf-8")
    monkeypatch.setattr("maf_test_harness.hooks.config.config_paths", lambda project_root=None: [bad])
    with pytest.raises(HookConfigError, match="exactly one of"):
        wf.Harness(tmp_path / "run", skills_root=Path("skills"))

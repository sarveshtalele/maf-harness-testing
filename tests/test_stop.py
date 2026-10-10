"""Stop semantics: stopping -> stopped ordering, explicit cancel points, killed subprocesses, CLI Ctrl-C."""

import asyncio
import base64
import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from agent_framework import MiddlewareFailure
from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
from test_execution_stage import local_pytest_plan
from test_ui_api import wait

import maf_test_harness.workflow as wf
from maf_test_harness import cli
from maf_test_harness.envs import environment
from maf_test_harness.envs.platform_compat import RUN_CANCEL, raise_if_cancelled
from maf_test_harness.hooks.engine import HookEngine, command_handler
from maf_test_harness.hooks.events import HookEvent
from maf_test_harness.hooks.maf_bridge import HookFunctionMiddleware
from maf_test_harness.ui.api import LocalAPI
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow_steps import execution as execution_steps
from maf_test_harness.workflow_steps import generation as generation_steps

SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]
CASE = {
    "priority": "P1",
    "level": "unit",
    "actor": "Developer",
    "target": "service.add",
    "description": "Another addition.",
    "preconditions": [],
    "data_notes": "2 and 2",
    "expected_result": "add(2, 2) is 4",
    "steps": ["Call add", "Compare"],
}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    monkeypatch.setattr(execution_steps, "build_plan", local_pytest_plan)
    monkeypatch.setattr(execution_steps, "build_environment_plan", local_env_plan)
    local = LocalAPI(tmp_path / "runs", requirements_mode="model", runtime_factory=ScriptedStageRuntime)
    yield local
    wait(local)
    local.close()


def approve(api, key):
    run = api.read(key)
    api.respond(key, {"request_id": run["pending"]["id"], "decision": "approve"})
    wait(api)
    assert not api.errors


def stage_in_flight(api, key, runtime_type):
    """Start a run whose second stage blocks inside the model call; return once it is in flight."""
    api.runtime_factory = runtime_type
    key = api.start({"repo_url": "https://github.com/acme/slow"})["id"]
    wait(api)
    approve(api, key)
    gate = api.read(key)["pending"]
    api.respond(key, {"request_id": gate["id"], "decision": "approve"})
    return key


def test_stopping_is_visible_until_the_operation_really_ends_then_stopped(api):
    entered, release = threading.Event(), threading.Event()

    class Slow(ScriptedStageRuntime):
        async def run_stage(self, **kwargs):
            entered.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await asyncio.to_thread(release.wait, 15)  # cleanup that outlives the stop request
                raise

    key = stage_in_flight(api, None, Slow)
    assert entered.wait(10)
    api.stop_run(key)
    run = api.read(key)
    assert run["status"] == "stopping" and run["busy"] and run["actions"] == []
    api.stop_run(key)  # a second click is a no-op, not a second stop
    assert api.read(key)["status"] == "stopping"
    release.set()
    wait(api)
    run = api.read(key)
    assert run["status"] == "stopped" and run["pending"] is None and not api.errors
    audit = (api.run_path(key) / "audit.jsonl").read_text()
    assert audit.count("run_stopped_by_user") == 1


def test_service_stays_busy_until_work_that_ignores_cancel_really_ends():
    service = RuntimeService()
    stopped = []

    async def stubborn():
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:  # swallows cancellation, like a thread stuck in a blocking call
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                pass

    async def other():
        return "never runs"

    try:
        service.submit(stubborn())
        time.sleep(0.1)
        stop = service.stop_active(lambda: stopped.append(1))
        time.sleep(0.3)  # past any grace period, while the task still runs
        assert not stop.done() and stopped == []
        with pytest.raises(RuntimeError, match="already running"):
            service.submit(other())
        stop.result(10)
        assert stopped == [1]
    finally:
        service.close()


def test_stop_between_generation_batches_makes_no_further_model_calls(api, monkeypatch):
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 1)
    batches = []

    class Cancelling(ScriptedStageRuntime):
        async def run_stage(self, **kwargs):
            result = await super().run_stage(**kwargs)
            review = getattr(kwargs.get("response_format"), "__name__", "") == "TestReviewDraft"
            if kwargs["stage"] == "test_generation" and not review:
                batches.append(1)
                RUN_CANCEL.get().set()  # Stop arrives while the first batch is being finished
            return result

    api.runtime_factory = lambda: Cancelling(
        extra_cases=[{"title": "add two", **CASE}, {"title": "add three", **CASE}]
    )
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    while api.read(key)["pending"]["stage"] != "test_design":  # approving the design review starts generation
        approve(api, key)
    gate = api.read(key)["pending"]
    api.respond(key, {"request_id": gate["id"], "decision": "approve"})
    wait(api)
    assert len(batches) == 1, "the loop must stop before the second batch's model call"
    assert api.read(key)["state"]["artifacts"].get("test_generation") is None


def test_raise_if_cancelled_uses_current_run_event():
    raise_if_cancelled()  # no event -> no-op
    event = threading.Event()
    token = RUN_CANCEL.set(event)
    try:
        raise_if_cancelled()
        event.set()
        with pytest.raises(asyncio.CancelledError):
            raise_if_cancelled()
    finally:
        RUN_CANCEL.reset(token)


def _run_stopped(fn):
    """Run fn in a worker thread under a RuntimeService, press Stop, and report how fn ended and how fast."""
    service = RuntimeService()
    outcome, done = {}, threading.Event()

    def target():
        started = time.monotonic()
        try:
            outcome["value"] = fn()
        except BaseException as exc:
            outcome["exc"] = exc
        outcome["elapsed"] = time.monotonic() - started
        done.set()

    async def run():
        await asyncio.to_thread(target)

    try:
        service.submit(run())
        time.sleep(1)
        service.stop_active(lambda: None)
        assert done.wait(15), "the blocking subprocess ignored Stop"
    finally:
        service.close()
    return outcome


def test_environment_probe_subprocess_is_killed_on_stop(monkeypatch):
    monkeypatch.setitem(environment.TOOLS, sys.executable, (SLEEP, "n/a"))
    outcome = _run_stopped(lambda: environment._probe(sys.executable))
    assert isinstance(outcome.get("exc"), InterruptedError) and outcome["elapsed"] < 15


def test_hook_command_subprocess_is_killed_on_stop():
    handler = command_handler(SLEEP, timeout=60)
    outcome = _run_stopped(lambda: handler(HookEvent(name="PreToolUse", stage="x", tool="t")))
    assert isinstance(outcome.get("exc"), InterruptedError) and outcome["elapsed"] < 15


def test_hook_command_still_runs_and_times_out_normally():
    ok = command_handler([sys.executable, "-c", "import sys; sys.stdin.read()"], timeout=20)
    assert ok(HookEvent(name="PreToolUse", stage="x", tool="t")).blocked is False
    with pytest.raises(TimeoutError):
        command_handler(SLEEP, timeout=0.5)(HookEvent(name="PreToolUse", stage="x", tool="t"))


def test_function_middleware_raises_middleware_failure_when_run_is_stopped():
    middleware = HookFunctionMiddleware(HookEngine())
    context = SimpleNamespace(function=SimpleNamespace(name="read_file"), arguments={}, result=None)
    called = []

    async def call_next():
        called.append(1)

    async def attempt():
        await middleware.process(context, call_next)

    asyncio.run(attempt())  # not stopped: the tool runs
    assert called == [1]
    event = threading.Event()
    event.set()
    token = RUN_CANCEL.set(event)
    try:
        with pytest.raises(MiddlewareFailure, match="stopped by user"):
            asyncio.run(attempt())
    finally:
        RUN_CANCEL.reset(token)
    assert called == [1]


def test_cli_keyboard_interrupt_marks_run_stopped_and_exits_130(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "validate_gateway_settings", lambda: None)
    monkeypatch.setattr(
        cli, "DailyUsageLedger", lambda: SimpleNamespace(limit=1, snapshot=lambda: {"used": 0})
    )
    host = SimpleNamespace(
        view={"status": "new"}, state={"active_stage": "repo_analysis"}, events=[], saved=0
    )
    host.audit = lambda event, **details: host.events.append(event)
    host.save = lambda: setattr(host, "saved", host.saved + 1)
    seen = {}

    async def start(*args):
        seen["cancel"] = RUN_CANCEL.get()
        raise KeyboardInterrupt

    host.start = start
    closed = []

    class Runtime:
        async def close(self):
            closed.append(1)

    monkeypatch.setattr(cli, "MAFHarnessRuntime", lambda **kwargs: Runtime())
    monkeypatch.setattr(cli, "Harness", lambda *a, **k: host)
    assert cli.main(["run", "https://github.com/acme/demo", "--run-dir", str(tmp_path / "r")]) == 130
    assert host.view["status"] == "stopped" and host.events == ["run_stopped_by_user"] and host.saved == 1
    assert seen["cancel"].is_set() and closed == [1]


def test_stop_then_rerun_resumes_with_stable_executor_ids_and_plain_checkpoints(api):
    entered = threading.Event()

    class Slow(ScriptedStageRuntime):
        async def run_stage(self, **kwargs):
            entered.set()
            await asyncio.sleep(30)

    key = stage_in_flight(api, None, Slow)
    assert entered.wait(10)
    before = sorted(api.host.workflow.executors)
    api.stop_run(key)
    wait(api)
    stopped = api.read(key)
    assert stopped["status"] == "stopped"
    stage = stopped["state"]["active_stage"]
    api.runtime_factory = ScriptedStageRuntime
    api.rerun(key, {"stage": stage, "request_id": None})
    wait(api)
    assert not api.errors
    run = api.read(key)
    assert run["pending"]["stage"] == stage and run["status"] == "waiting"
    after = sorted(api.host.workflow.executors)
    assert set(after) <= set(before)  # same executor ids, so saved checkpoints keep their meaning
    # Checkpoints carry only MAF framework types and builtins: no harness class needs allowed_checkpoint_types.
    blobs = [
        base64.b64decode(item["__pickled__"])
        for file in (api.run_path(key) / "checkpoints").rglob("*.json")
        for item in _pickled(json.loads(file.read_text()))
    ]
    assert blobs and all(b"maf_test_harness" not in blob for blob in blobs)


def _pickled(value):
    if isinstance(value, dict):
        if "__pickled__" in value:
            yield value
        for item in value.values():
            yield from _pickled(item)
    elif isinstance(value, list):
        for item in value:
            yield from _pickled(item)

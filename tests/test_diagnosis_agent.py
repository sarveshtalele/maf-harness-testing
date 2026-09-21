"""The diagnosis agent: a real MAF loop with typed tools and hooks, and its place in the workflow."""

import asyncio
import json
from pathlib import Path

import pytest
from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
)
from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
from test_execution_stage import local_pytest_plan

import maf_test_harness.agent.runtime as runtime_module
import maf_test_harness.workflow as wf
from maf_test_harness.agent import diagnosis
from maf_test_harness.agent.runtime import MAFHarnessRuntime
from maf_test_harness.hooks import Decision, HookConfig, HookSpec, build_engine
from maf_test_harness.tools.registry import ToolContext
from maf_test_harness.tools.typed import default_registry
from maf_test_harness.ui.runtime_service import RuntimeService

USAGE = {"input_token_count": 10, "output_token_count": 10, "total_token_count": 20}
NOTE = {
    "summary": "The install needs a different Python.",
    "likely_cause": "requires-python is newer than the interpreter",
    "category": "environment",
    "suggested_fixes": ["approve the Python download"],
    "needs_human": True,
}


class DiagnosingClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Calls the given tool once, then answers with the diagnosis note."""

    def __init__(self, tool_name, arguments):
        super().__init__()
        self.tool_name, self.arguments = tool_name, arguments
        self.calls, self.tool_names, self.tool_results, self.instructions = 0, [], [], ""

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.calls += 1
        self.tool_names = [getattr(t, "name", str(t)) for t in (options.get("tools") or [])]
        self.instructions = str(options.get("instructions") or "")
        results = [c for m in messages for c in m.contents if c.type == "function_result"]
        self.tool_results = [str(c.result) for c in results]
        if not results:
            call = Content.from_function_call(
                call_id="call-1", name=self.tool_name, arguments=json.dumps(self.arguments)
            )
            return ChatResponse(messages=[Message(role="assistant", contents=[call])], usage_details=USAGE)
        return ChatResponse(
            messages=[Message(role="assistant", contents=[Content.from_text(json.dumps(NOTE))])],
            usage_details=USAGE,
        )


def fresh_state():
    return {"budget": {"charged": 0, "calls": 0, "reported_tokens": 0}, "sessions": {}, "model_requests": {}}


def run_agent(monkeypatch, client, tools, engine, prompt="diagnose"):
    monkeypatch.setattr(runtime_module, "OpenAIChatCompletionClient", lambda **kwargs: client)
    runtime = MAFHarnessRuntime(
        api_key="k",
        model="m",
        skills_root=Path("skills"),
        base_url="http://t",
        skill_mode="preloaded",
        hooks=engine,
    )
    state = fresh_state()
    service = RuntimeService()

    async def go():
        return await runtime.run_tool_agent(
            stage="diagnosis_setup",
            persona=diagnosis.PERSONA,
            prompt=prompt,
            tools=tools,
            state=state,
            persist=lambda: None,
            response_format=diagnosis.DiagnosisNote,
        )

    try:
        return service.submit(go()).result(30), state
    finally:
        service.close()


@pytest.fixture
def tools_and_engine(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".env").write_text("SECRET=1")
    engine = build_engine(HookConfig(builtins={"audit": False}), context={"writable_roots": [str(ws)]})
    context = ToolContext(repo_root=ws, workspace=ws, writable_roots=[ws], ecosystem="python")
    return default_registry(context, engine).as_function_tools(diagnosis.AGENT_NAME), engine


def test_the_agent_uses_a_typed_tool_and_returns_a_validated_note(monkeypatch, tools_and_engine):
    tools, engine = tools_and_engine
    log = "ERROR: Package requires a different Python: 3.9 not in >=3.12"
    client = DiagnosingClient("diagnose_env_error", {"log": log, "ecosystem": "python"})
    note, state = run_agent(monkeypatch, client, tools, engine)
    assert note == NOTE
    envelope = json.loads(client.tool_results[0])
    assert envelope["code"] == "ENV-PY-003" and envelope["category"] == "environment"
    run = state["tool_agent_runs"][0]
    assert run["status"] == "returned" and run["tool_calls"] == ["diagnose_env_error"]
    assert state["budget"]["calls"] == 2 and state["budget"]["reported_tokens"] == 40


def test_only_read_only_tools_are_offered_and_the_todo_tool_is_present(monkeypatch, tools_and_engine):
    tools, engine = tools_and_engine
    client = DiagnosingClient("list_dir", {})
    run_agent(monkeypatch, client, tools, engine)
    offered = set(client.tool_names)
    assert {"diagnose_env_error", "diagnose_exec_error", "plan_env_fix", "read_file", "list_dir"} <= offered
    assert not offered & {"provision_toolchain", "write_test_file", "delete_file"}
    assert any("todo" in name.lower() for name in offered), offered
    assert "never change the machine" in client.instructions


def test_the_tools_refuse_secret_files_for_the_agent_too(monkeypatch, tools_and_engine):
    tools, engine = tools_and_engine
    client = DiagnosingClient("read_file", {"path": ".env"})
    run_agent(monkeypatch, client, tools, engine)
    assert json.loads(client.tool_results[0])["code"] == "FILE-SENSITIVE"


def test_a_hook_deny_reaches_the_agent_as_a_tool_error(monkeypatch, tools_and_engine):
    tools, _ = tools_and_engine
    strict = build_engine(HookConfig(builtins={"audit": False}), context={})
    strict.hooks.append(
        HookSpec("PreToolUse", lambda e: Decision("deny", "no reading today"), "user:no-reads", "^read_file$")
    )
    client = DiagnosingClient("read_file", {"path": "x"})
    run_agent(monkeypatch, client, tools, strict)
    assert client.tool_results[0].startswith("Error: blocked by hook (user:no-reads)")


def test_the_prompt_is_stable_first_and_carries_the_evidence():
    prompt = diagnosis.diagnosis_prompt("setup", {"name": "pip"}, "boom", [{"code": "ENV-PY-003"}])
    parsed = json.loads(prompt)
    assert list(parsed)[:2] == ["assignment", "output_contract"] and parsed["log_tail"] == "boom"
    assert parsed["output_contract"]["properties"]["category"]["enum"]


def test_log_tail_reads_the_end_of_the_files(tmp_path):
    (tmp_path / "a.log").write_text("x" * 10_000 + "DECISIVE")
    tail = diagnosis.log_tail([str(tmp_path / "a.log"), str(tmp_path / "missing.log")], 100)
    assert tail.endswith("DECISIVE") and len(tail) <= 100
    assert diagnosis.log_tail([]) == ""


def test_diagnose_never_raises_and_reports_the_failure():
    class Broken:
        async def run_tool_agent(self, **kwargs):
            raise RuntimeError("gateway down")

    note = asyncio.run(
        diagnosis.diagnose(
            Broken(), [], kind="setup", failing={}, log="", deterministic=[], state={}, persist=lambda: None
        )
    )
    assert note["source"] == "agent" and "gateway down" in note["error"]


# --- in the workflow --------------------------------------------------------------------------------------


@pytest.fixture
def drive(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "stdout.log").write_text("collecting...\nfatal error: Python.h: No such file or directory\n")
    (log_dir / "stderr.log").write_text("error: command 'gcc' failed\n")

    def failing_setup(steps, workspace, out_dir, **kwargs):
        out_dir.mkdir(parents=True)
        report = {
            "status": "setup_failed",
            "error": "Step 'pip-install' failed: failed",
            "warnings": [],
            "steps": [
                {
                    "name": "pip-install",
                    "argv": ["pip", "install", "chromadb"],
                    "status": "failed",
                    "exit_code": 1,
                    "stdout": str(log_dir / "stdout.log"),
                    "diagnoses": [{"code": "ENV-PY-001", "category": "environment"}],
                }
            ],
        }
        (out_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return report

    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    monkeypatch.setattr(wf, "build_plan", local_pytest_plan)
    monkeypatch.setattr(wf, "build_environment_plan", local_env_plan)
    monkeypatch.setattr(wf, "execute_setup", failing_setup)
    service = RuntimeService()

    def make(runtime):
        host = wf.Harness(
            tmp_path / "run", skills_root=Path("skills"), runtime=runtime, cache_dir=tmp_path / "cache"
        )
        service.submit(host.start("https://github.com/acme/demo")).result(15)

        def answer(**response):
            return service.submit(host.respond(host.view["pending"]["id"], response)).result(120)

        for _ in range(4):  # clone, repo, requirements, environment install
            answer(decision="approve")
        return host

    yield make
    service.close()


def test_a_failed_install_gets_an_agent_note_at_the_review_gate_when_enabled(drive, monkeypatch):
    monkeypatch.setattr(wf, "DIAGNOSIS_AGENT", True)
    runtime = ScriptedStageRuntime()
    host = drive(runtime)
    artifact = host.view["pending"]["details"]["artifact"]
    assert artifact["status"] == "setup_failed"
    note = artifact["report"]["agent_diagnosis"]
    assert note["category"] == "environment" and note["source"] == "agent" and note["needs_human"] is True
    call = runtime.tool_agent_calls[0]
    assert call["stage"] == "diagnosis_setup"
    assert "provision_toolchain" not in call["tools"] and "diagnose_env_error" in call["tools"]
    prompt = json.loads(call["prompt"])
    assert "Python.h" in prompt["log_tail"] and "gcc" in prompt["log_tail"]
    assert prompt["failing_step"]["name"] == "pip-install"
    audit = [json.loads(line)["event"] for line in (host.run_dir / "audit.jsonl").read_text().splitlines()]
    assert "agent_diagnosis_started" in audit and "agent_diagnosis_completed" in audit


def test_the_agent_is_off_by_default(drive):
    runtime = ScriptedStageRuntime()
    host = drive(runtime)
    assert "agent_diagnosis" not in host.view["pending"]["details"]["artifact"]["report"]
    assert runtime.tool_agent_calls == []


def test_a_failing_agent_never_blocks_the_gate(drive, monkeypatch):
    monkeypatch.setattr(wf, "DIAGNOSIS_AGENT", True)
    runtime = ScriptedStageRuntime()
    runtime.tool_agent_note = RuntimeError("model gateway unreachable")
    host = drive(runtime)
    report = host.view["pending"]["details"]["artifact"]["report"]
    assert host.view["pending"]["kind"] == "review"
    assert "unreachable" in report["agent_diagnosis"]["error"]

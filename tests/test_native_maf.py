"""Real MAF harness/SkillsProvider execution against a scripted chat transport.

Only the network boundary (``_inner_get_response``) is replaced. The harness agent, SkillsProvider,
function-invocation loop, middleware, session and budget accounting are the actual MAF code paths.
"""

import asyncio
import hashlib
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

import maf_test_harness.maf_runtime as runtime_module
from maf_test_harness.maf_runtime import AgentOutputError, MAFHarnessRuntime
from maf_test_harness.runtime_service import RuntimeService

SKILL = "test-design-generator"


class ScriptedClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Turn 1 loads the named skills (one load_skill call each), an optional script call follows; then JSON."""

    def __init__(self, load=None, extra_tool=None, script=None):
        super().__init__()
        self.load = [load] if isinstance(load, str) else list(load or [])
        self.extra_tool, self.script = extra_tool, script
        self.calls = 0
        self.instructions = []
        self.tool_names = []
        self.loops = []
        self.script_results = []

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.calls += 1
        self.instructions.append(str(options.get("instructions") or ""))
        self.tool_names.append([getattr(t, "name", str(t)) for t in (options.get("tools") or [])])
        self.loops.append(asyncio.get_running_loop())
        results = [c for m in messages for c in m.contents if c.type == "function_result"]
        self.script_results = [str(c.result) for c in results if c.call_id == "call-script"]
        usage = {"input_token_count": 10, "output_token_count": 10, "total_token_count": 20}

        def respond(*calls):
            return ChatResponse(
                messages=[Message(role="assistant", contents=list(calls))], usage_details=usage
            )

        if self.load and not results:
            return respond(
                *(
                    Content.from_function_call(
                        call_id=f"call-{i}", name="load_skill", arguments=json.dumps({"skill_name": n})
                    )
                    for i, n in enumerate(self.load)
                )
            )
        if self.script and len(results) == len(self.load):
            return respond(
                Content.from_function_call(
                    call_id="call-script", name="run_skill_script", arguments=json.dumps(self.script)
                )
            )
        if self.extra_tool and not results:
            return respond(Content.from_function_call(call_id="call-x", name=self.extra_tool, arguments="{}"))
        return ChatResponse(
            messages=[Message(role="assistant", contents=[Content.from_text('{"hello":"world"}')])],
            usage_details=usage,
        )


def fresh_state():
    return {
        "budget": {"limit": 2_000_000, "charged": 0, "calls": 0, "reported_tokens": 0},
        "sessions": {},
        "model_requests": {},
    }


def run_stage(monkeypatch, client, mode, companions=()):
    monkeypatch.setattr(runtime_module, "OpenAIChatCompletionClient", lambda **kwargs: client)
    runtime = MAFHarnessRuntime(
        api_key="test", model="test", skills_root=Path("skills"), base_url="http://test", skill_mode=mode
    )
    state = fresh_state()
    service = RuntimeService()

    async def go():
        return await runtime.run_stage(
            stage="test_design",
            skill_name=SKILL,
            persona="PERSONA: test",
            prompt="Return JSON.",
            state=state,
            persist=lambda: None,
            companions=companions,
        )

    try:
        return service.submit(go()).result(20), state
    finally:
        service.close()


def test_progressive_mode_loads_skill_through_maf_skills_provider(monkeypatch):
    client = ScriptedClient(load=SKILL)
    result, state = run_stage(monkeypatch, client, "progressive")
    assert result == {"status": "completed", "artifact": {"hello": "world"}}
    # The provider advertises only the assigned skill and exposes MAF's skill tools.
    assert "load_skill" in client.tool_names[0]
    # MAF 1.18 registers run_skill_script unconditionally (docs say only when scripts exist). It needs
    # approval, is never auto-approved here, and script discovery is disabled: see the rejection test below.
    assert set(client.tool_names[0]) == {"load_skill", "read_skill_resource", "run_skill_script"}
    assert SKILL in client.instructions[0]
    for other in ("junit-generator", "playwright-test-generator", "test-planning"):
        assert other not in client.instructions[0]
    trace = state["skill_tool_trace"]["test_design"]
    assert trace["skill_loaded_via_maf"] is True
    assert [c["name"] for c in trace["calls"]] == ["load_skill"] and trace["calls"][0]["ok"] is True
    prov = state["skill_provenance"]["test_design"]
    assert prov["uses_maf_skills_provider"] is True and prov["mode"] == "maf_skills_provider"
    expected = hashlib.sha256(Path(f"skills/{SKILL}/SKILL.md").read_bytes()).hexdigest()
    assert prov["sha256"] == expected and len(prov["package_sha256"]) == 64
    assert state["runtime_provenance"]["factory"] == "agent_framework.create_harness_agent"
    assert client.calls == 3 and state["budget"]["calls"] == 3  # load turn + READY turn + answer turn
    assert len({id(loop) for loop in client.loops}) == 1  # every provider call ran on the one runtime loop


def test_progressive_mode_rejects_output_when_skill_was_never_loaded(monkeypatch):
    client = ScriptedClient(load=None)
    with pytest.raises(AgentOutputError, match="did not load the assigned Agent Skill"):
        run_stage(monkeypatch, client, "progressive")


def test_progressive_mode_rejects_loading_a_different_skill(monkeypatch):
    client = ScriptedClient(load="junit-generator")
    with pytest.raises(AgentOutputError, match="did not load the assigned Agent Skill"):
        run_stage(monkeypatch, client, "progressive")


def test_hallucinated_tools_are_recorded_but_cannot_satisfy_skill_check(monkeypatch):
    client = ScriptedClient(extra_tool="read_repo")
    with pytest.raises(AgentOutputError):
        run_stage(monkeypatch, client, "progressive")


def test_script_execution_request_is_rejected_by_the_host(monkeypatch):
    client = ScriptedClient(extra_tool="run_skill_script")
    with pytest.raises(AgentOutputError, match="needs approval"):
        run_stage(monkeypatch, client, "progressive")


def test_preloaded_mode_is_labelled_as_not_using_maf_skills_provider(monkeypatch):
    client = ScriptedClient()
    result, state = run_stage(monkeypatch, client, "preloaded")
    assert result["status"] == "completed"
    assert "# Test Design Generator" in client.instructions[0]  # skill text is in system instructions
    assert client.tool_names[0] == []
    prov = state["skill_provenance"]["test_design"]
    assert prov["uses_maf_skills_provider"] is False and prov["mode"] == "host_preloaded"
    assert state["budget"]["calls"] == 1 and state["budget"]["reported_tokens"] == 20
    assert json.dumps(state["skill_tool_trace"])


def test_invalid_skill_mode_is_rejected():
    with pytest.raises(ValueError, match="skill_mode"):
        MAFHarnessRuntime(api_key="k", model="m", skills_root=Path("skills"), skill_mode="magic")


def test_skill_is_loaded_once_per_stage_and_reused_across_calls(monkeypatch):
    client = ScriptedClient(load=SKILL)
    monkeypatch.setattr(runtime_module, "OpenAIChatCompletionClient", lambda **kwargs: client)
    runtime = MAFHarnessRuntime(
        api_key="test", model="test", skills_root=Path("skills"), base_url="http://test"
    )
    state, service = fresh_state(), RuntimeService()

    async def two_calls():
        for _ in range(2):
            await runtime.run_stage(
                stage="test_design",
                skill_name=SKILL,
                persona="P",
                prompt="Return JSON.",
                state=state,
                persist=lambda: None,
            )

    try:
        service.submit(two_calls()).result(20)
    finally:
        service.close()
    assert client.calls == 4  # load + READY once, then one answer turn per call
    assert [c["name"] for c in state["skill_tool_trace"]["test_design"]["calls"]] == ["load_skill"]


def test_companion_skills_are_advertised_loaded_and_recorded(monkeypatch):
    client = ScriptedClient(load=[SKILL, "test-data-generator"])
    result, state = run_stage(monkeypatch, client, "progressive", companions=("test-data-generator",))
    assert result["status"] == "completed"
    assert "test-data-generator" in client.instructions[0] and "PRIMARY skill" in client.instructions[0]
    assert "junit-generator" not in client.instructions[0]  # only the selected skills are advertised
    trace = state["skill_tool_trace"]["test_design"]
    assert trace["skill_loaded_via_maf"] is True and set(trace["loaded_skills"]) == {
        SKILL,
        "test-data-generator",
    }
    prov = state["skill_provenance"]["test_design"]
    assert [c["name"] for c in prov["companions"]] == ["test-data-generator"] and len(
        prov["companions"][0]["package_sha256"]
    ) == 64


def test_output_is_rejected_when_a_companion_skill_was_not_loaded(monkeypatch):
    client = ScriptedClient(load=SKILL)  # primary loaded, companion ignored
    with pytest.raises(AgentOutputError, match="'test-data-generator'"):
        run_stage(monkeypatch, client, "progressive", companions=("test-data-generator",))


def test_reviewed_skill_script_runs_through_maf_run_skill_script_without_approval(monkeypatch):
    call = {
        "skill_name": "test-data-generator",
        "script_name": "scripts/deterministic_helpers.py",
        "args": ["--seed", "7", "--count", "2"],
    }
    client = ScriptedClient(load=[SKILL, "test-data-generator"], script=call)
    result, state = run_stage(monkeypatch, client, "progressive", companions=("test-data-generator",))
    assert result["status"] == "completed"
    payload = json.loads(client.script_results[0])  # the real script ran and produced deterministic JSON
    assert payload["seed"] == 7 and len(payload["id_ids"]) == 2
    run = state["skill_script_runs"][0]
    assert (run["skill"], run["script"], run["exit_code"]) == (
        "test-data-generator",
        "scripts/deterministic_helpers.py",
        0,
    )
    assert (
        len(run["sha256"]) == 64
        and "run_skill_script" in state["skill_tool_trace"]["test_design"]["calls"][-1]["name"]
    )


def test_scripts_that_are_not_model_tier_are_rejected_by_the_host(monkeypatch):
    call = {
        "skill_name": "traceability-generator",
        "script_name": "scripts/validate_traceability.py",
        "args": ["x.json"],
    }
    client = ScriptedClient(load=[SKILL, "traceability-generator"], script=call)
    with pytest.raises(AgentOutputError, match="needs approval|reviewed skill tools"):
        run_stage(monkeypatch, client, "progressive", companions=("traceability-generator",))


def test_script_runner_refuses_modified_scripts_unsafe_args_and_unlisted_scripts(tmp_path):
    import shutil

    from maf_test_harness.skill_scripts import HarnessScriptRunner, ScriptRefused

    root = tmp_path / "skills"
    shutil.copytree("skills", root)
    runner = HarnessScriptRunner(root, tiers=("model",))
    ok = runner.run(
        "test-data-generator", "scripts/deterministic_helpers.py", ["--seed", "1", "--count", "1"]
    )
    assert ok["exit_code"] == 0 and json.loads(ok["stdout"])["seed"] == 1
    for bad in (["../../etc/passwd"], ["/etc/passwd"], ["~/x"], ["a" * 400]):
        with pytest.raises(ScriptRefused, match="Argument not allowed"):
            runner.run("test-data-generator", "scripts/deterministic_helpers.py", bad)
    with pytest.raises(ScriptRefused, match="not on the reviewed allow-list"):
        runner.run("traceability-generator", "scripts/validate_traceability.py", [])  # host tier only
    with pytest.raises(ScriptRefused, match="not on the reviewed allow-list"):
        runner.run(
            "coverage-analyzer", "scripts/run-coverage.js", []
        )  # reviewed and deliberately not enabled
    (root / "test-data-generator" / "scripts" / "deterministic_helpers.py").write_text("print('evil')\n")
    with pytest.raises(ScriptRefused, match="changed since it was reviewed"):
        runner.run("test-data-generator", "scripts/deterministic_helpers.py", [])

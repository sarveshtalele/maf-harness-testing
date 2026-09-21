"""The AGENT-HOOKS-0.1 backend (MAF create_agent_hooks_middleware + agent-hooks-sdk), same rules as the in-house one.

Skipped when the optional SDK (``pip install maf-test-harness[hooks]``) is not installed.
"""

import json
import sys
from pathlib import Path

import pytest
from test_hooks_maf import (  # noqa: F401
    FAKE_KEY,
    ToolCallingClient,
    engine_for,
    leak,
    registry_for,
    workspace,
)

import maf_test_harness.agent.runtime as runtime_module
from maf_test_harness.agent.runtime import MAFHarnessRuntime
from maf_test_harness.hooks import HookConfig, build_engine
from maf_test_harness.hooks.agent_hooks_bridge import BACKENDS, EngineInterceptor, hook_bundle, sdk_available
from maf_test_harness.ui.runtime_service import RuntimeService

agent_hooks = pytest.importorskip("agent_hooks")


def run_agent(monkeypatch, client, tools, engine):
    monkeypatch.setattr(runtime_module, "OpenAIChatCompletionClient", lambda **kwargs: client)
    runtime = MAFHarnessRuntime(
        api_key="k",
        model="m",
        skills_root=Path("skills"),
        base_url="http://t",
        skill_mode="preloaded",
        tool_provider=lambda stage: tools,
        hooks=engine,
        hooks_backend="sdk",
    )
    state = {"budget": {"charged": 0, "calls": 0, "reported_tokens": 0}, "sessions": {}, "model_requests": {}}
    service = RuntimeService()

    async def go():
        return await runtime.run_stage(
            stage="test_design",
            skill_name="test-design-generator",
            persona="PERSONA: test",
            prompt="Return JSON.",
            state=state,
            persist=lambda: None,
        )

    try:
        return service.submit(go()).result(30)
    finally:
        service.close()


def test_backends_and_availability():
    assert BACKENDS == ("inhouse", "sdk") and sdk_available()


def test_an_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="hooks_backend"):
        MAFHarnessRuntime(api_key="k", model="m", skills_root=Path("skills"), hooks_backend="quantum")


def test_a_denied_tool_call_is_blocked_and_the_model_sees_an_error(monkeypatch, workspace):  # noqa: F811
    engine = engine_for(workspace)
    client = ToolCallingClient("write_test_file", {"path": "../evil.py", "content": "x"})
    registry = registry_for(workspace, engine)
    result = run_agent(monkeypatch, client, registry.as_function_tools("test_design"), engine)
    assert result["status"] == "completed"
    assert client.tool_results and "path" in client.tool_results[0].lower()
    assert not (workspace.parent / "evil.py").exists()


def test_an_allowed_call_runs(monkeypatch, workspace):  # noqa: F811
    engine = engine_for(workspace)
    client = ToolCallingClient(
        "write_test_file", {"path": "tests/test_a.py", "content": "def test_a(): pass\n"}
    )
    registry = registry_for(workspace, engine)
    run_agent(monkeypatch, client, registry.as_function_tools("test_design"), engine)
    assert (workspace / "tests" / "test_a.py").exists()
    assert json.loads(client.tool_results[0])["ok"] is True


def test_argument_transforms_are_written_back(monkeypatch, workspace):  # noqa: F811
    engine = engine_for(workspace)
    client = ToolCallingClient("write_test_file", {"path": "a.py", "content": f"KEY = '{FAKE_KEY}'\n"})
    registry = registry_for(workspace, engine)
    run_agent(monkeypatch, client, registry.as_function_tools("test_design"), engine)
    written = (workspace / "a.py").read_text()
    assert "sk-abcdef" not in written and "[REDACTED]" in written


def test_result_transforms_reach_the_model(monkeypatch, workspace):  # noqa: F811
    engine = engine_for(workspace)
    client = ToolCallingClient("leak", {})
    run_agent(monkeypatch, client, [leak], engine)
    seen = client.tool_results[0]
    assert "abcdefgh12345678" not in seen and "[REDACTED]" in seen


def test_a_model_call_deny_aborts_the_run(monkeypatch, workspace):  # noqa: F811
    engine = engine_for(workspace, stage_budget={"per_stage_tokens": 15})
    client = ToolCallingClient("leak", {})
    with pytest.raises(Exception, match="hook|budget|blocked|cap") as info:
        run_agent(monkeypatch, client, [leak], engine)
    assert client.calls == 1, info.value


def test_evaluate_only_records_but_does_not_block(monkeypatch, workspace):  # noqa: F811
    engine = build_engine(
        HookConfig(builtins={"secret_redaction": False}),
        context={"writable_roots": [str(workspace)]},
        setting="evaluate_only",
    )
    client = ToolCallingClient("write_test_file", {"path": "../evil.py", "content": "x"})
    registry = registry_for(workspace, engine)
    run_agent(monkeypatch, client, registry.as_function_tools("test_design"), engine)
    assert (
        json.loads(client.tool_results[0])["code"] == "FILE-SCOPE"
    )  # the tool ran (and refused the path itself)


def test_the_interceptor_ignores_points_it_does_not_map():
    interceptor = EngineInterceptor(build_engine(HookConfig(), context={}))
    verdict = interceptor.intercept({"interception_point": "input", "target": {"content": "hi"}})
    assert verdict.decision.value == "allow"


def test_the_bundle_needs_the_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "agent_hooks", None)
    assert sdk_available() is False
    with pytest.raises(ModuleNotFoundError):
        hook_bundle(build_engine(HookConfig(), context={}))

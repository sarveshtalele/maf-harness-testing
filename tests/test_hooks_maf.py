"""Hooks inside a real MAF agent loop: only the network boundary is scripted (see test_native_maf.py).

The harness agent, function-invocation loop, middleware pipeline, session and budget accounting are the real MAF code;
the hook middleware is attached exactly as ``MAFHarnessRuntime._create_agent`` attaches it.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
    tool,
)

import maf_test_harness.agent.runtime as runtime_module
from maf_test_harness.agent.runtime import MAFHarnessRuntime
from maf_test_harness.hooks import Decision, HookConfig, HookEngine, HookSpec, build_engine
from maf_test_harness.hooks.maf_bridge import HookChatMiddleware, HookFunctionMiddleware, blocked_message
from maf_test_harness.tools.file_tools import FILE_TOOLS
from maf_test_harness.tools.registry import ToolContext, ToolRegistry
from maf_test_harness.ui.runtime_service import RuntimeService

SKILL = "test-design-generator"
USAGE = {"input_token_count": 10, "output_token_count": 10, "total_token_count": 20}
# credential-shaped values are assembled at run time so the repository secrets scan (CI) can stay strict
FAKE_KEY = "sk" + "-" + "abcdefghijklmnop1234"
FAKE_TOKEN = "ghp" + "_" + "abcdefghijklmnopqrstuvwx"


class ToolCallingClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Turn 1 calls one tool; turn 2 (after the result) answers with JSON."""

    def __init__(self, tool_name, arguments):
        super().__init__()
        self.tool_name, self.arguments = tool_name, arguments
        self.calls, self.tool_results = 0, []

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.calls += 1
        results = [c for m in messages for c in m.contents if c.type == "function_result"]
        self.tool_results = [str(c.result) for c in results]
        if not results:
            call = Content.from_function_call(
                call_id="call-1", name=self.tool_name, arguments=json.dumps(self.arguments)
            )
            return ChatResponse(messages=[Message(role="assistant", contents=[call])], usage_details=USAGE)
        answer = Content.from_text('{"hello":"world"}')
        return ChatResponse(messages=[Message(role="assistant", contents=[answer])], usage_details=USAGE)


@tool(name="leak", approval_mode="never_require")
def leak() -> str:
    """Return text that contains a credential."""
    return f"config loaded, token=abcdefgh12345678 and key {FAKE_KEY}"


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "ws").mkdir()
    return tmp_path / "ws"


def registry_for(workspace, engine):
    context = ToolContext(repo_root=workspace, workspace=workspace, writable_roots=[workspace])
    return ToolRegistry(
        context, engine, stage_tools={"test_design": ("write_test_file", "read_file")}
    ).register(*FILE_TOOLS)


def run_agent(monkeypatch, client, tools, engine):
    monkeypatch.setattr(runtime_module, "OpenAIChatCompletionClient", lambda **kwargs: client)
    runtime = MAFHarnessRuntime(
        api_key="test",
        model="test",
        skills_root=Path("skills"),
        base_url="http://test",
        skill_mode="preloaded",
        tool_provider=lambda stage: tools,
        hooks=engine,
    )
    state = {"budget": {"charged": 0, "calls": 0, "reported_tokens": 0}, "sessions": {}, "model_requests": {}}
    service = RuntimeService()

    async def go():
        return await runtime.run_stage(
            stage="test_design",
            skill_name=SKILL,
            persona="PERSONA: test",
            prompt="Return JSON.",
            state=state,
            persist=lambda: None,
        )

    try:
        return service.submit(go()).result(30)
    finally:
        service.close()


def engine_for(workspace, **overrides):
    return build_engine(
        HookConfig(builtins={"audit": False, **overrides}), context={"writable_roots": [str(workspace)]}
    )


def test_a_denied_tool_call_never_runs_and_the_model_is_told_why(monkeypatch, workspace):
    engine = engine_for(workspace)
    registry = registry_for(workspace, engine)
    client = ToolCallingClient("write_test_file", {"path": "../evil.py", "content": "x"})
    result = run_agent(monkeypatch, client, registry.as_function_tools("test_design"), engine)
    assert result["status"] == "completed"  # the run carried on: a denied tool is not a failed run
    assert client.tool_results and client.tool_results[0].startswith(
        "Error: blocked by hook (builtin:path_scope)"
    )
    assert not (workspace.parent / "evil.py").exists()


def test_an_allowed_tool_call_runs_through_the_hooks(monkeypatch, workspace):
    engine = engine_for(workspace)
    registry = registry_for(workspace, engine)
    client = ToolCallingClient(
        "write_test_file", {"path": "tests/test_a.py", "content": "def test_a(): pass\n"}
    )
    run_agent(monkeypatch, client, registry.as_function_tools("test_design"), engine)
    assert (workspace / "tests" / "test_a.py").read_text() == "def test_a(): pass\n"
    assert json.loads(client.tool_results[0])["ok"] is True


def test_secrets_in_tool_arguments_are_redacted_before_the_tool_runs(monkeypatch, workspace):
    engine = engine_for(workspace)
    registry = registry_for(workspace, engine)
    client = ToolCallingClient("write_test_file", {"path": "a.py", "content": f"KEY = '{FAKE_KEY}'\n"})
    run_agent(monkeypatch, client, registry.as_function_tools("test_design"), engine)
    written = (workspace / "a.py").read_text()
    assert "sk-abcdef" not in written and "[REDACTED]" in written


def test_secrets_in_a_tool_result_never_reach_the_model(monkeypatch, workspace):
    engine = engine_for(workspace)
    client = ToolCallingClient("leak", {})
    run_agent(monkeypatch, client, [leak], engine)
    seen = client.tool_results[0]
    assert "abcdefgh12345678" not in seen and "sk-abcdef" not in seen and "[REDACTED]" in seen


def test_without_hooks_the_same_tool_leaks(monkeypatch, workspace):
    """Control: the redaction above is the hooks' doing, not an artefact of the harness."""
    client = ToolCallingClient("leak", {})
    run_agent(monkeypatch, client, [leak], None)
    assert "abcdefgh12345678" in client.tool_results[0]


def test_evaluate_only_lets_the_call_through_but_records_it(monkeypatch, workspace):
    log = []
    engine = build_engine(
        HookConfig(builtins={"secret_redaction": False}),
        context={"writable_roots": [str(workspace)]},
        audit=lambda event, **fields: log.append((event, fields)),
        setting="evaluate_only",
    )
    registry = registry_for(workspace, engine)
    client = ToolCallingClient("write_test_file", {"path": "../evil.py", "content": "x"})
    run_agent(monkeypatch, client, registry.as_function_tools("test_design"), engine)
    assert (
        json.loads(client.tool_results[0])["code"] == "FILE-SCOPE"
    )  # the tool ran, and refused the path itself
    decisions = [f for event, f in log if event == "hook_decision"]
    assert decisions and decisions[0]["verdict"] == "deny" and decisions[0]["enforced"] is False


def test_a_crashing_hook_denies_the_tool_call(monkeypatch, workspace):
    def boom(event):
        raise RuntimeError("hook bug")

    engine = HookEngine([HookSpec("PreToolUse", boom, "user:boom")])
    client = ToolCallingClient("leak", {})
    run_agent(monkeypatch, client, [leak], engine)
    assert client.tool_results[0].startswith("Error: blocked by hook (user:boom)")
    assert "abcdefgh12345678" not in client.tool_results[0]


def test_model_call_hook_deny_aborts_the_run_instead_of_being_absorbed(monkeypatch, workspace):
    engine = engine_for(workspace, stage_budget={"per_stage_tokens": 15})  # the first call uses 20 tokens
    client = ToolCallingClient("leak", {})
    with pytest.raises(Exception, match="blocked by hook"):
        run_agent(monkeypatch, client, [leak], engine)
    assert client.calls == 1  # the second provider call never happened


def test_stage_name_reaches_the_hooks(monkeypatch, workspace):
    seen = []
    engine = HookEngine(
        [
            HookSpec("PreToolUse", lambda e: seen.append(("tool", e.stage)) or Decision(), "s"),
            HookSpec("PreModelCall", lambda e: seen.append(("model", e.stage)) or Decision(), "m"),
        ]
    )
    run_agent(monkeypatch, ToolCallingClient("leak", {}), [leak], engine)
    assert ("tool", "test_design") in seen and ("model", "test_design") in seen


def test_middleware_order_is_chat_hook_guard_tracer_function_hook(workspace):
    runtime = MAFHarnessRuntime(
        api_key="k",
        model="m",
        skills_root=Path("skills"),
        skill_mode="preloaded",
        hooks=engine_for(workspace),
    )
    runtime._guards["s"], runtime._tracers["s"] = "guard", "tracer"
    order = runtime._middleware("s")
    assert [type(m).__name__ if not isinstance(m, str) else m for m in order] == [
        "HookChatMiddleware",
        "guard",
        "tracer",
        "HookFunctionMiddleware",
    ]
    runtime.hooks = None
    assert runtime._middleware("s") == ["guard", "tracer"]


# --- the bridge on its own, with hand-made contexts ---------------------------------------------------------


def context(name="write_test_file", arguments=None, result=None):
    return SimpleNamespace(function=SimpleNamespace(name=name), arguments=arguments or {}, result=result)


def test_function_middleware_short_circuits_on_deny():
    engine = HookEngine([HookSpec("PreToolUse", lambda e: Decision("deny", "nope"), "d")])
    ran, ctx = [], context()

    async def call_next():
        ran.append(1)

    asyncio.run(HookFunctionMiddleware(engine).process(ctx, call_next))
    assert ran == [] and ctx.result == blocked_message("d", "nope")


def test_function_middleware_passes_transformed_arguments_and_rewrites_results():
    engine = HookEngine(
        [
            HookSpec("PreToolUse", lambda e: Decision("transform", "", {"arguments": {"path": "safe"}}), "t"),
            HookSpec("PostToolUse", lambda e: Decision("transform", "", {"result": "clean"}), "p"),
        ]
    )
    ctx = context(arguments={"path": "raw"})

    async def call_next():
        assert ctx.arguments == {"path": "safe"}
        ctx.result = "dirty"

    asyncio.run(HookFunctionMiddleware(engine).process(ctx, call_next))
    assert ctx.result == "clean"


def test_function_middleware_leaves_non_text_results_alone():
    engine = HookEngine([HookSpec("PostToolUse", lambda e: Decision("deny", "x"), "p")])
    ctx = context()

    async def call_next():
        ctx.result = [object()]

    asyncio.run(HookFunctionMiddleware(engine).process(ctx, call_next))
    assert isinstance(ctx.result, list)


def test_function_middleware_fails_closed_when_the_bridge_itself_breaks():
    class Broken(HookEngine):
        def before_tool(self, *a, **k):
            raise RuntimeError("engine bug")

    ctx, ran = context(), []

    async def call_next():
        ran.append(1)

    asyncio.run(HookFunctionMiddleware(Broken()).process(ctx, call_next))
    assert ran == [] and "engine bug" in ctx.result


def test_chat_middleware_tracks_stage_tokens_for_soft_caps():
    engine = HookEngine()
    middleware = HookChatMiddleware(engine, lambda: "test_generation")
    ctx = SimpleNamespace(messages=[1, 2], result=None)

    async def call_next():
        ctx.result = SimpleNamespace(usage_details={"total_token_count": 42})

    asyncio.run(middleware.process(ctx, call_next))
    asyncio.run(middleware.process(ctx, call_next))
    assert middleware.used_tokens == 84

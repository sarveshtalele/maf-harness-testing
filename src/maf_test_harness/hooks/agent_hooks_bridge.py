"""Run the harness hook engine as an AGENT-HOOKS-0.1 interceptor inside MAF's ``create_agent_hooks_middleware``.

MAF 1.18 ships the enforcement middleware for the AGENT-HOOKS-0.1 control contract (experimental) and needs the optional
``agent-hooks-sdk`` package (``pip install maf-test-harness[hooks]``). This module compiles the harness's own hook engine
(built-ins plus ``.harness/hooks.json``) onto it: one interceptor whose verdicts come from ``HookEngine``, so the same
rules hold whichever backend enforces them. ``HARNESS_HOOKS_BACKEND=sdk`` selects it; the default is the in-house
middleware (``maf_bridge.py``), which needs no extra package.

What the SDK adds over the in-house backend: interception of the run input and final output, post-model-call hooks, a
signed-off interception record (``record_sink``), identity of each context, streaming buffered fail-closed, and durable
history gated behind verdicts. What it does not do: the harness's ``StageGate`` / ``OnEnvError`` / ``OnExecError`` /
``Stop`` events are workflow events, not agent-loop events, and stay with the ``Harness``.

Mapping: ``pre_tool_call`` -> ``PreToolUse`` (deny, or transform of the whole argument object), ``post_tool_call`` ->
``PostToolUse`` (transform of the result text), ``pre_model_call`` -> ``PreModelCall``. Every other point is allowed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .engine import HookEngine
from .events import PRE_MODEL

BACKENDS = ("inhouse", "sdk")


def sdk_available() -> bool:
    try:
        import agent_hooks  # noqa: F401
    except ImportError:
        return False
    return True


class EngineInterceptor:
    """An ``agent_hooks.Interceptor`` whose verdicts are the hook engine's."""

    def __init__(self, engine: HookEngine, stage: Callable[[], str] = lambda: ""):
        self.engine, self.stage = engine, stage
        self.used_tokens = 0

    def intercept(self, context: dict[str, Any]) -> Any:
        from agent_hooks import Decision, Transform, Verdict

        point = context.get("interception_point")
        stage = self.stage()
        if point == "pre_tool_call":
            call = context.get("tool_call") or {}
            args = dict(call.get("args") or {})
            decision, changed = self.engine.before_tool(str(call.get("name", "")), args, stage)
            if decision.blocked or decision.would_block:
                return self._deny(decision, Verdict, Decision)
            if changed != args:
                return Verdict(
                    Decision.TRANSFORM,
                    reason=decision.source or "hook",
                    transform=Transform("$target", changed),
                )
        elif point == "post_tool_call":
            call = context.get("tool_call") or {}
            value = (context.get("tool_result") or {}).get("value")
            if isinstance(value, str):
                new = self.engine.after_tool(
                    str(call.get("name", "")), dict(call.get("args") or {}), value, stage
                )
                if new != value:
                    return Verdict(Decision.TRANSFORM, reason="hook", transform=Transform("$target", new))
        elif point == "pre_model_call":
            decision = self.engine.notify(
                PRE_MODEL, stage, stage_tokens=self.used_tokens, messages=len(context.get("messages") or [])
            )
            if decision.blocked or decision.would_block:
                return self._deny(decision, Verdict, Decision)
        elif point == "post_model_call":
            usage = context.get("usage") or {}
            self.used_tokens += int(usage.get("total_tokens") or usage.get("total_token_count") or 0)
        return Verdict(Decision.ALLOW)

    @staticmethod
    def _deny(decision: Any, verdict: Any, kind: Any) -> Any:
        return verdict(kind.DENY, reason=decision.source or "harness_hook", message=decision.reason[:400])


def hook_bundle(
    engine: HookEngine,
    stage: Callable[[], str] = lambda: "",
    record_sink: Callable[[Any], None] | None = None,
) -> Any:
    """The ``MiddlewareBundle`` to install first on an agent. Raises ``ModuleNotFoundError`` without the SDK."""
    from agent_framework import create_agent_hooks_middleware

    return create_agent_hooks_middleware(
        [EngineInterceptor(engine, stage)], mode=engine.mode, record_sink=record_sink
    )


__all__ = ["BACKENDS", "EngineInterceptor", "hook_bundle", "sdk_available"]

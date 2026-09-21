"""Run the hook engine inside MAF's agent loop as function and chat middleware.

* ``HookFunctionMiddleware``: PreToolUse / PostToolUse. A deny answers the model with an ``Error: blocked by hook``
  tool result and does not run the tool (the model can carry on); a transform rewrites the arguments before the tool
  runs, or the result before the model sees it.
* ``HookChatMiddleware``: PreModelCall. A deny aborts the run (``MiddlewareFailure``: enforcement must fail loud, not be
  turned into a tool error the model can route around).

Command hooks are blocking subprocesses, so the engine is called through ``asyncio.to_thread``. An unexpected bug in
this bridge is treated like a failing hook: deny (fail closed), never silently allow.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from agent_framework import ChatMiddleware, Content, FunctionMiddleware, MiddlewareFailure

from .engine import HookEngine
from .events import PRE_MODEL

StageGetter = Callable[[], str]


def _as_dict(arguments: Any) -> dict[str, Any]:
    if hasattr(arguments, "model_dump"):
        return arguments.model_dump()
    return dict(arguments) if isinstance(arguments, Mapping) else {}


def _rebuild(original: Any, arguments: dict[str, Any]) -> Any:
    """Same kind of container as the tool received: a validated model stays a model."""
    if hasattr(original, "model_validate"):
        return type(original).model_validate(arguments)
    return arguments


def blocked_message(source: str, reason: str) -> str:
    return f"Error: blocked by hook ({source or 'hook'}): {reason or 'denied'}"


class HookFunctionMiddleware(FunctionMiddleware):
    def __init__(self, engine: HookEngine, stage: StageGetter = lambda: ""):
        self.engine, self.stage = engine, stage

    async def process(self, context, call_next):
        name = context.function.name
        stage = self.stage()
        try:
            arguments = _as_dict(context.arguments)
            decision, changed = await asyncio.to_thread(self.engine.before_tool, name, arguments, stage)
            if decision.blocked:
                context.result = blocked_message(decision.source, decision.reason)
                return
            if changed != arguments:
                context.arguments = _rebuild(context.arguments, changed)
        except Exception as exc:  # the bridge itself failed: fail closed
            context.result = blocked_message("hook-bridge", f"{type(exc).__name__}: {str(exc)[:200]}")
            return
        await call_next()
        try:
            context.result = await self._after(name, changed, context.result, stage)
        except Exception as exc:  # cannot vouch that the output was scrubbed: do not hand it to the model
            context.result = blocked_message("hook-bridge", f"post-tool hooks failed: {type(exc).__name__}")

    async def _after(self, name: str, arguments: dict[str, Any], result: Any, stage: str) -> Any:
        """PostToolUse over the tool's text. MAF hands middleware either a string or a list of ``Content`` items;
        only text items are passed to the hooks, other content (images, files) is left as it is."""
        if isinstance(result, str):
            return await asyncio.to_thread(self.engine.after_tool, name, arguments, result, stage)
        if isinstance(result, list) and result and all(hasattr(item, "type") for item in result):
            rebuilt = []
            for item in result:
                text = getattr(item, "text", None) if item.type == "text" else None
                if isinstance(text, str):
                    new = await asyncio.to_thread(self.engine.after_tool, name, arguments, text, stage)
                    item = item if new == text else Content.from_text(new)
                rebuilt.append(item)
            return rebuilt
        return result


class HookChatMiddleware(ChatMiddleware):
    """PreModelCall for every provider request; also keeps the stage's running token total for soft-cap hooks."""

    def __init__(self, engine: HookEngine, stage: StageGetter = lambda: ""):
        self.engine, self.stage = engine, stage
        self.used_tokens = 0

    async def process(self, context, call_next):
        decision = await asyncio.to_thread(
            self.engine.notify,
            PRE_MODEL,
            self.stage(),
            stage_tokens=self.used_tokens,
            messages=len(context.messages),
        )
        if decision.blocked:
            raise MiddlewareFailure(f"Model call blocked by hook ({decision.source}): {decision.reason}")
        await call_next()
        usage = getattr(context.result, "usage_details", None) or {}
        usage = usage.to_dict() if hasattr(usage, "to_dict") else usage
        self.used_tokens += int(usage.get("total_token_count") or 0) if isinstance(usage, Mapping) else 0


def hook_middleware(engine: HookEngine, stage: StageGetter = lambda: "") -> list[Any]:
    """Middleware to add to an agent, chat hook first so a denied call never reserves budget."""
    return [HookChatMiddleware(engine, stage), HookFunctionMiddleware(engine, stage)]


__all__ = ["HookChatMiddleware", "HookFunctionMiddleware", "blocked_message", "hook_middleware"]

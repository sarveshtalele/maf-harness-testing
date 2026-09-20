"""MAF middleware used as lifecycle hooks by the reference harness."""

from __future__ import annotations

import time
from typing import Any

from agent_framework import ChatMiddleware, FunctionMiddleware


class AuditSink:
    """In-memory audit log kept deliberately small for the sample."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class ChatAuditHook(ChatMiddleware):
    """Hook around each model/chat invocation."""

    def __init__(self, sink: AuditSink) -> None:
        self.sink = sink

    async def process(self, context, call_next):
        started = time.monotonic()
        try:
            await call_next()
        except BaseException as exc:
            self.sink.record(
                {
                    "kind": "chat",
                    "status": "error",
                    "error": type(exc).__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )
            raise

        self.sink.record(
            {
                "kind": "chat",
                "status": "ok",
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
        )


class ToolAuditHook(FunctionMiddleware):
    """Hook around every MAF function call, including Agent Skill tools."""

    def __init__(self, sink: AuditSink) -> None:
        self.sink = sink
        self.calls: list[str] = []

    async def process(self, context, call_next):
        name = context.function.name
        self.calls.append(name)
        started = time.monotonic()

        try:
            await call_next()
        except BaseException as exc:
            self.sink.record(
                {
                    "kind": "tool",
                    "name": name,
                    "status": "error",
                    "error": type(exc).__name__,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )
            raise

        self.sink.record(
            {
                "kind": "tool",
                "name": name,
                "status": "ok",
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
        )

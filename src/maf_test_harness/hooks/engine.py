"""The hook engine: run every matching hook for an event and combine their verdicts, fail-closed.

Rules (see docs/HOOKS.md):

* deny beats ask beats transform beats allow; the first deny ends the event.
* transforms chain: each hook sees the arguments/result the previous one produced.
* a hook that crashes, times out or prints garbage is a **deny** on enforcing events (PreToolUse, PreModelCall,
  StageGate) and only recorded on observer events.
* ``mode="evaluate_only"`` records what would have happened and never blocks (roll a hook out safely).
* a deny on an observer event (SessionStart, PostToolUse, OnEnvError, OnExecError, Stop) is recorded, not enforced.
* ``ask`` needs a human; with no approver configured it is treated as deny, never as allow.
"""

from __future__ import annotations

import copy
import importlib
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..envs.platform_compat import run_cancellable
from .events import (
    ALLOW,
    ASK,
    DENY,
    ENFORCING,
    POST_TOOL,
    PRE_TOOL,
    TRANSFORM,
    Decision,
    HookEvent,
    strongest,
)

Handler = Callable[[HookEvent], Any]
Approver = Callable[[HookEvent, str], bool]


@dataclass
class HookSpec:
    event: str
    handler: Handler
    name: str
    matcher: str | None = None  # regular expression on the tool name (tool events) or stage (other events)

    def matches(self, event: HookEvent) -> bool:
        if not self.matcher:
            return True
        subject = event.tool or event.stage
        return bool(re.search(self.matcher, subject))


def command_handler(argv: list[str], timeout: float = 10, env: dict[str, str] | None = None) -> Handler:
    """A hook that is an external command: the event is JSON on stdin; exit 2 denies (reason on stderr); exit 0
    with a ``{"verdict": ...}`` JSON object on stdout decides; exit 0 with no output allows; anything else fails."""
    if not isinstance(argv, list | tuple) or not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError("a command hook needs an argv list of strings (no shell)")

    def run(event: HookEvent) -> Decision:
        try:
            proc = run_cancellable(
                argv, input=json.dumps(event.to_dict(), default=str), timeout=timeout, env=env
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"hook command timed out after {timeout}s") from exc
        if proc.returncode == 2:
            return Decision(DENY, (proc.stderr or proc.stdout or "denied by hook command").strip()[:400])
        if proc.returncode != 0:
            raise RuntimeError(f"hook command exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
        out = proc.stdout.strip()
        return Decision.from_any(json.loads(out)) if out else Decision()

    return run


def python_handler(target: str) -> Handler:
    """A hook that is ``package.module:function``; it receives the event as a dict."""
    module_name, _, function = target.partition(":")
    if not module_name or not function:
        raise ValueError(f"python hook must look like 'package.module:function', not {target!r}")
    function_obj = getattr(importlib.import_module(module_name), function)

    def run(event: HookEvent) -> Any:
        return function_obj(event.to_dict())

    return run


@dataclass
class HookEngine:
    hooks: list[HookSpec] = field(default_factory=list)
    mode: str = "enforce"  # enforce | evaluate_only
    audit: Callable[..., Any] | None = None
    approver: Approver | None = None
    context: dict[str, Any] = field(
        default_factory=dict
    )  # facts hooks may consult: writable_roots, allow_downloads

    def __post_init__(self) -> None:
        if self.mode not in {"enforce", "evaluate_only"}:
            raise ValueError("hook mode must be 'enforce' or 'evaluate_only'")

    def _record(self, event: HookEvent, hook: HookSpec, decision: Decision) -> None:
        if self.audit and (decision.verdict != ALLOW or decision.would_block):
            self.audit(
                "hook_decision",
                hook_event=event.name,
                tool=event.tool or None,
                stage=event.stage or None,
                hook=hook.name,
                verdict=decision.verdict,
                reason=decision.reason[:300],
                enforced=not decision.would_block and decision.verdict in {DENY, ASK},
            )

    def fire(self, event: HookEvent) -> Decision:
        """Run every hook registered for ``event``; return the combined decision. Never raises."""
        final = Decision()
        for hook in self.hooks:
            if hook.event != event.name or not hook.matches(event):
                continue
            try:
                decision = Decision.from_any(hook.handler(event), hook.name)
            except Exception as exc:  # fail closed where it matters
                decision = (
                    Decision(
                        DENY,
                        f"hook '{hook.name}' failed: {type(exc).__name__}: {str(exc)[:200]}",
                        source=hook.name,
                    )
                    if event.name in ENFORCING
                    else Decision(source=hook.name)
                )
                if self.audit:
                    self.audit(
                        "hook_error",
                        hook=hook.name,
                        hook_event=event.name,
                        error=f"{type(exc).__name__}: {exc}"[:300],
                    )
            enforcing = event.name in ENFORCING and self.mode == "enforce"
            if decision.verdict == ASK and enforcing:
                decision = self._resolve_ask(event, decision)
            if not enforcing and decision.verdict in {DENY, ASK}:
                decision.would_block = True  # evaluate_only, or an observer event: recorded, never enforced
            self._record(event, hook, decision)
            if decision.verdict == TRANSFORM and decision.payload and self.mode == "enforce":
                self._apply(event, decision.payload)
            if decision.verdict == DENY and not decision.would_block:
                return decision  # the first enforced deny ends the event
            final = strongest(final, decision)
        return final

    def _resolve_ask(self, event: HookEvent, decision: Decision) -> Decision:
        if self.approver and self.approver(event, decision.reason):
            return Decision(ALLOW, f"approved: {decision.reason}", source=decision.source)
        return Decision(DENY, f"approval required and not granted: {decision.reason}", source=decision.source)

    @staticmethod
    def _apply(event: HookEvent, payload: dict[str, Any]) -> None:
        if isinstance(payload.get("arguments"), dict):
            event.arguments = copy.deepcopy(payload["arguments"])
        if "result" in payload:
            event.result = None if payload["result"] is None else str(payload["result"])

    # convenience wrappers ------------------------------------------------------------------------------------
    def before_tool(
        self, tool: str, arguments: dict[str, Any], stage: str = ""
    ) -> tuple[Decision, dict[str, Any]]:
        event = HookEvent(PRE_TOOL, stage, tool, dict(arguments))
        decision = self.fire(event)
        return decision, event.arguments

    def after_tool(self, tool: str, arguments: dict[str, Any], result: str, stage: str = "") -> str:
        event = HookEvent(POST_TOOL, stage, tool, dict(arguments), result)
        self.fire(event)
        return event.result if event.result is not None else result

    def notify(self, name: str, stage: str = "", **data: Any) -> Decision:
        return self.fire(HookEvent(name, stage, data=data))


__all__ = ["Approver", "Handler", "HookEngine", "HookSpec", "command_handler", "python_handler"]

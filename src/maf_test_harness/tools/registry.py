"""Typed tool registry: every tool takes a validated argument object and returns the same ``ToolResult`` envelope.

An agent that runs commands needs three things a raw shell cannot give: arguments checked against a schema, a result
it can act on without parsing free text (``ok``, an error ``code``, a ``category`` that says whose fault it is, concrete
``suggested_fixes``), and one place where hooks see every call. ``ToolRegistry.call`` is that place:

    validate arguments -> PreToolUse hooks -> handler -> PostToolUse hooks -> ToolResult

``as_function_tools`` exposes the same tools to a MAF agent; the hook middleware there and the hooks called here are the
same engine, so a rule written once holds however the tool is reached.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

MAX_RESULT_CHARS = 6_000


@dataclass
class ToolResult:
    ok: bool
    code: str = "OK"  # an errors.taxonomy code, or OK / TOOL-ARGS / TOOL-ERROR / HOOK-DENIED / ...
    category: str = ""  # environment | infra | test_defect | app_defect | "" (see errors.taxonomy)
    evidence: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    suggested_fixes: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "category": self.category,
            "evidence": self.evidence,
            "data": self.data,
            "suggested_fixes": self.suggested_fixes,
            "next_actions": self.next_actions,
        }

    def to_json(self, limit: int = MAX_RESULT_CHARS) -> str:
        text = json.dumps(self.to_dict(), ensure_ascii=False, default=str)
        if len(text) <= limit:
            return text
        # keep it valid JSON: shrink the payload, never cut the envelope in the middle
        slim = {**self.to_dict(), "data": {"truncated": True}, "evidence": self.evidence[:500]}
        return json.dumps(slim, ensure_ascii=False, default=str)[:limit]

    @staticmethod
    def from_json(text: str) -> ToolResult:
        raw = json.loads(text)
        return ToolResult(
            ok=bool(raw["ok"]),
            code=str(raw.get("code", "OK")),
            category=str(raw.get("category", "")),
            evidence=str(raw.get("evidence", "")),
            data=dict(raw.get("data") or {}),
            suggested_fixes=[str(x) for x in raw.get("suggested_fixes") or []],
            next_actions=[str(x) for x in raw.get("next_actions") or []],
        )

    @staticmethod
    def failure(
        code: str, evidence: str, *, category: str = "", fixes: list[str] | None = None
    ) -> ToolResult:
        return ToolResult(False, code, category, evidence[:600], suggested_fixes=fixes or [])


@dataclass
class ToolContext:
    """What tools may touch. Everything outside these roots is unreachable, whatever arguments the model sends."""

    repo_root: Path | None = None  # the cloned repository (read-only)
    workspace: Path | None = None  # the isolated copy where dependencies are installed and tests run
    writable_roots: list[Path] = field(default_factory=list)  # where write tools may create files
    ecosystem: str = ""
    allow_downloads: bool = False  # set by the environment approval; provisioning is refused without it
    skills_root: Path | None = None  # where the vendored Agent Skills live (check_skill_output)
    allowed_skills: tuple[
        str, ...
    ] = ()  # the skills of the current stage: the only ones whose scripts may be run

    def read_roots(self) -> list[Path]:
        return [p.resolve() for p in (self.workspace, self.repo_root) if p]


Handler = Callable[[ToolContext, dict[str, Any]], ToolResult]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Handler
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    writes: bool = False  # changes files or the machine (used by mode/approval rules)
    downloads: bool = False  # fetches software: needs consent


def object_schema(required: list[str] | None = None, **properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


class ToolRegistry:
    def __init__(
        self,
        context: ToolContext,
        hooks: Any = None,  # hooks.HookEngine; typed loosely so this module does not depend on the hooks package
        audit: Callable[..., Any] | None = None,
        stage_tools: dict[str, tuple[str, ...]] | None = None,
    ):
        self.context, self.hooks, self.audit = context, hooks, audit
        self.stage_tools = stage_tools or {}
        self._tools: dict[str, ToolSpec] = {}
        self.sync_hook_context()

    def sync_hook_context(self) -> None:
        """Hooks decide from ``hooks.context`` (writable roots, download consent); the tool context is the source of
        truth. ``call`` re-syncs before every call. Behind a MAF agent the hook middleware runs before the tool does,
        so whoever changes the tool context there (the environment approval) must call this too."""
        if self.hooks is not None:
            self.hooks.context.update(
                writable_roots=[str(p) for p in self.context.writable_roots],
                allow_downloads=self.context.allow_downloads,
            )

    def register(self, *specs: ToolSpec) -> ToolRegistry:
        for spec in specs:
            if spec.name in self._tools:
                raise ValueError(f"tool {spec.name!r} is already registered")
            Draft202012Validator.check_schema(spec.parameters)
            self._tools[spec.name] = spec
        return self

    def names(self, stage: str | None = None) -> list[str]:
        if stage is None:
            return sorted(self._tools)
        return [n for n in self.stage_tools.get(stage, ()) if n in self._tools]

    def spec(self, name: str) -> ToolSpec:
        return self._tools[name]

    def call(
        self, name: str, arguments: dict[str, Any] | None = None, stage: str = "", *, run_hooks: bool = True
    ) -> ToolResult:
        """Run one tool. Never raises: every failure is a ``ToolResult`` the agent can read.

        ``run_hooks=False`` is for callers that already sit behind the hook middleware (a MAF agent).
        """
        hooks = self.hooks if run_hooks else None
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult.failure(
                "TOOL-UNKNOWN", f"no tool named {name!r}", fixes=[f"use one of: {', '.join(self.names())}"]
            )
        arguments = dict(arguments or {})
        problems = sorted(e.message for e in Draft202012Validator(spec.parameters).iter_errors(arguments))
        if problems:
            return ToolResult.failure(
                "TOOL-ARGS", "; ".join(problems)[:500], fixes=[f"see the schema of {name}"]
            )
        if hooks is not None:
            self.sync_hook_context()
            decision, arguments = hooks.before_tool(name, arguments, stage)
            if decision.blocked:
                return self._done(
                    name, ToolResult.failure("HOOK-DENIED", f"{decision.source}: {decision.reason}")
                )
            problems = sorted(e.message for e in Draft202012Validator(spec.parameters).iter_errors(arguments))
            if problems:  # a transform hook may not produce arguments the tool would not accept
                return self._done(
                    name, ToolResult.failure("TOOL-ARGS", f"after hooks: {'; '.join(problems)}"[:500])
                )
        try:
            result = spec.handler(self.context, arguments)
        except Exception as exc:  # a handler bug must not take the agent loop down
            result = ToolResult.failure("TOOL-ERROR", f"{type(exc).__name__}: {str(exc)[:400]}")
        if hooks is not None:
            text = result.to_json()
            scrubbed = hooks.after_tool(name, arguments, text, stage)
            if scrubbed != text:
                try:
                    result = ToolResult.from_json(scrubbed)
                except (ValueError, KeyError, TypeError):
                    result = ToolResult.failure(
                        "HOOK-OUTPUT", "a hook rewrote the tool output into something unreadable"
                    )
        return self._done(name, result)

    def _done(self, name: str, result: ToolResult) -> ToolResult:
        if self.audit:
            self.audit("typed_tool", tool=name, ok=result.ok, code=result.code)
        return result

    def as_function_tools(self, stage: str) -> list[Any]:
        """The stage's tools as MAF ``FunctionTool``s (each returns the envelope as JSON text).

        Hooks are not run here: the agent's ``HookFunctionMiddleware`` already wraps every tool call, and running
        them twice would double-redact and double-audit.
        """
        from agent_framework import FunctionTool

        tools = []
        for name in self.names(stage):
            spec = self._tools[name]

            def run(_name: str = name, **arguments: Any) -> str:
                return self.call(_name, arguments, stage, run_hooks=False).to_json()

            tools.append(
                FunctionTool(
                    name=name,
                    description=spec.description,
                    func=run,
                    input_model=spec.parameters,
                    approval_mode="never_require",
                )
            )
        return tools


__all__ = ["ToolContext", "ToolRegistry", "ToolResult", "ToolSpec", "object_schema"]

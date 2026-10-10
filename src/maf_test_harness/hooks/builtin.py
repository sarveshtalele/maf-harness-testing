"""Built-in hooks, on by default: secret redaction, write-scope guard, download consent, audit of gates and errors.

Each factory returns ``HookSpec`` objects bound to the engine's ``context`` (writable roots, consent flags), so the same
engine object can be reconfigured per run without rebuilding the hooks.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .. import mcp_servers, settings
from .engine import HookSpec
from .events import (
    ASK,
    DENY,
    ON_ENV_ERROR,
    ON_EXEC_ERROR,
    POST_TOOL,
    PRE_MODEL,
    PRE_TOOL,
    STAGE_GATE,
    STOP,
    Decision,
    HookEvent,
)

REDACTED = "[REDACTED]"
_KEYED = re.compile(
    r"""(?ix)((?:api[_-]?key|secret|token|password|passwd)["']?\s*[:=]\s*["']?)[^\s"',;]{8,}"""
)
_TOKENS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{16,}"),
]
_SECRET_ENV = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "AWS_SECRET_ACCESS_KEY",
)


def redact(text: str, *, keyed: bool = True) -> tuple[str, int]:
    """Replace secret-looking strings (and the values of well-known secret environment variables) in ``text``."""
    count = 0
    runtime_key = settings.current()["api_key"]  # a key entered in the UI never lives in os.environ
    if len(runtime_key) >= 8 and runtime_key in text:
        count += text.count(runtime_key)
        text = text.replace(runtime_key, REDACTED)
    for value in mcp_servers.secret_values():  # MCP server env values / bearer tokens
        if value in text:
            count += text.count(value)
            text = text.replace(value, REDACTED)
    for name in _SECRET_ENV:
        value = os.environ.get(name, "")
        if len(value) >= 8 and value in text:
            count += text.count(value)
            text = text.replace(value, REDACTED)
    for pattern in _TOKENS:
        text, n = pattern.subn(REDACTED, text)
        count += n
    if keyed:
        text, n = _KEYED.subn(
            lambda m: m.group(1) + REDACTED, text
        )  # keep the key and quotes (JSON stays valid), hide the value
        count += n
    return text, count


def _redact_value(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        total, out = 0, {}
        for key, item in value.items():
            out[key], n = _redact_value(item)
            total += n
        return out, total
    if isinstance(value, list):
        total, items = 0, []
        for item in value:
            new, n = _redact_value(item)
            items.append(new)
            total += n
        return items, total
    return value, 0


def secret_redaction() -> list[HookSpec]:
    def before(event: HookEvent) -> Decision:
        args, count = _redact_value(event.arguments)
        return (
            Decision("transform", f"redacted {count} secret(s) from tool arguments", {"arguments": args})
            if count
            else Decision()
        )

    def after(event: HookEvent) -> Decision:
        text, count = redact(event.result or "")
        return (
            Decision("transform", f"redacted {count} secret(s) from tool output", {"result": text})
            if count
            else Decision()
        )

    return [
        HookSpec(PRE_TOOL, before, "builtin:secret_redaction"),
        HookSpec(POST_TOOL, after, "builtin:secret_redaction"),
    ]


WRITE_TOOLS = r"^(write_test_file|delete_file|apply_env_fix)$"


def path_scope(context: dict[str, Any]) -> list[HookSpec]:
    """Tools that write may only touch ``context['writable_roots']`` (the generated-tests directory); a path that
    escapes through ``..`` or an absolute path elsewhere is denied. With no roots configured, writes are denied."""

    def check(event: HookEvent) -> Decision:
        roots = [Path(r).resolve() for r in context.get("writable_roots", [])]
        raw = event.arguments.get("path")
        if raw is None:
            return Decision()
        if not roots:
            return Decision(DENY, "no writable directory is configured for this run")
        for root in roots:
            target = (
                (root / str(raw)).resolve() if not Path(str(raw)).is_absolute() else Path(str(raw)).resolve()
            )
            if target.is_relative_to(root):
                return Decision()
        return Decision(DENY, f"path '{raw}' is outside the writable directories")

    return [HookSpec(PRE_TOOL, check, "builtin:path_scope", WRITE_TOOLS)]


def download_consent(context: dict[str, Any]) -> list[HookSpec]:
    """Downloading a toolchain needs consent: the environment approval sets ``allow_downloads``; otherwise ask."""

    def check(event: HookEvent) -> Decision:
        if context.get("allow_downloads"):
            return Decision()
        return Decision(
            ASK,
            f"download {event.arguments.get('kind', 'a toolchain')} {event.arguments.get('version', '')}".strip(),
        )

    return [HookSpec(PRE_TOOL, check, "builtin:download_consent", r"^provision_toolchain$")]


def stage_budget(options: dict[str, Any]) -> list[HookSpec]:
    """Optional soft cap: deny a model call once the stage has used ``per_stage_tokens`` (off unless configured)."""
    cap = int(options.get("per_stage_tokens", 0) or 0)

    def check(event: HookEvent) -> Decision:
        used = int(event.data.get("stage_tokens", 0) or 0)
        return (
            Decision(DENY, f"stage used {used:,} of its {cap:,}-token soft cap")
            if cap and used >= cap
            else Decision()
        )

    return [HookSpec(PRE_MODEL, check, "builtin:stage_budget")] if cap else []


def audit_events(audit: Any) -> list[HookSpec]:
    """Record gates and environment/execution errors in audit.jsonl (tool calls are already in trace.jsonl)."""

    def record(event: HookEvent) -> Decision:
        if audit:
            audit(
                "hook_event",
                hook_event=event.name,
                stage=event.stage or None,
                data={k: v for k, v in event.data.items() if k != "state"},
            )
        return Decision()

    return [
        HookSpec(name, record, "builtin:audit") for name in (STAGE_GATE, ON_ENV_ERROR, ON_EXEC_ERROR, STOP)
    ]


DEFAULT_BUILTINS = ("secret_redaction", "path_scope", "download_consent", "stage_budget", "audit")


def build_builtins(enabled: dict[str, Any], context: dict[str, Any], audit: Any) -> list[HookSpec]:
    """Built-ins selected by config: every name is on unless the config sets it to ``false``."""
    hooks: list[HookSpec] = []
    for name in DEFAULT_BUILTINS:
        setting = enabled.get(name, True)
        if setting is False:
            continue
        options = setting if isinstance(setting, dict) else {}
        if name == "secret_redaction":
            hooks += secret_redaction()
        elif name == "path_scope":
            hooks += path_scope(context)
        elif name == "download_consent":
            hooks += download_consent(context)
        elif name == "stage_budget":
            hooks += stage_budget(options)
        elif name == "audit":
            hooks += audit_events(audit)
    return hooks

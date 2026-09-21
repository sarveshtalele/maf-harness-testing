"""``.harness/hooks.json``: load, validate and compile user hooks (plus the built-ins) into a ``HookEngine``.

Two files are read, user first, then project: ``~/.maf-harness/hooks.json`` and ``<working directory>/.harness/hooks.json``.
The working directory is where the harness is started, never the repository under test: a cloned repository cannot
install hooks. A malformed file is an error, not a warning: silently ignoring a broken safety hook would be worse.

.. code-block:: json

    {
      "mode": "enforce",
      "builtins": {"stage_budget": {"per_stage_tokens": 2000000}, "audit": false},
      "hooks": [
        {"event": "PreToolUse", "matcher": "^write_test_file$", "command": ["python", "check_path.py"], "timeout": 5},
        {"event": "PostToolUse", "python": "my_hooks:scrub_output"}
      ]
    }
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .builtin import DEFAULT_BUILTINS, build_builtins
from .engine import Approver, HookEngine, HookSpec, command_handler, python_handler
from .events import EVENTS

MODES = ("enforce", "evaluate_only")
_ENTRY_KEYS = {"event", "matcher", "command", "python", "timeout", "name"}
_TOP_KEYS = {"mode", "builtins", "hooks"}
MAX_TIMEOUT = 300


class HookConfigError(ValueError):
    """The hooks configuration is invalid; the message names the file and the entry."""


@dataclass
class HookConfig:
    mode: str = "enforce"
    builtins: dict[str, Any] = field(default_factory=dict)
    hooks: list[dict[str, Any]] = field(default_factory=list)  # validated entries, each tagged with "_source"
    sources: list[str] = field(default_factory=list)


def config_paths(project_root: Path | None = None) -> list[Path]:
    """Config files in load order (later files win for ``mode`` and ``builtins``; ``hooks`` are concatenated)."""
    return [
        Path.home() / ".maf-harness" / "hooks.json",
        (project_root or Path.cwd()) / ".harness" / "hooks.json",
    ]


def validate_entry(entry: Any, source: str, index: int) -> dict[str, Any]:
    where = f"{source}: hooks[{index}]"
    if not isinstance(entry, dict):
        raise HookConfigError(f"{where} must be an object")
    unknown = set(entry) - _ENTRY_KEYS
    if unknown:
        raise HookConfigError(f"{where} has unknown key(s) {sorted(unknown)}; allowed: {sorted(_ENTRY_KEYS)}")
    if entry.get("event") not in EVENTS:
        raise HookConfigError(f"{where}: 'event' must be one of {', '.join(EVENTS)}")
    command, target = entry.get("command"), entry.get("python")
    if (command is None) == (target is None):
        raise HookConfigError(
            f"{where}: give exactly one of 'command' (argv list) or 'python' ('module:function')"
        )
    if command is not None and (
        not isinstance(command, list) or not command or not all(isinstance(a, str) and a for a in command)
    ):
        raise HookConfigError(
            f"{where}: 'command' must be a non-empty list of strings (an argv, never a shell line)"
        )
    if target is not None and (
        not isinstance(target, str) or target.count(":") != 1 or target.startswith(":")
    ):
        raise HookConfigError(f"{where}: 'python' must look like 'package.module:function'")
    matcher = entry.get("matcher")
    if matcher is not None:
        if not isinstance(matcher, str):
            raise HookConfigError(f"{where}: 'matcher' must be a regular expression string")
        try:
            re.compile(matcher)
        except re.error as exc:
            raise HookConfigError(f"{where}: 'matcher' is not a valid regular expression ({exc})") from exc
    timeout = entry.get("timeout", 10)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or not 0 < timeout <= MAX_TIMEOUT:
        raise HookConfigError(f"{where}: 'timeout' must be a number of seconds between 0 and {MAX_TIMEOUT}")
    return {**entry, "timeout": timeout, "_source": source}


def parse_config(data: Any, source: str = "hooks.json") -> HookConfig:
    if not isinstance(data, dict):
        raise HookConfigError(f"{source}: the top level must be an object")
    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise HookConfigError(f"{source}: unknown key(s) {sorted(unknown)}; allowed: {sorted(_TOP_KEYS)}")
    mode = data.get("mode", "enforce")
    if mode not in MODES:
        raise HookConfigError(f"{source}: 'mode' must be one of {', '.join(MODES)}")
    builtins = data.get("builtins", {})
    if not isinstance(builtins, dict) or set(builtins) - set(DEFAULT_BUILTINS):
        raise HookConfigError(
            f"{source}: 'builtins' must be an object with keys from {', '.join(DEFAULT_BUILTINS)} "
            "(each true, false or an options object)"
        )
    for name, setting in builtins.items():
        if not isinstance(setting, bool | dict):
            raise HookConfigError(f"{source}: builtins.{name} must be true, false or an options object")
    hooks = data.get("hooks", [])
    if not isinstance(hooks, list):
        raise HookConfigError(f"{source}: 'hooks' must be a list")
    return HookConfig(
        mode=mode,
        builtins=dict(builtins),
        hooks=[validate_entry(entry, source, i) for i, entry in enumerate(hooks)],
        sources=[source],
    )


def load_config(paths: list[Path] | None = None) -> HookConfig:
    """Merge every existing config file; no file at all is a valid (built-ins only) configuration."""
    merged = HookConfig()
    for path in paths if paths is not None else config_paths():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HookConfigError(f"{path}: cannot read hooks configuration ({exc})") from exc
        one = parse_config(data, str(path))
        merged.mode = one.mode if "mode" in data else merged.mode
        merged.builtins.update(one.builtins)
        merged.hooks += one.hooks
        merged.sources += one.sources
    return merged


def _resolve_argv(argv: list[str], entry: dict[str, Any]) -> list[str]:
    """Absolute program path (a relative argv[0] is resolved against the config file, never the caller's cwd, which
    Windows would otherwise use); ``python`` / ``python3`` mean the interpreter running the harness."""
    program = argv[0]
    if program in {"python", "python3"}:
        return [sys.executable, *argv[1:]]
    if os.sep in program or "/" in program:
        base = Path(entry["_source"]).parent
        path = Path(program) if Path(program).is_absolute() else (base / program)
        found = str(path.resolve()) if path.exists() else None
    else:
        found = shutil.which(program)
    if not found:
        raise HookConfigError(f"{entry['_source']}: hook command {program!r} was not found")
    return [found, *argv[1:]]


def _hook_env() -> dict[str, str]:
    from ..envs.execution import BASIC_ENV  # lazy: keeps the hooks package importable on its own

    return {key: os.environ[key] for key in BASIC_ENV if key in os.environ}


def compile_entry(entry: dict[str, Any]) -> HookSpec:
    if "command" in entry:
        argv = _resolve_argv(entry["command"], entry)
        handler = command_handler(argv, timeout=entry["timeout"], env=_hook_env())
        label = (
            Path(argv[1]).name
            if len(argv) > 1 and Path(argv[0]).stem.startswith("python")
            else Path(argv[0]).name
        )
    else:
        try:
            handler = python_handler(entry["python"])
        except (ImportError, AttributeError, ValueError) as exc:
            raise HookConfigError(f"{entry['_source']}: cannot load {entry['python']!r} ({exc})") from exc
        label = entry["python"]
    return HookSpec(entry["event"], handler, entry.get("name") or f"user:{label}", entry.get("matcher"))


def build_engine(
    config: HookConfig | None = None,
    *,
    context: dict[str, Any] | None = None,
    audit: Any = None,
    approver: Approver | None = None,
    setting: str = "enforce",
) -> HookEngine:
    """The engine for a run: built-ins first, then the user's hooks in file order.

    ``setting`` is ``HARNESS_HOOKS``: ``enforce`` (default), ``evaluate_only`` (record, never block) or ``off``.
    """
    setting = (setting or "enforce").strip().lower()
    if setting not in {"enforce", "evaluate_only", "off"}:
        raise HookConfigError("HARNESS_HOOKS must be 'enforce', 'evaluate_only' or 'off'")
    context = context if context is not None else {}
    if setting == "off":
        if audit:
            audit("hooks_disabled", reason="HARNESS_HOOKS=off")
        return HookEngine([], "enforce", audit, approver, context)
    config = config if config is not None else load_config()
    mode = "evaluate_only" if setting == "evaluate_only" else config.mode
    hooks = build_builtins(config.builtins, context, audit) + [compile_entry(e) for e in config.hooks]
    return HookEngine(hooks, mode, audit, approver, context)


__all__ = [
    "HookConfig",
    "HookConfigError",
    "build_engine",
    "compile_entry",
    "config_paths",
    "load_config",
    "parse_config",
    "validate_entry",
]

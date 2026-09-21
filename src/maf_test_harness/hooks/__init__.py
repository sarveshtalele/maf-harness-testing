"""Hooks: user- and harness-defined checks that run before/after tool calls and model calls and at workflow events.

``events``     the vocabulary: event names, verdicts, ``HookEvent``, ``Decision``
``engine``     ``HookEngine`` combines verdicts (deny > ask > transform > allow), fail-closed
``builtin``    on by default: secret redaction, write scope, download consent, stage budget, audit
``config``     ``.harness/hooks.json`` loader and ``build_engine``
``maf_bridge`` MAF function/chat middleware that runs the engine inside an agent loop
"""

from .config import HookConfig, HookConfigError, build_engine, load_config
from .engine import HookEngine, HookSpec, command_handler, python_handler
from .events import (
    EVENTS,
    ON_ENV_ERROR,
    ON_EXEC_ERROR,
    POST_TOOL,
    PRE_MODEL,
    PRE_TOOL,
    SESSION_START,
    STAGE_GATE,
    STOP,
    Decision,
    HookEvent,
)

__all__ = [
    "EVENTS",
    "ON_ENV_ERROR",
    "ON_EXEC_ERROR",
    "POST_TOOL",
    "PRE_MODEL",
    "PRE_TOOL",
    "SESSION_START",
    "STAGE_GATE",
    "STOP",
    "Decision",
    "HookConfig",
    "HookConfigError",
    "HookEngine",
    "HookEvent",
    "HookSpec",
    "build_engine",
    "command_handler",
    "load_config",
    "python_handler",
]

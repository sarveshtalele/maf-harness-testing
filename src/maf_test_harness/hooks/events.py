"""Hook events and verdicts: the vocabulary shared by every hook handler.

Modelled on the AGENT-HOOKS-0.1 idea (every interceptor returns a verdict; the framework acts on it), with the
event names users know from Claude Code style hooks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SESSION_START, PRE_TOOL, POST_TOOL, PRE_MODEL = "SessionStart", "PreToolUse", "PostToolUse", "PreModelCall"
ON_ENV_ERROR, ON_EXEC_ERROR, STAGE_GATE, STOP = "OnEnvError", "OnExecError", "StageGate", "Stop"

EVENTS = (SESSION_START, PRE_TOOL, POST_TOOL, PRE_MODEL, ON_ENV_ERROR, ON_EXEC_ERROR, STAGE_GATE, STOP)
# A verdict of deny/ask stops the action only for these; the others are observers (a deny is recorded, not enforced).
ENFORCING = frozenset({PRE_TOOL, PRE_MODEL, STAGE_GATE})

ALLOW, DENY, TRANSFORM, ASK = "allow", "deny", "transform", "ask"
VERDICTS = (ALLOW, DENY, TRANSFORM, ASK)
_STRENGTH = {ALLOW: 0, TRANSFORM: 1, ASK: 2, DENY: 3}


@dataclass
class HookEvent:
    name: str
    stage: str = ""
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.name,
            "stage": self.stage,
            "tool": self.tool,
            "arguments": self.arguments,
            "result": self.result,
            "data": self.data,
        }


@dataclass
class Decision:
    verdict: str = ALLOW
    reason: str = ""
    payload: dict[str, Any] | None = (
        None  # transform: {"arguments": {...}} before a tool, {"result": "..."} after
    )
    source: str = ""  # which hook decided
    would_block: bool = False  # evaluate_only mode: the verdict that would have been enforced

    @property
    def blocked(self) -> bool:
        return self.verdict in {DENY, ASK} and not self.would_block

    @staticmethod
    def from_any(value: Any, source: str = "") -> Decision:
        """Accept a Decision, a ``{"verdict": ...}`` dict (what command hooks print) or None (= allow)."""
        if value is None:
            return Decision(source=source)
        if isinstance(value, Decision):
            value.source = value.source or source
            return value
        if isinstance(value, dict):
            verdict = str(value.get("verdict", ALLOW)).lower()
            if verdict not in VERDICTS:
                raise ValueError(f"unknown verdict {verdict!r}")
            payload = value.get("payload")
            return Decision(
                verdict, str(value.get("reason", "")), payload if isinstance(payload, dict) else None, source
            )
        raise TypeError(f"a hook must return a Decision, a dict or None, not {type(value).__name__}")


def strongest(current: Decision, new: Decision) -> Decision:
    return new if _STRENGTH[new.verdict] > _STRENGTH[current.verdict] else current

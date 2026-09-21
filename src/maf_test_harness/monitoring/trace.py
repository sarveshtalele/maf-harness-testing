"""Append-only run trace: which model runs, tool calls and skill loads happened, in order, with timing and tokens.

MAF already emits OpenTelemetry spans (see ``telemetry.py``); this file is the harness's own, dependency-free record that the
UI and ``maf-harness trace`` read back from disk for any run. It is written from MAF middleware, so it records what the
framework actually executed, not what the model claimed. Arguments are summarised and secret-shaped strings are redacted:
the trace never contains prompts, model responses or complete generated files.

Event kinds (``kind``):

``model_run``  one ``agent.run`` (phase ``prime`` loads skills, phase ``structured`` returns the artifact)
``chat_call``  one provider request inside a run: duration, tokens (reported or estimated), status
``tool_call``  one MAF tool invocation: name, category, summarised arguments, outcome, duration
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SKILL_TOOLS = {
    "load_skill": "skill_load",
    "read_skill_resource": "skill_resource",
    "run_skill_script": "skill_script",
}
_SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]{8,}|gh[pousr]_[A-Za-z0-9]{16,})")
ARGUMENT_LIMIT = 120


def tool_category(name: str, harness_tools: tuple[str, ...] | list[str] = ()) -> str:
    """``skill_load`` / ``skill_resource`` / ``skill_script`` for the MAF skill tools, ``harness`` for central tools."""
    if name in SKILL_TOOLS:
        return SKILL_TOOLS[name]
    return "harness" if name in harness_tools else "unexpected"


def redact(text: str) -> str:
    return _SECRET.sub("[redacted]", text)


def summarize_arguments(arguments: Any) -> dict[str, Any]:
    """Short, safe view of tool arguments: long strings keep a prefix, their length and a hash (never the full text)."""
    if not isinstance(arguments, dict):
        return {"value": redact(str(arguments))[:ARGUMENT_LIMIT]}
    summary: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str) and len(value) > ARGUMENT_LIMIT:
            digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:10]
            summary[key] = f"{redact(value[: ARGUMENT_LIMIT - 20])}… ({len(value)} chars, sha256:{digest})"
        elif isinstance(value, str):
            summary[key] = redact(value)
        elif isinstance(value, int | float | bool) or value is None:
            summary[key] = value
        else:
            summary[key] = redact(json.dumps(value, default=str))[:ARGUMENT_LIMIT]
    return summary


class TraceRecorder:
    """Thread-safe JSON Lines writer (one event per line, monotonically increasing ``seq``)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._seq: int | None = None

    def emit(self, kind: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            if self._seq is None:
                self._seq = _count_lines(self.path)
            self._seq += 1
            record = {"seq": self._seq, "at": datetime.now(UTC).isoformat(), "kind": kind, **fields}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, default=str) + "\n")
        return record


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as stream:
            return sum(1 for _ in stream)
    except OSError:
        return 0


def read_trace(path: Path) -> list[dict[str, Any]]:
    """All events of a trace file; a torn or malformed line is skipped, never fatal."""
    events: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and "kind" in record:
            events.append(record)
    return events

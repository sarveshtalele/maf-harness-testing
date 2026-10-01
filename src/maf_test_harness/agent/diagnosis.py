"""The diagnosis agent: an LLM helper for failures the deterministic recovery could not resolve.

The harness already classifies a failed install or test run (``errors/``) and applies the recoveries the playbook allows
(``envs/recovery.py``). What is left is the unknown and the manual: an error nothing matches, a compiler that is missing, a
build that needs a human choice. This agent looks at the evidence with the read-only typed tools and writes a short note for
the human at the review gate: what happened, whose problem it is, what to do. It changes nothing.

Off by default (``HARNESS_DIAGNOSIS_AGENT=on``): it costs tokens, and it only runs after a failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..llm.prompts import build_prompt

AGENT_NAME = "diagnosis"  # the trace/tool-map name of this loop; deliberately not a workflow stage
LOG_TAIL_CHARS = 4_000

PERSONA = (
    "ROLE: Environment and execution diagnosis engineer.\n"
    "GOAL: explain why a setup or test-run command failed and what the human should do next, using facts from the tools.\n"
    "RULES: call diagnose_env_error / diagnose_exec_error on the log first; use plan_env_fix to see what the harness would "
    "do on its own; read a file only when the log names it. Never claim a fix was applied: you cannot change anything. "
    "Distinguish an environment problem (missing package, wrong toolchain) from a test defect and from an application "
    "defect. If the tools do not recognise the error, say so plainly and quote the decisive log line."
)


class DiagnosisNote(BaseModel):
    summary: str = Field(max_length=500, description="What failed, in one or two sentences.")
    likely_cause: str = Field(max_length=400)
    category: Literal["environment", "infra", "test_defect", "app_defect", "unknown"]
    suggested_fixes: list[str] = Field(default_factory=list, max_length=6)
    needs_human: bool = True


def log_tail(paths: list[str], limit: int = LOG_TAIL_CHARS) -> str:
    """The end of the given log files (the decisive lines are almost always last)."""
    text = ""
    for path in paths:
        try:
            text += Path(path).read_text(encoding="utf-8", errors="replace")[-limit:] + "\n"
        except OSError:
            continue
    return text.strip()[-limit:]


def diagnosis_prompt(
    kind: str, failing: dict[str, Any], log: str, deterministic: list[dict[str, Any]]
) -> str:
    return build_prompt(
        {
            "assignment": f"Diagnose this failed {kind} command and write the note for the human reviewer.",
            "output_contract": DiagnosisNote.model_json_schema(),
            "failing_step": failing,
            "deterministic_diagnoses": deterministic,
            "log_tail": log,
        },
        ("assignment", "output_contract", "failing_step", "deterministic_diagnoses", "log_tail"),
    )


async def diagnose(
    runtime: Any,
    tools: list,
    *,
    kind: str,
    failing: dict[str, Any],
    log: str,
    deterministic: list[dict[str, Any]],
    state: dict,
    persist,
) -> dict[str, Any]:
    """Run the agent; always returns a dict (``{"error": ...}`` when the loop failed): a missing note never blocks a gate."""
    prompt = diagnosis_prompt(kind, failing, log, deterministic)
    try:
        note = await runtime.run_tool_agent(
            stage=f"{AGENT_NAME}_{kind}",
            persona=PERSONA,
            prompt=prompt,
            tools=tools,
            state=state,
            persist=persist,
            response_format=DiagnosisNote,
        )
        return DiagnosisNote.model_validate(note).model_dump() | {"source": "agent"}
    except Exception as exc:  # advisory only
        return {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "source": "agent"}


__all__ = ["AGENT_NAME", "PERSONA", "DiagnosisNote", "diagnose", "diagnosis_prompt", "log_tail"]

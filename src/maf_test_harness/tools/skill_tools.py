"""``check_skill_output``: let a stage agent run the reviewed validators of its own skills on a draft.

The vendored Agent Skills ship validators (``validate_traceability.py``, ``validate_fixtures.py``, ...). The lock policy
is unchanged: only scripts recorded in ``skills/SKILLS.lock.json`` whose bytes still match their SHA-256 may run, argv-only,
in a fresh temp dir with a sanitized environment and a timeout (``skills/scripts.py``). What this tool widens is *who* may
ask: a stage agent may run the reviewed scripts (host tier included) of the skills assigned to its stage, on the files it
hands over, to see the violations before the human gate does. It cannot run anything else, in any other skill, or with
paths outside the temp directory.
"""

from __future__ import annotations

from typing import Any

from ..skills.scripts import HarnessScriptRunner, ScriptRefused
from .registry import ToolContext, ToolResult, ToolSpec, object_schema

MAX_FILE_CHARS = 200_000


def check_skill_output(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.skills_root is None:
        return ToolResult.failure("NO-SKILLS", "the skills directory is not configured for this run")
    skill, script = args["skill"], args["script"]
    if skill not in ctx.allowed_skills:
        return ToolResult.failure(
            "SKILL-SCOPE",
            f"skill '{skill}' is not assigned to this stage",
            fixes=[f"use one of: {', '.join(ctx.allowed_skills) or 'none'}"],
        )
    files = args.get("files") or {}
    if any(len(text) > MAX_FILE_CHARS for text in files.values()):
        return ToolResult.failure("FILE-TOO-LARGE", f"a file is over {MAX_FILE_CHARS} characters")
    runner = HarnessScriptRunner(ctx.skills_root, tiers=("host", "model"))
    try:
        outcome = runner.run(skill, script, args.get("args") or [], files=files)
    except ScriptRefused as exc:
        return ToolResult.failure(
            "SCRIPT-REFUSED", str(exc), fixes=["only reviewed, hash-intact scripts of your skills may run"]
        )
    output = (outcome["stdout"] + outcome["stderr"]).strip()
    passed = outcome["exit_code"] == 0
    return ToolResult(
        passed,
        code="OK" if passed else "SKILL-CHECK-FAILED",
        evidence=output[:600],
        data={
            "skill": skill,
            "script": script,
            "sha256": outcome["sha256"],
            "exit_code": outcome["exit_code"],
        },
        suggested_fixes=[] if passed else ["fix exactly what the validator reports, then check again"],
    )


SKILL_TOOLS = (
    ToolSpec(
        "check_skill_output",
        "Run a reviewed validator script of one of your assigned skills on a draft document and get its verdict. "
        "Files are made available under input/<name>; pass them as args, e.g. ['input/doc.json'].",
        check_skill_output,
        object_schema(
            ["skill", "script"],
            skill={"type": "string", "maxLength": 80},
            script={
                "type": "string",
                "maxLength": 120,
                "description": "path inside the skill, e.g. scripts/validate_fixtures.py",
            },
            args={"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 12},
            files={"type": "object", "additionalProperties": {"type": "string"}, "maxProperties": 6},
        ),
    ),
)

__all__ = ["SKILL_TOOLS", "check_skill_output"]

"""Execute locked host skill entry points and persist truthful script evidence."""

import json
from pathlib import Path
from .scripts import HarnessScriptRunner
from .registry import verify_lock
from ..monitoring.trace import TraceRecorder


def execute_host_skill(
    run_dir: Path, skills_root: Path, stage: str, skill: str, payload: dict, state: dict, audit
) -> dict:
    problems = verify_lock(skills_root, [skill])
    if problems:
        raise ValueError("Host skill integrity failed: " + "; ".join(problems))
    runner = HarnessScriptRunner(skills_root, tiers=("host",))
    audit("skill_script_started", stage=stage, skill=skill, script="scripts/analyze.py")
    result = runner.run(
        skill,
        "scripts/analyze.py",
        ["--request", "input/request.json", "--output", "output.json"],
        files={"request.json": json.dumps(payload)},
        output_json="output.json",
    )
    record = {k: v for k, v in result.items() if k not in {"data", "stdout", "stderr"}}
    record["stage"] = stage
    state.setdefault("skill_script_runs", []).append(record)
    ok = result["exit_code"] == 0 and isinstance(result.get("data"), dict)
    audit("skill_script_completed", **record, ok=ok, error=result["stderr"][:600] if not ok else "")
    TraceRecorder(run_dir / "trace.jsonl").emit(
        "tool_call",
        stage=stage,
        phase="host-static",
        name="run_skill_script",
        category="skill_script",
        arguments={"skill_name": skill, "script_name": "scripts/analyze.py", "sha256": record["sha256"]},
        ok=ok,
        duration_ms=round(record["duration_seconds"] * 1000),
    )
    state.setdefault("skill_tool_trace", {})[stage] = {
        "execution_mode": "host-static",
        "skills": [skill],
        "assigned_skills": [skill],
        "loaded_skills": [skill],
        "verified_host_skill": ok,
        "skill_loaded_via_maf": None,
        "scripts_executed": [record],
    }
    if not ok:
        raise ValueError(f"Host skill {skill} failed: {result['stderr'][:600]}")
    return result["data"]

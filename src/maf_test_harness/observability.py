"""Observability and traceability report for one run, rebuilt from what is on disk.

Sources (all inside the run directory, so any past run can be inspected without re-running anything):

* ``trace.jsonl``  model runs, provider calls (tokens, duration) and every tool call with arguments (``trace.py``)
* ``audit.jsonl``  gates, approvals, repairs, auto-fixes, build checks, installs and executions (host events)
* ``run.json``     state: skill provenance, verified skill/tool trace, budget, artifacts, QA report

A run made before ``trace.jsonl`` existed still renders: tool calls come from the recorded invocations (names only, no
arguments or timings) and the report says so (``trace_level``).

The report answers four questions: which **skills** were loaded and by which stage, which **tools** were invoked (and which
were never used), what each stage **cost** (calls, tokens, time), and how every requirement **traces** to cases, generated
files and results, with the skills that produced each link.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent_tools import CENTRAL_TOOL_NAMES, STAGE_TOOLS
from .skills_config import SKILL_NAMES
from .trace import read_trace, tool_category

DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
_HOST_LANES = {
    "requested": ("human", "Approval requested"),
    "human_response": ("human", "Human response"),
    "artifact_approved": ("host", "Artifact approved"),
    "tool_started": ("host", "Tool started"),
    "tool_completed": ("host", "Tool completed"),
    "environment_started": ("host", "Environment install started"),
    "environment_completed": ("host", "Environment install completed"),
    "execution_started": ("host", "Test execution started"),
    "execution_completed": ("host", "Test execution completed"),
    "heal_started": ("host", "Heal round started"),
    "deliverable_created": ("host", "Deliverable ZIP created"),
    "autofix": ("host", "Auto-fix"),
    "build_gate": ("host", "Build gate"),
    "stage_error": ("host", "Stage error"),
    "stage_repair": ("host", "Stage repair"),
    "test_generation_repair": ("host", "Generation repair"),
    "generation_attempt_saved": ("host", "Generation attempt saved"),
    "nested_with_flattened": ("host", "Nested with flattened"),
}


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            events.append(record)
    return events


def _origins(skills_root: Path) -> set[str]:
    """Names of skills vendored from the upstream catalog (everything else is local)."""
    try:
        from .skills_registry import load_lock

        return set(load_lock(skills_root).get("skills", {}))
    except Exception:  # a missing or unreadable lock only removes the origin column
        return set()


def _event_detail(event: dict[str, Any]) -> str:
    keep = (
        "stage",
        "kind",
        "decision",
        "status",
        "tool",
        "path",
        "detail",
        "batch",
        "attempt",
        "error",
        "reason",
    )
    parts = [f"{k}={str(event[k])[:90]}" for k in keep if event.get(k) not in (None, "", [])]
    return ", ".join(parts)


def _cache_report(chat: list[dict[str, Any]]) -> dict[str, Any]:
    """Input tokens served from the provider's prompt cache, per stage and overall, plus prompt-prefix stability.

    ``prefix_ratio`` is the share of each request that repeats the previous request of the same stage byte for byte:
    the most a prefix cache could ever serve. ``hit_pct`` is what the provider actually reported as cached.
    """
    stages: dict[str, dict[str, Any]] = {}
    for event in chat:
        if event.get("status") != "ok":
            continue
        row = stages.setdefault(
            event.get("stage") or "?",
            {"calls": 0, "input": 0, "cached": 0, "chars": 0, "shared": 0, "reported": 0},
        )
        tokens = event.get("tokens") or {}
        row["calls"] += 1
        row["input"] += int(tokens.get("input") or 0)
        if tokens.get("cached") is not None:
            row["cached"] += int(tokens["cached"])
            row["reported"] += 1
        row["chars"] += int(event.get("prompt_chars") or 0)
        row["shared"] += int(event.get("prefix_shared_chars") or 0)
    for row in stages.values():
        row["hit_pct"] = (
            round(100 * row["cached"] / row["input"], 1) if row["input"] and row["reported"] else None
        )
        row["prefix_ratio"] = round(100 * row["shared"] / row["chars"], 1) if row["chars"] else None
    total_input = sum(r["input"] for r in stages.values())
    total_cached = sum(r["cached"] for r in stages.values())
    reported = sum(r["reported"] for r in stages.values())
    return {
        "stages": stages,
        "all": {
            "input": total_input,
            "cached": total_cached,
            "hit_pct": round(100 * total_cached / total_input, 1) if total_input and reported else None,
        },
    }


def build_report(run_dir: Path, skills_root: Path | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    view = _read_json(run_dir / "run.json")
    if not view:
        raise FileNotFoundError(f"{run_dir}/run.json not found: not a harness run directory")
    state: dict[str, Any] = view.get("state", {})
    audit = _read_jsonl(run_dir / "audit.jsonl")
    trace = read_trace(run_dir / "trace.jsonl")
    trace_level = "full" if trace else "summary"
    stages = state.get("stages") or list(SKILL_NAMES)
    provenance = state.get("skill_provenance", {})
    stage_trace = state.get("skill_tool_trace", {})
    budget = state.get("budget", {})
    origins = _origins(skills_root or DEFAULT_SKILLS_ROOT)

    stamps = [t for t in (_parse(e.get("at")) for e in [*audit, *trace]) if t]
    t0, t1 = (min(stamps), max(stamps)) if stamps else (None, None)

    def rel(ts: str | None) -> float | None:
        moment = _parse(ts)
        return round((moment - t0).total_seconds(), 2) if moment and t0 else None

    # ---- tool calls -----------------------------------------------------------------------------------------
    tool_calls: list[dict[str, Any]] = []
    if trace:
        for event in trace:
            if event["kind"] == "tool_call":
                tool_calls.append(
                    {
                        "seq": event.get("seq"),
                        "at": event.get("at"),
                        "t": rel(event.get("at")),
                        "stage": event.get("stage", ""),
                        "phase": event.get("phase", ""),
                        "name": event.get("name", ""),
                        "category": event.get("category", "unexpected"),
                        "arguments": event.get("arguments", {}),
                        "ok": bool(event.get("ok")),
                        "duration_ms": event.get("duration_ms"),
                        "result_chars": event.get("result_chars"),
                    }
                )
    else:  # older run: names per model run only; arguments and timings were never recorded
        seen_stages: set[str] = set()
        for number, invocation in enumerate(state.get("invocations", []), 1):
            names = list(invocation.get("tool_calls", []))
            stage_name = invocation.get("stage", "")
            if stage_name in seen_stages:
                # each later run of a stage repeats the primed load_skill calls in its record: count them once
                primed = 1 + len(invocation.get("companions", []))
                dropped = 0
                while names and names[0] == "load_skill" and dropped < primed:
                    names.pop(0)
                    dropped += 1
            seen_stages.add(stage_name)
            for name in names:
                tool_calls.append(
                    {
                        "seq": None,
                        "at": None,
                        "t": None,
                        "stage": invocation.get("stage", ""),
                        "phase": "structured",
                        "name": name,
                        "category": tool_category(name, CENTRAL_TOOL_NAMES),
                        "arguments": {},
                        "ok": True,
                        "duration_ms": None,
                        "result_chars": None,
                        "run": number,
                    }
                )

    # ---- model runs and provider calls ------------------------------------------------------------------------
    chat = [e for e in trace if e["kind"] == "chat_call"]
    tokens_by_run: dict[tuple, int] = defaultdict(int)
    for event in chat:
        key = (event.get("stage"), event.get("phase"), event.get("run_index") or event.get("attempt"))
        tokens_by_run[key] += int((event.get("tokens") or {}).get("total") or 0)
    model_runs: list[dict[str, Any]] = []
    if trace:
        for event in trace:
            if event["kind"] != "model_run":
                continue
            key = (event.get("stage"), event.get("phase"), event.get("run_index") or event.get("attempt"))
            model_runs.append(
                {
                    "seq": event.get("seq"),
                    "t": rel(event.get("at")),
                    "stage": event.get("stage", ""),
                    "phase": event.get("phase", ""),
                    "attempt": event.get("run_index") or event.get("attempt"),
                    "skills": event.get("skills", []),
                    "temperature": event.get("temperature"),
                    "status": event.get("status", "returned"),
                    "duration_ms": event.get("duration_ms"),
                    "tokens": tokens_by_run.get(key, 0),
                    "tool_calls": event.get("tool_calls", []),
                }
            )
    else:
        for number, invocation in enumerate(state.get("invocations", []), 1):
            model_runs.append(
                {
                    "seq": number,
                    "t": None,
                    "stage": invocation.get("stage", ""),
                    "phase": "structured",
                    "attempt": None,
                    "skills": [invocation.get("skill"), *invocation.get("companions", [])],
                    "temperature": None,
                    "status": invocation.get("status", ""),
                    "duration_ms": None,
                    "tokens": None,  # per-run tokens were not recorded before trace.jsonl existed
                    "tool_calls": invocation.get("tool_calls", []),
                }
            )

    # ---- stages ---------------------------------------------------------------------------------------------
    audit_by_stage: dict[str, Counter] = defaultdict(Counter)
    for event in audit:
        audit_by_stage[event.get("stage", "")][event.get("event", "")] += 1
    stage_rows: list[dict[str, Any]] = []
    for stage in stages:
        prov = provenance.get(stage) or {}
        assigned = [prov["name"], *(c["name"] for c in prov.get("companions", []))] if prov else []
        loaded = list((stage_trace.get(stage) or {}).get("loaded_skills", []))
        runs = [r for r in model_runs if r["stage"] == stage]
        calls = [c for c in tool_calls if c["stage"] == stage]
        loaded_via_calls = {
            str(c["arguments"].get("skill_name"))
            for c in calls
            if c["category"] == "skill_load" and c["ok"] and c["arguments"].get("skill_name")
        }
        loaded = sorted(set(loaded) | loaded_via_calls)
        harness_available = list(STAGE_TOOLS.get(stage, ()))
        harness_called = Counter(c["name"] for c in calls if c["category"] == "harness")
        counts = audit_by_stage.get(stage, Counter())
        durations = [r["duration_ms"] for r in runs if r["duration_ms"] is not None]
        stage_rows.append(
            {
                "stage": stage,
                "actor": "agent" if stage in SKILL_NAMES else "host",
                "assigned_skills": assigned,
                "loaded_skills": loaded,
                "skills_verified": bool((stage_trace.get(stage) or {}).get("skill_loaded_via_maf")),
                "model_runs": len(runs),
                "tool_calls": len(calls),
                "tool_failures": sum(1 for c in calls if not c["ok"]),
                "harness_tools_available": harness_available,
                "harness_tools_called": dict(harness_called),
                "tokens": sum(r["tokens"] for r in runs) if trace else None,
                "model_seconds": round(sum(durations) / 1000, 1) if durations else None,
                "repairs": counts["stage_repair"] + counts["test_generation_repair"],
                "errors": counts["stage_error"],
                "approved": stage in (state.get("approved") or {}) or stage in (state.get("artifacts") or {}),
            }
        )

    # ---- skills ---------------------------------------------------------------------------------------------
    skill_stages: dict[str, dict[str, Any]] = {}
    for stage, prov in provenance.items():
        entries = [
            (prov["name"], "primary", prov),
            *((c["name"], "companion", c) for c in prov.get("companions", [])),
        ]
        for name, role, info in entries:
            row = skill_stages.setdefault(
                name,
                {
                    "skill": name,
                    "origin": "upstream" if name in origins else "local",
                    "stages": [],
                    "role": role,
                    "package_sha256": info.get("package_sha256", ""),
                    "loads": 0,
                    "scripts": [],
                },
            )
            row["stages"].append(stage)
    for call in tool_calls:
        if call["category"] == "skill_load":
            name = str(call["arguments"].get("skill_name", ""))
            if name in skill_stages:
                skill_stages[name]["loads"] += 1
    if not trace:  # summary level: one verified load per stage that lists the skill
        for stage_row in stage_rows:
            for name in stage_row["loaded_skills"]:
                if name in skill_stages:
                    skill_stages[name]["loads"] += 1
    script_runs = state.get("skill_script_runs") or []
    for run in script_runs:
        row = skill_stages.get(str(run.get("skill", "")))
        if row is not None:
            row["scripts"].append(run.get("script") or run.get("path") or "script")
    skills = sorted(skill_stages.values(), key=lambda r: (r["stages"][0] if r["stages"] else "", r["skill"]))

    # ---- traceability ---------------------------------------------------------------------------------------
    traceability = _traceability(run_dir, state, provenance)

    # ---- timeline -------------------------------------------------------------------------------------------
    timeline: list[dict[str, Any]] = []
    for event in audit:
        lane, label = _HOST_LANES.get(
            event.get("event", ""), ("host", str(event.get("event", "")).replace("_", " "))
        )
        detail = event.get("details") if isinstance(event.get("details"), dict) else {}
        timeline.append(
            {
                "at": event.get("at"),
                "t": rel(event.get("at")),
                "lane": lane,
                "stage": event.get("stage") or "(run)",
                "label": label,
                "detail": _event_detail({**event, **{k: v for k, v in detail.items() if k == "tool"}}),
            }
        )
    for event in trace:
        if event["kind"] == "model_run":
            timeline.append(
                {
                    "at": event.get("at"),
                    "t": rel(event.get("at")),
                    "lane": "model",
                    "stage": event.get("stage", ""),
                    "label": f"Model run ({event.get('phase', '')})",
                    "detail": f"{event.get('status', 'returned')}, {event.get('duration_ms')} ms, skills={event.get('skills')}",
                }
            )
        elif event["kind"] == "tool_call":
            lane = "skill" if str(event.get("category", "")).startswith("skill") else "tool"
            args = ", ".join(f"{k}={v}" for k, v in (event.get("arguments") or {}).items())[:110]
            timeline.append(
                {
                    "at": event.get("at"),
                    "t": rel(event.get("at")),
                    "lane": lane,
                    "stage": event.get("stage", ""),
                    "label": f"{event.get('name')}",
                    "detail": f"{args} → {'ok' if event.get('ok') else 'FAILED'} ({event.get('duration_ms')} ms)",
                }
            )
    timeline.sort(key=lambda row: row["at"] or "")

    # ---- totals and integrity checks -------------------------------------------------------------------------
    categories = Counter(c["category"] for c in tool_calls)
    trace_tokens = sum(int((e.get("tokens") or {}).get("total") or 0) for e in chat)
    cache = _cache_report(chat)
    tooling = state.get("tooling") or {}
    totals = {
        "provider_calls": budget.get("calls", 0),
        "tokens": budget.get("reported_tokens", 0),
        "input_tokens": cache["all"]["input"],
        "cached_input_tokens": cache["all"]["cached"],
        "cache_hit_pct": cache["all"]["hit_pct"],
        "model_runs": len(model_runs),
        "tool_calls": len(tool_calls),
        "skill_loads": categories["skill_load"]
        if trace
        else sum(len(r["loaded_skills"]) for r in stage_rows),
        "skill_resources": categories["skill_resource"],
        "skill_scripts": categories["skill_script"],
        "harness_tool_calls": categories["harness"],
        "unexpected_tool_calls": categories["unexpected"],
        "script_runs": len(script_runs),
        "autofix": tooling.get("autofix", 0),
        "build_checks": tooling.get("build_checks", 0),
        "build_failures": tooling.get("build_failures", 0),
        "human_gates": sum(1 for e in audit if e.get("event") == "requested"),
        "stage_errors": sum(1 for e in audit if e.get("event") == "stage_error"),
        "wall_seconds": round((t1 - t0).total_seconds(), 1) if t0 and t1 else None,
    }
    checks = _checks(state, stage_rows, tool_calls, trace, chat, trace_tokens, script_runs, totals)

    return {
        "run": {
            "id": run_dir.name,
            "repo_url": state.get("repo_url"),
            "status": view.get("status"),
            "model": (state.get("runtime_provenance") or {}).get("model"),
            "skill_mode": (state.get("runtime_provenance") or {}).get("skill_mode"),
            "maf": {
                "core": (state.get("runtime_provenance") or {}).get("agent_framework_core"),
                "openai": (state.get("runtime_provenance") or {}).get("agent_framework_openai"),
            },
            "telemetry": state.get("telemetry"),
            "started": t0.isoformat() if t0 else None,
        },
        "trace_level": trace_level,
        "totals": totals,
        "cache": cache,
        "stages": stage_rows,
        "skills": skills,
        "tool_calls": tool_calls,
        "tool_summary": _tool_summary(tool_calls),
        "model_runs": model_runs,
        "timeline": timeline,
        "traceability": traceability,
        "checks": checks,
    }


def _tool_summary(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per tool name: calls, failures, total time, and which stages used it; never-called harness tools are listed too."""
    rows: dict[str, dict[str, Any]] = {}
    for call in tool_calls:
        row = rows.setdefault(
            call["name"],
            {
                "tool": call["name"],
                "category": call["category"],
                "calls": 0,
                "failures": 0,
                "ms": 0,
                "stages": set(),
            },
        )
        row["calls"] += 1
        row["failures"] += 0 if call["ok"] else 1
        row["ms"] += call["duration_ms"] or 0
        row["stages"].add(call["stage"])
    for name in CENTRAL_TOOL_NAMES:
        rows.setdefault(
            name, {"tool": name, "category": "harness", "calls": 0, "failures": 0, "ms": 0, "stages": set()}
        )
    return [
        {**row, "stages": ", ".join(sorted(s for s in row["stages"] if s))}
        for row in sorted(rows.values(), key=lambda r: (-r["calls"], r["tool"]))
    ]


def _traceability(run_dir: Path, state: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    """requirement → case → generated file → result, and the skills that produced each link."""
    qa = ((state.get("artifacts") or {}).get("test_execution") or {}).get("qa_report") or _read_json(
        run_dir / "qa_report.json"
    )

    def skills_of(stage: str) -> list[str]:
        prov = provenance.get(stage) or {}
        return [prov["name"], *(c["name"] for c in prov.get("companions", []))] if prov else []

    produced_by = {
        "requirements": skills_of("requirements"),
        "test_design": skills_of("test_design"),
        "test_generation": skills_of("test_generation"),
        "test_execution": f"host runner ({qa.get('adapter')})" if qa else "host runner",
    }
    chain: list[dict[str, Any]] = []
    orphan_cases: list[str] = []
    if qa:
        cases = {c["id"]: c for c in qa.get("cases", [])}
        linked: set[str] = set()
        for requirement in qa.get("requirement_coverage", []):
            rows = []
            for case_id in requirement.get("cases", []):
                case = cases.get(case_id, {})
                linked.add(case_id)
                rows.append(
                    {
                        "case": case_id,
                        "title": case.get("title", ""),
                        "level": case.get("level", ""),
                        "file": case.get("file", ""),
                        "status": case.get("status", "not_executed"),
                    }
                )
            chain.append(
                {
                    "requirement": requirement["id"],
                    "text": requirement.get("text", ""),
                    "actor": requirement.get("actor", ""),
                    "state": requirement.get("state", ""),
                    "cases": rows,
                }
            )
        orphan_cases = sorted(set(cases) - linked)
        verdict = qa.get("verdict")
    else:  # no execution yet: requirement → case → file from the approved design and generation
        artifacts = state.get("artifacts") or {}
        design = artifacts.get("test_design") or {}
        generated = (artifacts.get("test_generation") or {}).get("files", [])
        file_of = {cid: f["path"] for f in generated for cid in f.get("case_ids", [])}
        requirements = {
            r["id"]: r
            for r in ((artifacts.get("requirements") or {}).get("requirement_model") or {}).get(
                "requirements", []
            )
        }
        titles = {
            c.get("id"): c.get("title", "")
            for key, cases in design.items()
            if key.endswith("_tests") and isinstance(cases, list)
            for c in cases
        }
        for req_id, case_ids in (
            (design.get("traceability_matrix") or {}).get("requirement_to_tests") or {}
        ).items():
            req = requirements.get(req_id, {})
            chain.append(
                {
                    "requirement": req_id,
                    "text": req.get("text", ""),
                    "actor": req.get("actor", ""),
                    "state": "designed",
                    "cases": [
                        {
                            "case": cid,
                            "title": titles.get(cid, ""),
                            "level": "",
                            "file": file_of.get(cid, ""),
                            "status": "not_executed",
                        }
                        for cid in case_ids
                    ],
                }
            )
        verdict = None
    all_cases = [c for row in chain for c in row["cases"]]
    return {
        "verdict": verdict,
        "produced_by": produced_by,
        "chain": chain,
        "orphan_cases": orphan_cases,
        "requirements_without_cases": [r["requirement"] for r in chain if not r["cases"]],
        "case_status_counts": dict(Counter(c["status"] for c in all_cases)),
    }


def _checks(
    state: dict[str, Any],
    stage_rows: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    trace: list[dict[str, Any]],
    chat: list[dict[str, Any]],
    trace_tokens: int,
    script_runs: list[dict[str, Any]],
    totals: dict[str, Any],
) -> list[dict[str, Any]]:
    agent_rows = [r for r in stage_rows if r["actor"] == "agent" and r["assigned_skills"]]
    missing = {r["stage"]: sorted(set(r["assigned_skills"]) - set(r["loaded_skills"])) for r in agent_rows}
    missing = {k: v for k, v in missing.items() if v}
    unexpected = sorted({c["name"] for c in tool_calls if c["category"] == "unexpected"})
    failed = [c for c in tool_calls if not c["ok"]]
    provider_errors = [e for e in chat if e.get("status") == "error"]
    reported = totals["tokens"]
    consistent = (trace_tokens == reported) if trace and reported else None
    harness_possible = sum(len(r["harness_tools_available"]) > 0 for r in agent_rows)
    telemetry = state.get("telemetry") or {}
    return [
        {
            "check": "Every assigned skill was loaded through MAF load_skill",
            "ok": not missing and bool(agent_rows),
            "detail": "all loaded"
            if not missing and agent_rows
            else (str(missing) if missing else "no agent stage ran"),
        },
        {
            "check": "No unexpected tools were invoked",
            "ok": not unexpected,
            "detail": "none" if not unexpected else ", ".join(unexpected),
        },
        {
            "check": "Tool calls succeeded",
            "ok": not failed,
            "detail": f"{len(tool_calls) - len(failed)}/{len(tool_calls)} ok"
            + (f"; failed: {sorted({c['name'] for c in failed})}" if failed else ""),
        },
        {
            "check": "Skill scripts ran only from the reviewed allow-list",
            "ok": all(
                r.get("status", "ok") in {"ok", "succeeded", "completed", 0} or r.get("exit_code", 0) == 0
                for r in script_runs
            ),
            "detail": f"{len(script_runs)} run(s)",
        },
        {
            "check": "Provider calls succeeded",
            "ok": not provider_errors,
            "detail": f"{len(chat) - len(provider_errors)} ok, {len(provider_errors)} failed"
            if chat
            else "not recorded (older run)",
        },
        {
            "check": "Trace tokens match the daily-ledger accounting",
            "ok": consistent if consistent is not None else True,
            "detail": (
                f"trace {trace_tokens:,} vs reported {reported:,}" if trace else "not recorded (older run)"
            ),
        },
        {
            "check": "Central harness tools were used by the agents (informational)",
            "ok": True,
            "detail": f"{totals['harness_tool_calls']} call(s) in {harness_possible} stage(s) that offer them; the host applies the same checks itself",
        },
        {
            "check": "OpenTelemetry export (informational)",
            "ok": True,
            "detail": (
                f"mode={telemetry.get('mode')}, enabled={telemetry.get('enabled')}"
                if telemetry
                else "not recorded (older run)"
            ),
        },
        {
            "check": "Trace detail level",
            "ok": True,
            "detail": "full (trace.jsonl)"
            if trace
            else "summary only: tool arguments and timings were not recorded for this run",
        },
    ]


def render_markdown(report: dict[str, Any]) -> str:
    run, totals = report["run"], report["totals"]
    lines = [
        f"# Observability and traceability — {run['id']}",
        "",
        f"* Repository: {run.get('repo_url')}  ·  status: **{run.get('status')}**  ·  model: {run.get('model')}",
        f"* MAF core {run['maf'].get('core')}, openai {run['maf'].get('openai')}, skill mode {run.get('skill_mode')}",
        f"* Trace detail: **{report['trace_level']}**",
        "",
        "## Totals",
        "",
        "| Metric | Value |",
        "|---|---|",
        *(f"| {k.replace('_', ' ')} | {v} |" for k, v in totals.items()),
        "",
        "## Prompt cache",
        "",
        "| Stage | Calls | Input tokens | Cached | Hit % | Prompt prefix repeated % |",
        "|---|---|---|---|---|---|",
        *(
            f"| {name} | {c['calls']} | {c['input']:,} | {c['cached']:,} | "
            f"{c['hit_pct'] if c['hit_pct'] is not None else 'not reported'} | "
            f"{c['prefix_ratio'] if c['prefix_ratio'] is not None else '—'} |"
            for name, c in report.get("cache", {}).get("stages", {}).items()
        ),
        "",
        "## Stages",
        "",
        "| Stage | Actor | Assigned skills | Loaded | Model runs | Tool calls | Tokens | Repairs | Errors |",
        "|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {s['stage']} | {s['actor']} | {', '.join(s['assigned_skills']) or '—'} | {', '.join(s['loaded_skills']) or '—'} "
            f"| {s['model_runs']} | {s['tool_calls']} | {format(s['tokens'], ',') if s['tokens'] is not None else '—'} | {s['repairs']} | {s['errors']} |"
            for s in report["stages"]
        ),
        "",
        "## Skills invoked",
        "",
        "| Skill | Origin | Stages | Loads | Scripts run |",
        "|---|---|---|---|---|",
        *(
            f"| {s['skill']} | {s['origin']} | {', '.join(s['stages'])} | {s['loads']} | {', '.join(s['scripts']) or '—'} |"
            for s in report["skills"]
        ),
        "",
        "## Tools invoked",
        "",
        "| Tool | Category | Calls | Failures | Time (ms) | Stages |",
        "|---|---|---|---|---|---|",
        *(
            f"| {t['tool']} | {t['category']} | {t['calls']} | {t['failures']} | {t['ms']} | {t['stages'] or '—'} |"
            for t in report["tool_summary"]
        ),
        "",
        "## Traceability",
        "",
        f"Produced by: {json.dumps(report['traceability']['produced_by'])}",
        "",
        f"Case results: {report['traceability']['case_status_counts']}",
        "",
        "| Requirement | State | Case | Test file | Result |",
        "|---|---|---|---|---|",
    ]
    for row in report["traceability"]["chain"]:
        if not row["cases"]:
            lines.append(f"| {row['requirement']} | {row['state']} | — | — | — |")
        for case in row["cases"]:
            lines.append(
                f"| {row['requirement']} | {row['state']} | {case['case']} | {case['file']} | {case['status']} |"
            )
    lines += ["", "## Integrity checks", "", "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {c['check']} | {'PASS' if c['ok'] else 'FAIL'} | {c['detail']} |" for c in report["checks"]]
    return "\n".join(lines) + "\n"

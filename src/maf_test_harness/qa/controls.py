"""End-to-end traceability: host-assigned control IDs from the test plan to the final report.

Pure functions, no I/O. The host assigns every ID (models never write them), so no model-facing schema changes.

    PLN-xx   plan area, in plan order; PLN-00 = requirement that no planned area covers
    CTL-nnn  one control per requirement, in natural requirement-ID order
"""

from __future__ import annotations

import re
from typing import Any

from ..hooks.builtin import redact

NOT_PLANNED = "PLN-00"
FAILED_AT_ORDER = ("planning", "design", "generation", "environment_setup", "test_execution")
# Worst status wins for a control with several cases.
SEVERITY = {"passed": 0, "not_executed": 1, "failed": 2, "error": 3, "generation_failed": 4}
_DIGITS = re.compile(r"(\d+)")


def area_id(index: int) -> str:
    """``index`` is 1-based plan order."""
    return f"PLN-{index:02d}"


def _natural(value: str) -> list[Any]:
    # Digit runs compare as numbers, so REQ-2 sorts before REQ-10. Parts alternate text/number by position.
    return [int(part) if part.isdigit() else part for part in _DIGITS.split(value)]


def _best_area(requirement: dict[str, Any], areas: list[dict[str, Any]]) -> int | None:
    files = set(requirement.get("source_files") or [])
    symbol = str(requirement.get("source_symbol") or "")
    best, best_score = None, 0
    for index, area in enumerate(areas):
        score = len(files & set(area.get("evidence_files") or []))
        text = " ".join([str(area.get("objective") or ""), *map(str, area.get("scenarios") or [])])
        if symbol and re.search(rf"(?<!\w){re.escape(symbol)}(?!\w)", text):  # whole word: "run" != "running"
            score += 1
        if score > best_score:  # strict: ties keep the first area in plan order
            best, best_score = index, score
    return best


def assign_controls(
    requirements_artifact: dict[str, Any], plan_artifact: dict[str, Any] | None
) -> list[dict]:
    """One control per requirement, mapped to the plan area whose evidence files overlap its source files most."""
    areas = (plan_artifact or {}).get("areas") or []
    model = (requirements_artifact or {}).get("requirement_model") or {}
    requirements = sorted(model.get("requirements") or [], key=lambda r: _natural(str(r["id"])))
    width = max(3, len(str(len(requirements))))
    controls = []
    for number, requirement in enumerate(requirements, 1):
        index = _best_area(requirement, areas)
        area = areas[index] if index is not None else {}
        controls.append(
            {
                "control_id": f"CTL-{number:0{width}d}",
                "requirement_id": str(requirement["id"]),
                "requirement_text": " ".join(str(requirement.get("text") or "").split())[:160],
                # No plan at all (older runs) leaves the area blank so the design path decides the failure.
                "plan_area_id": (
                    str(area.get("area_id") or area_id(index + 1))
                    if index is not None
                    else NOT_PLANNED
                    if areas
                    else ""
                ),
                "plan_area_name": (
                    str(area.get("name") or "") if index is not None else "Not planned" if areas else ""
                ),
                "level": str(area.get("level") or "") if index is not None else "",
            }
        )
    return controls


def add_plan_controls(plan: dict[str, Any], requirements_artifact: dict[str, Any]) -> dict[str, Any]:
    """Host fields on a validated test plan: ``area_id`` per area and the ``controls`` list. Mutates and returns it."""
    for index, area in enumerate(plan.get("areas") or [], 1):
        area["area_id"] = area_id(index)
    plan["controls"] = assign_controls(requirements_artifact, plan)
    return plan


def plan_controls(requirements_artifact: dict[str, Any], plan_artifact: dict[str, Any] | None) -> list[dict]:
    """Stored controls, or controls computed on the fly for runs created before control IDs existed."""
    return (plan_artifact or {}).get("controls") or assign_controls(requirements_artifact, plan_artifact)


def case_controls(case: dict[str, Any], controls: list[dict]) -> list[str]:
    """Control IDs whose requirement is linked by the case. Exact ID tokens: REQ-1 never matches REQ-10."""
    linked = {
        str(link.get("id"))
        for link in case.get("traceability_links") or []
        if isinstance(link, dict) and link.get("type") == "requirement"
    }
    return [control["control_id"] for control in controls if control["requirement_id"] in linked]


def environment_outcome(environment: dict[str, Any] | None) -> tuple[str, str]:
    """(ok | failed | skipped | not_run, reason) from the environment stage status."""
    env = environment or {}
    status = str(env.get("status") or "")
    reason = "; ".join(
        str(item) for item in env.get("missing_prerequisites") or env.get("missing_packages") or []
    )
    if status in {"ready", "not_needed"}:
        return "ok", ""
    if status in {"blocked", "failed", "incomplete"}:
        return "failed", (reason or status)
    if status == "skipped":
        return "skipped", str(env.get("reason") or "")
    return "not_run", ""


def _case_outcome(case: dict[str, Any], failures: dict[str, str], env_status: str, env_reason: str) -> dict:
    case_id, status, file = str(case["id"]), case.get("status"), case.get("file") or ""
    message = str(case.get("message") or "")
    if not file:
        generation = (
            "generation_failed" if case_id in failures or status == "generation_failed" else "not_generated"
        )
        reason = failures.get(case_id) or message or "No generated test file for this case."
        final, failed_at = "generation_failed", "generation"
    else:
        generation, reason, failed_at = "generated", "", ""
        if status in {"failed", "error"}:
            final, failed_at, reason = status, "test_execution", message
        elif status == "passed":
            final = "passed"
        elif status == "skipped":
            final, failed_at, reason = "not_executed", "", f"skipped: {message}"
        elif env_status == "failed":
            final, failed_at, reason = "not_executed", "environment_setup", env_reason
        else:
            final, failed_at, reason = "not_executed", "test_execution", message
    return {
        "case_id": case_id,
        "title": case.get("title", ""),
        "level": case.get("level", ""),
        "file": file,
        "confidence": case.get("confidence"),
        "generation": generation,
        "execution": status if file else "not_executed",
        "final_status": final,
        "failed_at": failed_at,
        "reason": redact(reason)[0][:300],
    }


def _design_gap(requirement_id: str, notes: str) -> str:
    token = re.compile(r"(?<![\w-])" + re.escape(requirement_id) + r"(?![\w-])", re.I)
    for segment in re.split(r";\s*|\n", notes):
        if token.search(segment):
            return segment.strip()
    return ""


def trace_rows(
    controls: list[dict],
    cases: list[dict],
    generation: dict[str, Any],
    environment: dict[str, Any] | None,
    design: dict[str, Any] | None,
) -> list[dict]:
    """One row per control: the cases linked to its requirement, each step's outcome and the first failing step."""
    failures = {
        cid: str(failure.get("last_error") or "")
        for failure in generation.get("generation_failures") or []
        for cid in failure.get("case_ids") or []
    }
    env_status, env_reason = environment_outcome(environment)
    gaps = (design or {}).get("gaps_report") or {}
    notes = str(gaps.get("notes") or "")
    rows = []
    for control in controls:
        linked = [case for case in cases if control["requirement_id"] in case.get("requirements", [])]
        outcomes = [_case_outcome(case, failures, env_status, env_reason) for case in linked]
        if not outcomes:
            requirement_id = control["requirement_id"]
            witnesses = gaps.get("pre_existing_test_witnesses") or {}
            generation_state = execution_state = "n/a"
            if requirement_id in witnesses:
                final, failed_at = "covered_by_existing_test", ""
                reason = ", ".join(sorted({str(w.get("test_file")) for w in witnesses[requirement_id]}))
            elif requirement_id in (gaps.get("excluded_requirements") or []):
                final, failed_at = "excluded", ""
                reason = _design_gap(requirement_id, notes) or "Excluded with an ID-linked reason."
            else:
                final = "not_designed"
                failed_at = "planning" if control["plan_area_id"] == NOT_PLANNED else "design"
                reason = (
                    "Requirement is not in any planned test area."
                    if failed_at == "planning"
                    else _design_gap(requirement_id, notes)
                    or "No test case was designed for this requirement."
                )
        else:
            worst = max(outcomes, key=lambda o: SEVERITY[o["final_status"]])
            final = worst["final_status"]
            # The step comes from the outcomes that carry the worst status, not from a milder one.
            steps = [o["failed_at"] for o in outcomes if o["final_status"] == final and o["failed_at"]]
            failed_at = min(steps, key=FAILED_AT_ORDER.index) if final != "passed" and steps else ""
            reason = next(
                (
                    o["reason"]
                    for o in outcomes
                    if o["final_status"] == final and o["failed_at"] == failed_at and o["reason"]
                ),
                "",
            )
            generation_state = next(
                (o["generation"] for o in outcomes if o["generation"] != "generated"), "generated"
            )
            execution_state = worst["execution"]
        rows.append(
            {
                **control,
                "environment": env_status,
                "case_ids": [o["case_id"] for o in outcomes],
                "test_files": sorted({o["file"] for o in outcomes if o["file"]}),
                "generation": generation_state,
                "execution": execution_state,
                "final_status": final,
                "failed_at": failed_at,
                "reason": redact(reason)[0][:300],
                "cases": outcomes,
            }
        )
    return rows


def summarize_trace(rows: list[dict]) -> dict[str, Any]:
    final: dict[str, int] = {}
    failed: dict[str, int] = {}
    for row in rows:
        final[row["final_status"]] = final.get(row["final_status"], 0) + 1
        if row["failed_at"]:
            failed[row["failed_at"]] = failed.get(row["failed_at"], 0) + 1
    return {
        "controls": len(rows),
        "final_status": dict(sorted(final.items())),
        "failed_at": {step: failed[step] for step in FAILED_AT_ORDER if step in failed},
    }


def summary_text(summary: dict[str, Any]) -> str:
    """One line: controls, passed, covered by existing test, excluded and failed-at counts; zero parts omitted."""
    final = summary.get("final_status") or {}
    parts = [f"**{summary.get('controls', 0)}**"]
    for label, key in (
        ("passed", "passed"),
        ("covered by existing", "covered_by_existing_test"),
        ("excluded", "excluded"),
    ):
        if final.get(key):
            parts.append(f"{label} {final[key]}")
    failed = summary.get("failed_at") or {}
    if failed:
        parts.append("failed at: " + ", ".join(f"{step} {count}" for step, count in failed.items()))
    return "Controls: " + " · ".join(parts)


def trace_matrix(state: dict[str, Any], execution: dict[str, Any] | None = None) -> list[dict]:
    """Traceability rows for a whole run, from its saved artifacts (runs without stored controls are recomputed)."""
    from .report import build_qa_report  # lazy: report imports this module

    arts = state.get("artifacts", {})
    if execution is None:
        execution = (arts.get("test_execution") or {}).get("execution")
    environment = state.get("environment") or {}
    qa = build_qa_report(
        repo=arts.get("repo_analysis") or {},
        requirements=arts.get("requirements") or {},
        design=arts.get("test_design") or {},
        generation=arts.get("test_generation") or {},
        execution=execution,
        plan=arts.get("test_planning"),
        environment={**(environment.get("plan") or {}), "status": environment.get("status")},
    )
    return qa["traceability"]

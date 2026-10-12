"""Deterministic QA report: requirement -> approved case -> generated test -> executed result.

No model is involved. Two separate signals are reported and never merged: "source-review accepted" (the independent
reviewer's host-computed confidence, a static judgement) and "passed" (the executed result). A requirement is "passed"
only when every linked case was executed and passed. A case accepted by the review that then fails on an assertion is a
`review_miss`.
"""

from __future__ import annotations

import re
from typing import Any

from ..envs.runners import map_results_to_cases
from ..qa import ledger
from ..qa.confidence import review_accepted
from ..settings import TEST_REVIEW_MIN_CONFIDENCE, design_review_enabled
from .controls import case_controls, plan_controls, summarize_trace, summary_text, trace_rows
from .triage import build_updates
from .verification import documented_quote, is_verified, summarize_triage, triage_cases

# Assertion-failure signatures of the common runners: "AssertionError", pytest's "assert a == b" rewrite, and
# "expected X got Y" / "expected X but was Y" messages. A bare word "assert" in a traceback or a name does not count.
_ASSERTION_MISMATCH = re.compile(
    r"AssertionError|\bassert\s+[^\n]{1,200}?\s==\s|\bexpected\b[^\n]{0,200}?\b(?:got|but was)\b",
    re.IGNORECASE,
)

SECTIONS = ("positive", "negative", "boundary", "validation", "contract")


def md_cell(value: Any, limit: int = 0) -> str:
    """Table-safe text: pipes and newlines would split or break a Markdown row; truncation is marked."""
    text = " ".join(str(value if value is not None else "").split()).replace("|", "\\|")
    return text[: limit - 1] + "…" if limit and len(text) > limit else text


def design_cases(design: dict[str, Any]) -> list[dict[str, Any]]:
    return [case for section in SECTIONS for case in design.get(f"{section}_tests", []) or []]


CASE_COLUMNS = ("Sno", "Requirement ID", "Test Case ID", "Test Case Description", "Steps", "Expected Result")


def _flat(value: Any) -> str:
    """Single-line text for list items; None becomes an empty string."""
    return " ".join(str(value if value is not None else "").split())


def case_requirement_ids(case: dict[str, Any], requirements: dict[str, Any] | None = None) -> list[str]:
    """Requirement IDs for one case, resolved the way the host does.

    Direct ``requirement`` links first, then the parent requirement of each linked acceptance criterion (from the
    requirements artifact). Only when neither exists, the raw link IDs (validation, api_contract) are used.
    """
    links = [link for link in case.get("traceability_links") or [] if isinstance(link, dict)]
    model = (requirements or {}).get("requirement_model") or {}
    parent_of = {
        str(item.get("id")): str(item.get("requirement_id"))
        for item in model.get("acceptance_criteria") or []
        if isinstance(item, dict) and item.get("requirement_id")
    }
    ids = [str(link.get("id")) for link in links if link.get("type") == "requirement"]
    ids += [
        parent_of[str(link.get("id"))]
        for link in links
        if link.get("type") == "acceptance_criterion" and str(link.get("id")) in parent_of
    ]
    if not ids:
        ids = [str(link.get("id")) for link in links]
    return list(dict.fromkeys(item for item in ids if item and item != "None"))


def case_table_rows(
    design: dict[str, Any], requirements: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """One row per case, grouped by category; Sno restarts at 1 in each group.

    Carries every field a reviewer needs (priority, level, actor, target, preconditions, data notes, all links).
    Older runs without ``steps`` yield an empty list (rendered as a dash).
    """
    rows: list[dict[str, Any]] = []
    for group in SECTIONS:
        for sno, case in enumerate(design.get(f"{group}_tests") or [], 1):
            rows.append(
                {
                    "group": group,
                    "sno": sno,
                    "requirement_ids": case_requirement_ids(case, requirements),
                    "id": case.get("id", ""),
                    "title": case.get("title", ""),
                    "description": case.get("description", ""),
                    "priority": case.get("priority", ""),
                    "level": case.get("level", ""),
                    "actor": case.get("actor", ""),
                    "target": case.get("target", ""),
                    "preconditions": [str(item) for item in case.get("preconditions") or []],
                    "data_notes": case.get("data_notes", ""),
                    "steps": [str(step) for step in case.get("steps") or []],
                    "expected_result": case.get("expected_result", ""),
                    "links": [
                        f"{link.get('type', '')}:{link.get('id', '')}"
                        for link in case.get("traceability_links") or []
                        if isinstance(link, dict)
                    ],
                }
            )
    return rows


def render_case_table(rows: list[dict[str, Any]]) -> str:
    """Markdown: one six-column table per non-empty category, then a details list for the remaining fields."""
    out: list[str] = []
    for group in SECTIONS:
        cases = [row for row in rows if row["group"] == group]
        if not cases:
            continue
        out += [
            "",
            f"### {group.title()} cases",
            "",
            "| " + " | ".join(CASE_COLUMNS) + " |",
            "|" + "---|" * 6,
        ]
        for row in cases:
            title = md_cell(row["title"])
            description = row["description"]
            if description and description != row["title"]:
                title = f"{title} — {md_cell(description)}"
            steps = " ".join(f"{i}) {step}" for i, step in enumerate(row["steps"], 1))
            out.append(
                f"| {row['sno']} | {md_cell(', '.join(row['requirement_ids'])) or '—'} | {md_cell(row['id'])} | {title} "
                f"| {md_cell(steps) or '—'} | {md_cell(row['expected_result'])} |"
            )
        out += ["", f"**{group.title()} case details**", ""]
        for row in cases:
            out.append(
                f"- {_flat(row['id'])}: priority {_flat(row['priority']) or '—'}, level {_flat(row['level']) or '—'}, "
                f"actor {_flat(row['actor']) or '—'}, target {_flat(row['target']) or '—'}. "
                f"Preconditions: {'; '.join(_flat(item) for item in row['preconditions']) or 'none'}. "
                f"Data: {_flat(row['data_notes']) or 'none'}. "
                f"Links: {', '.join(_flat(link) for link in row['links']) or 'none'}."
            )
    return "\n".join(out).lstrip("\n") or "_None._"


def verdict_for(execution: dict[str, Any] | None) -> str:
    status = (execution or {}).get("status", "not_run")
    return {"passed": "PASS", "passed_with_skips": "PASS_WITH_SKIPS", "failed": "FAIL"}.get(
        status, "INCONCLUSIVE"
    )


GATE_LABELS = {"plan": "test planning", "design": "test design", "test": "generated tests"}


def _score_cell(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "confidence": record["confidence"],
        "threshold": record["threshold"],
        "accepted": record["accepted"],
        "verdict": record["verdict"],
        "round": record["round"],
        "line": record["line"],
        "link": f"{ledger.LEDGER}#L{record['line']}",
    }


def confidence_summary(
    records: list[dict[str, Any]], cases: list[dict[str, Any]], controls: list[dict]
) -> dict:
    """Gates per kind (threshold, hard or advisory, accepted subjects) and, per case, the latest plan, design and test
    score with a link to its ledger line. Only the latest epoch counts (a rerun-from-step starts a new one), so stale
    scores of removed cases are not shown. Runs without a ledger get empty gates and no rows."""
    records = ledger.in_epoch(records)
    latest = ledger.latest_by_subject(records)
    gates: dict[str, Any] = {}
    for kind in GATE_LABELS:
        rows = [r for r in records if r["kind"] == kind]
        final = [latest[key] for key in latest if key[0] == kind]
        gates[kind] = {
            "threshold": rows[-1]["threshold"] if rows else None,
            "gate": rows[-1]["gate"] if rows else None,
            "subjects": len(final),
            "accepted": sum(1 for r in final if r["accepted"]),
            "lowest_confidence": min((r["confidence"] for r in final), default=None),
        }
    area_of_control = {c["control_id"]: c.get("plan_area_id") or "" for c in controls}
    rows = []
    for case in (
        sorted(cases, key=lambda c: c["id"]) if records else []
    ):  # no ledger: no rows, the section is omitted
        control_ids = list(case.get("control_ids") or [])
        area = next((area_of_control[c] for c in control_ids if area_of_control.get(c)), "")
        rows.append(
            {
                "case_id": case["id"],
                "control_ids": control_ids,
                "plan_area_id": area,
                "plan": _score_cell(latest.get(("plan", area)) if area else None),
                "design": _score_cell(latest.get(("design", case["id"]))),
                "test": _score_cell(latest.get(("test", case["id"]))),
            }
        )
    return {"ledger": ledger.LEDGER, "records": len(records), "gates": gates, "rows": rows}


def build_qa_report(
    *,
    repo: dict[str, Any],
    requirements: dict[str, Any],
    design: dict[str, Any],
    generation: dict[str, Any],
    execution: dict[str, Any] | None,
    provenance: dict[str, Any] | None = None,
    stack: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
    generated_at: str | None = None,
    plan: dict[str, Any] | None = None,
    confidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    files = generation.get("files", [])
    controls = plan_controls(requirements, plan)
    reviews = {r["case_id"]: r for r in generation.get("case_reviews", [])}
    file_for_case = {cid: f["path"] for f in files for cid in f.get("case_ids", [])}
    failure_for_case = {
        cid: f.get("last_error", "")
        for f in generation.get("generation_failures", [])
        for cid in f["case_ids"]
    }
    generation_failed = set(failure_for_case)
    execution_cases = (execution or {}).get("cases", [])
    results = map_results_to_cases(
        [c for c in execution_cases if c.get("scope", "generated") == "generated"], files
    )
    result_for_case = {}
    severity = {"passed": 0, "skipped": 1, "not_executed": 2, "failed": 3, "error": 4}
    for result in results:
        cid = result.get("case_id")
        if cid and (
            cid not in result_for_case
            or severity.get(result["status"], 4) > severity.get(result_for_case[cid]["status"], 4)
        ):
            result_for_case[cid] = result
    cases = []
    diagnostics = (execution or {}).get("diagnostics", [])
    quarantined = set((execution or {}).get("quarantined", []))
    for case in design_cases(design):
        result = result_for_case.get(case["id"])
        message = (result or {}).get("message", "")
        if not result and (diagnostics or file_for_case.get(case["id"]) in quarantined):
            stem = (file_for_case.get(case["id"]) or "").rsplit("/", 1)[-1]
            own = [ln for ln in diagnostics if stem and stem in ln]
            message = "\n".join((own or diagnostics)[:8])[:900]
        if not result and case["id"] in generation_failed and not file_for_case.get(case["id"]):
            message = f"Test generation failed: {failure_for_case[case['id']]}"[:900]
        cases.append(
            {
                "id": case["id"],
                "title": case["title"],
                "type": case.get("type"),
                "level": case.get("level", ""),
                "actor": case.get("actor", ""),
                "priority": case.get("priority"),
                "confidence": reviews.get(case["id"], {}).get("confidence"),
                "reviewer_reason": reviews.get(case["id"], {}).get("reason"),
                "model_score": reviews.get(case["id"], {}).get("model_score"),
                "scores": reviews.get(case["id"], {}).get("scores"),
                "caps": reviews.get(case["id"], {}).get("caps", []),
                "file": file_for_case.get(case["id"]),
                "status": (
                    result["status"]
                    if result
                    else "generation_failed"
                    if case["id"] in generation_failed and not file_for_case.get(case["id"])
                    else "error"
                    if file_for_case.get(case["id"]) in quarantined
                    else "not_executed"
                    if execution
                    else "not_run"
                ),
                "message": message,
                "requirements": [
                    link["id"] for link in case.get("traceability_links", []) if link["type"] == "requirement"
                ],
                "control_ids": case_controls(case, controls),
                # Design-case oracle fields are read defensively: runs made before they existed carry none.
                "tier": case.get("tier"),
                "oracle": case.get("oracle"),
                "oracle_quote": documented_quote(case),
            }
        )
        threshold = (generation.get("verification_summary") or {}).get(
            "threshold", TEST_REVIEW_MIN_CONFIDENCE
        )
        review = reviews.get(case["id"])
        source_review = (
            "not_reviewed"
            if review is None
            else "accepted"
            if review_accepted(review, threshold)
            else "rejected"
        )
        row = cases[-1]
        row["source_review"] = source_review
        row["review_miss"] = (
            source_review == "accepted"
            and row["status"] in {"failed", "error"}
            and bool(_ASSERTION_MISMATCH.search(row["message"] or ""))
        )
        row["verified"] = is_verified(source_review, row["status"])
    adapter = str((execution or {}).get("adapter") or "")
    triage_rows = triage_cases(cases, adapter.split("-")[0] or None)
    triage_by_case = {r["case_id"]: r for r in triage_rows}
    for row in cases:
        record = triage_by_case.get(row["id"])
        row["triage"] = (
            {"classification": record["classification"], "reason": record["reason"]} if record else None
        )
    cases.sort(key=lambda c: (-(c.get("confidence") or 0), c["id"]))
    test_cases = case_table_rows(design, requirements)
    by_id = {c["id"]: c for c in cases}
    coverage = []
    for req in requirements.get("requirement_model", {}).get("requirements", []):
        linked = [
            by_id[i]
            for i in design.get("traceability_matrix", {}).get("requirement_to_tests", {}).get(req["id"], [])
            if i in by_id
        ]
        statuses = {c["status"] for c in linked}
        state = (
            "untested"
            if not linked
            else "failing"
            if statuses & {"failed", "error"}
            else "passed"
            if statuses == {"passed"}
            else "generation_failed"
            if statuses == {"generation_failed"}
            else "skipped"
            if statuses == {"skipped"}
            else "not_executed"
        )
        rid = req["id"]
        gaps = design.get("gaps_report", {})
        req_state = (
            "new"
            if linked
            else "pre_existing_witness"
            if rid in gaps.get("pre_existing_test_witnesses", {})
            else "excluded"
            if rid in gaps.get("excluded_requirements", [])
            else "unexplained"
        )
        coverage.append(
            {
                "requirement_state": req_state,
                "id": req["id"],
                "text": req["text"],
                "category": req.get("category", ""),
                "actor": req.get("actor", ""),
                "state": state,
                "cases": [c["id"] for c in linked],
            }
        )
    counts: dict[str, int] = {}
    for c in cases:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    status_counts: dict[str, int] = {}
    for row in coverage:
        status_counts[row["state"]] = status_counts.get(row["state"], 0) + 1
    unmapped = [r["name"] for r in results if not r.get("case_id")]
    req_sources = {
        r["id"]: r.get("source_files", [])
        for r in requirements.get("requirement_model", {}).get("requirements", [])
    }
    environment = {**(environment or {}), "diagnoses": (execution or {}).get("diagnoses", [])}
    updates = build_updates(cases, files, repo.get("source_files", []), coverage, req_sources, environment)
    verdict = verdict_for(execution)
    if counts.get("failed") or counts.get("error"):
        verdict = "FAIL"
    elif verdict.startswith("PASS") and (
        not cases
        or counts.get("not_executed")
        or counts.get("not_run")
        or counts.get("generation_failed")
        or unmapped
    ):
        verdict = "INCONCLUSIVE"
    elif verdict == "PASS" and counts.get("skipped"):
        verdict = "PASS_WITH_SKIPS"
    generated_verdict = (
        "FAIL"
        if counts.get("failed")
        or counts.get("error")
        or any(
            step.get("kind") == "test"
            and step.get("scope", "generated") == "generated"
            and step.get("status") not in {"passed", "command_succeeded"}
            for step in (execution or {}).get("steps", [])
        )
        else "INCONCLUSIVE"
        if not cases
        or counts.get("not_executed")
        or counts.get("not_run")
        or counts.get("generation_failed")
        or unmapped
        else "PASS_WITH_SKIPS"
        if counts.get("skipped")
        else "PASS"
    )
    baseline = []
    for scope, expected in (execution or {}).get("scope_expectations", {}).items():
        observed = [c for c in execution_cases if c.get("scope") == scope]
        steps = [s for s in (execution or {}).get("steps", []) if s.get("scope") == scope]
        observed_classes = {c.get("classname", "").rsplit(".", 1)[-1].split("$")[0] for c in observed}
        missing_classes = sorted(set(expected.get("classes", [])) - observed_classes)
        status = (
            "FAIL"
            if any(c.get("status") in {"failed", "error"} for c in observed)
            or any(s.get("status") in {"failed", "error", "timeout"} for s in steps)
            else "PASS"
            if observed and not missing_classes and all(c.get("status") == "passed" for c in observed)
            else "NOT_APPLICABLE"
            if expected.get("status") == "not_applicable"
            else "INCONCLUSIVE"
        )
        baseline.append(
            {
                "scope": scope,
                "verdict": status,
                "tests": len(observed),
                "classes": expected.get("classes", []),
                "missing_classes": missing_classes,
                "reason": expected.get("reason", "")
                + (f" No fresh test evidence for: {', '.join(missing_classes)}." if missing_classes else ""),
                "cases": observed,
            }
        )
    if any(row["verdict"] == "FAIL" for row in baseline):
        verdict = "FAIL"
    elif verdict.startswith("PASS") and (
        any(row["verdict"] == "INCONCLUSIVE" for row in baseline)
        or any(row["state"] == "untested" and row["requirement_state"] == "unexplained" for row in coverage)
    ):
        verdict = "INCONCLUSIVE"
    source_files = set(repo.get("source_files", []))
    represented_files = {p for sources in req_sources.values() for p in sources}
    traceability = trace_rows(controls, cases, generation, environment, design)
    return {
        "generated_at": generated_at,
        "repository": {
            "url": repo.get("repo_url"),
            "commit": repo.get("commit_sha"),
            "name": repo.get("repo_name"),
        },
        "verdict": verdict,
        "generated_verdict": generated_verdict,
        "verification": generation.get("verification_summary"),
        "review_vs_execution": review_vs_execution(cases),
        "verification_counts": {
            "review_accepted": sum(c["source_review"] == "accepted" for c in cases),
            "execution_passed": sum(c["status"] == "passed" for c in cases),
            "verified": sum(bool(c.get("verified")) for c in cases),
        },
        "triage": triage_rows,
        "triage_summary": summarize_triage(triage_rows),
        "case_verification": {
            row["case_id"]: {
                "source_review": row["source_review"],
                "confidence": row["confidence"],
                "execution": row["execution"],
                "review_miss": row["review_miss"],
                "verified": is_verified(row["source_review"], row["execution"]),
            }
            for row in review_vs_execution(cases)
        },
        "baseline_regression": baseline,
        "source_intake": {
            **requirements.get("intake", {}),
            "inventory_source_files": len(source_files),
            "requirement_source_files": len(represented_files & source_files),
            "scope": "Discovered static declarations, not complete business requirements or runtime coverage.",
        },
        "runtime_coverage": {"line": "not_measured", "branch": "not_measured", "mutation": "not_measured"},
        "execution_status": (execution or {}).get("status", "not_run"),
        "adapter": (execution or {}).get("adapter"),
        "case_status_counts": counts,
        "cases": cases,
        "test_cases": test_cases,
        "requirement_coverage": coverage,
        "requirement_state_counts": {
            k: sum(c["requirement_state"] == k for c in coverage)
            for k in ("new", "pre_existing_witness", "excluded", "unexplained")
        },
        "coverage_scope": "Requirement-to-test traceability; not measured line, branch or behavior coverage.",
        "traceability": traceability,
        "traceability_summary": summarize_trace(traceability),
        "confidence": confidence_summary(confidence or [], cases, controls),
        "requirement_status_counts": dict(sorted(status_counts.items())),
        "failures": [
            c
            for c in cases
            if c["status"] in {"failed", "error", "generation_failed"}
            or (c["status"] == "not_executed" and c["message"])
        ],
        "unmapped_results": unmapped,
        "updates": updates,
        "stack": stack or {},
        "provenance": provenance or {},
        "warnings": list((execution or {}).get("warnings", [])),
        "limitations": [
            "Local-process execution is not a security sandbox.",
            "A passed requirement means its linked generated tests passed execution; it is not proof of correctness.",
            "Source-review acceptance is a static judgement of the expectation; it is not execution evidence.",
            "Test design and source were authored by a model under human review; static validation checks structure, "
            "not semantic completeness.",
        ],
    }


def _updates_markdown(updates: dict[str, Any]) -> list[str]:
    out = ["", "## Files to update", ""]
    if not any(updates.values()):
        return out + [
            "Nothing to update: every executed case passed and every requirement has a passing test."
        ]
    if updates["generated_test_files_to_update"]:
        out += [
            "### Generated test files",
            "",
            "| File | Line(s) | Case | Class | What to change |",
            "|---|---|---|---|---|",
        ]
        for u in updates["generated_test_files_to_update"]:
            lines = ", ".join(map(str, u["lines"])) or "whole file"
            out.append(
                f"| `{u['file']}` | {lines} | {u['case'][:60]} | {u['classification']} | {u['detail'][:100]} — {u['action']} |"
            )
    if updates["repository_files_to_review"]:
        out += [
            "",
            "### Repository source files to review",
            "",
            "| File:line | Case | Class | Evidence |",
            "|---|---|---|---|",
        ]
        for u in updates["repository_files_to_review"]:
            out.append(
                f"| `{u['file']}:{u['line']}` | {u['case'][:60]} | {u['classification']} | {u['detail'][:110]} |"
            )
    if updates["requirements_needing_tests"]:
        out += [
            "",
            "### Requirements that still need a passing test",
            "",
            "| Requirement | State | Cover these files |",
            "|---|---|---|",
        ]
        for u in updates["requirements_needing_tests"]:
            out.append(
                f"| {u['requirement'][:90]} | {u['state']} | {', '.join(f'`{f}`' for f in u['source_files_to_cover'][:4])} |"
            )
    for u in updates["environment_actions"]:
        out.append(f"- **Environment:** {u['action']}")
    return out


def _traceability_markdown(report: dict[str, Any]) -> list[str]:
    rows = report.get("traceability") or []
    if not rows:
        return []
    summary = report.get("traceability_summary") or {}
    out = [
        "## End-to-end traceability",
        "",
        "Each control follows one requirement from planning (PLN) through design, generation, environment setup "
        "and execution. Failed at names the first step that did not pass.",
        "",
        summary_text(summary or summarize_trace(rows)),
        "",
        "| Control | Requirement | Plan area | Test case(s) | Test file(s) | Generation | Execution | Status | Failed at | Reason |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    out += [
        f"| {md_cell(r['control_id'])} | {md_cell(r['requirement_id'])} "
        f"| {md_cell(r['plan_area_id'])} {md_cell(r['plan_area_name'], 40)} "
        f"| {md_cell(', '.join(r['case_ids']), 120)} | {md_cell(', '.join(r['test_files']), 120)} "
        f"| {r['generation']} | {r['execution']} | {r['final_status']} | {r['failed_at'] or '-'} "
        f"| {md_cell(r['reason'], 300)} |"
        for r in rows
    ]
    return [*out, ""]


def review_vs_execution(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per case: what the source review accepted and what execution then showed (review misses flagged)."""
    return [
        {
            "case_id": c["id"],
            "source_review": c.get("source_review", "not_reviewed"),
            "confidence": c.get("confidence"),
            "execution": c["status"],
            "review_miss": bool(c.get("review_miss")),
        }
        for c in sorted(cases, key=lambda c: c["id"])
    ]


def confidence_gates_text(report: dict[str, Any]) -> str:
    """Names the three confidence gates with thresholds: planning and design from the ledger, tests from generation."""
    gates = (report.get("confidence") or {}).get("gates") or {}
    verification = report.get("verification") or {}
    parts = []
    for kind, label in GATE_LABELS.items():
        if kind == "test" and verification:
            mode = "hard" if verification.get("enforced") else "advisory"
            parts.append(
                f"{label} >= {verification.get('threshold')} ({verification.get('verified', 0)}/"
                f"{verification.get('total', 0)} accepted, {mode} gate)"
            )
            continue
        gate = gates.get(kind) or {}
        if gate.get("threshold") is None and kind == "design" and not design_review_enabled():
            parts.append(f"{label}: design review disabled (HARNESS_DESIGN_REVIEW=0)")
        elif gate.get("threshold") is None:
            parts.append(f"{label}: not recorded (run predates the confidence ledger)")
        else:
            parts.append(
                f"{label} >= {gate['threshold']} ({gate['accepted']}/{gate['subjects']} accepted, {gate['gate']} gate)"
            )
    return "Confidence gates: " + "; ".join(parts) + "."


def _verification_markdown(report: dict[str, Any]) -> list[str]:
    summary = report.get("verification") or {}
    if not summary:
        return [confidence_gates_text(report), ""]
    lowest = "n/a" if summary.get("min_confidence") is None else summary["min_confidence"]
    basis = "full-accuracy gate" if summary.get("enforced") else "fail-soft (HARNESS_REQUIRE_FULL_ACCURACY=0)"
    misses = sum(row["review_miss"] for row in report.get("review_vs_execution", []))
    return [
        f"Source review: {summary.get('verified', 0)}/{summary.get('total', 0)} cases source-review accepted "
        f"(threshold {summary.get('threshold')}, lowest accepted confidence {lowest}; {basis}). "
        f"Review misses (accepted, then failed an assertion): {misses}. Execution is reported separately below.",
        "",
        confidence_gates_text(report),
        "",
    ]


def _confidence_markdown(report: dict[str, Any]) -> list[str]:
    section = report.get("confidence") or {}
    rows = section.get("rows") or []
    if not rows:
        return []

    def cell(score):
        if score is None:
            return "-"
        mark = "accepted" if score["accepted"] else "not accepted"
        return f"{score['confidence']} ({mark}) [L{score['line']}]({score['link']})"

    return [
        "## Confidence traceability",
        "",
        "Scores are host-computed: the reviewer's rubric points, capped by deterministic evidence rules. The latest score "
        f"per kind is shown; every review pass is in `{section.get('ledger', ledger.LEDGER)}` (line links).",
        "",
        "| Case | Control(s) | Plan area | Plan | Design | Test |",
        "|---|---|---|---|---|---|",
        *[
            f"| {md_cell(r['case_id'])} | {md_cell(', '.join(r['control_ids']))} | {md_cell(r['plan_area_id'])} "
            f"| {cell(r['plan'])} | {cell(r['design'])} | {cell(r['test'])} |"
            for r in rows
        ],
        "",
    ]


def _review_markdown(report: dict[str, Any]) -> list[str]:
    rows = report.get("review_vs_execution") or []
    if not rows:
        return []
    lines = [
        "## Review vs execution",
        "",
        "Source review is a static judgement of the expectation against the implementation. Execution is the run result.",
        "",
        "| Case | Source review | Confidence | Execution | Review miss |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {md_cell(r['case_id'])} | {r['source_review']} | "
        f"{'' if r['confidence'] is None else r['confidence']} | {r['execution']} | "
        f"{'yes' if r['review_miss'] else 'no'} |"
        for r in rows
    ]
    return [*lines, ""]


def render_markdown(report: dict[str, Any]) -> str:
    repo = report["repository"]
    lines = [
        f"# QA report — {repo.get('name') or repo.get('url')}",
        "",
        f"**Verdict: {report['verdict']}** · execution `{report['execution_status']}` · "
        f"adapter `{report.get('adapter') or 'n/a'}` · commit `{(repo.get('commit') or '')[:12]}`",
        "",
        "## Case results",
        "",
        "| ID | Control | Case | Level | Actor | Pri | Confidence | Status | File |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    lines[4:4] = [
        "## Evidence scope",
        "",
        f"Generated suite: **{report.get('generated_verdict', report['verdict'])}**. "
        "A suite verdict does not qualify the complete application.",
        "",
        "Line, branch and mutation coverage: **not measured**.",
        "",
        *_verification_markdown(report),
        "| Existing regression scope | Verdict | Tests | Reason |",
        "|---|---|---|---|",
        *[
            f"| {md_cell(r['scope'])} | {r['verdict']} | {r['tests']} | {md_cell(r['reason'])} |"
            for r in report.get("baseline_regression", [])
        ],
        "",
        *_traceability_markdown(report),
        *_confidence_markdown(report),
    ]
    for c in report["cases"]:
        lines.append(
            f"| {md_cell(c['id'])} | {md_cell(', '.join(c.get('control_ids') or []))} | {md_cell(c['title'], 70)} "
            f"| {md_cell(c['level'])} | {md_cell(c['actor'])} "
            f"| {md_cell(c['priority'])} | {'' if c.get('confidence') is None else c['confidence']} "
            f"| {c['status']} | {md_cell(c['file'])} |"
        )
    lines += [
        "",
        *_review_markdown(report),
        "## Test cases",
        "",
        render_case_table(report.get("test_cases") or []),
        "",
    ]
    lines += [
        "",
        "## Requirement traceability",
        "",
        "Linked generated tests are not measured line, branch or complete behavior coverage.",
        "",
        "| ID | State | Basis | Requirement |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {md_cell(r['id'])} | {r['state']} | {r['requirement_state']} | {md_cell(r['text'], 110)} |"
        for r in report["requirement_coverage"]
    ]
    lines += [
        "",
        "Requirement states (static traceability, not measured coverage): "
        + ", ".join(f"{k}={v}" for k, v in report["requirement_state_counts"].items()),
    ]
    if report["failures"]:
        lines += ["", "## Failures", ""]
        lines += [
            f"- **{c['title']}** (`{c['file'] or 'no file'}`, {c['status']})\n  ```\n  {c['message'][:600]}\n  ```"
            for c in report["failures"]
        ]
    checks = report.get("skill_checks", {})
    if checks:
        lines += ["", "## Skill contract checks", "", "| Skill | Check | Result |", "|---|---|---|"]
        for name, check in checks.items():
            verdict = "PASS" if check["passed"] else "FAIL"
            lines.append(f"| `{check['skill']}` | {name} | {verdict} — {check['output'][:100]} |")
    lines += _updates_markdown(report["updates"])
    if report["unmapped_results"]:
        lines += ["", "## Executed tests not mapped to an approved case", ""] + [
            f"- {n}" for n in report["unmapped_results"]
        ]
    lines += ["", "## Limitations", ""] + [f"- {item}" for item in report["limitations"]]
    return "\n".join(lines) + "\n"

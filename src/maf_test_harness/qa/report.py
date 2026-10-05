"""Deterministic QA report: requirement -> approved case -> generated test -> executed result.

No model is involved. A requirement is only "verified" when every linked case was executed and passed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..envs.runners import map_results_to_cases
from .triage import build_updates

SECTIONS = ("positive", "negative", "boundary", "validation", "contract")


def design_cases(design: dict[str, Any]) -> list[dict[str, Any]]:
    return [case for section in SECTIONS for case in design.get(f"{section}_tests", []) or []]


def verdict_for(execution: dict[str, Any] | None) -> str:
    status = (execution or {}).get("status", "not_run")
    return {"passed": "PASS", "passed_with_skips": "PASS_WITH_SKIPS", "failed": "FAIL"}.get(
        status, "INCONCLUSIVE"
    )


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
) -> dict[str, Any]:
    files = generation.get("files", [])
    file_for_case = {f["case_ids"][0]: f["path"] for f in files}
    results = map_results_to_cases((execution or {}).get("cases", []), files)
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
        cases.append(
            {
                "id": case["id"],
                "title": case["title"],
                "type": case.get("type"),
                "level": case.get("level", ""),
                "actor": case.get("actor", ""),
                "priority": case.get("priority"),
                "file": file_for_case.get(case["id"]),
                "status": (
                    result["status"]
                    if result
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
            }
        )
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
            else "verified"
            if statuses == {"passed"}
            else "not_executed"
        )
        coverage.append(
            {
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
        not cases or counts.get("not_executed") or counts.get("not_run") or unmapped
    ):
        verdict = "INCONCLUSIVE"
    elif verdict == "PASS" and counts.get("skipped"):
        verdict = "PASS_WITH_SKIPS"
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "repository": {
            "url": repo.get("repo_url"),
            "commit": repo.get("commit_sha"),
            "name": repo.get("repo_name"),
        },
        "verdict": verdict,
        "execution_status": (execution or {}).get("status", "not_run"),
        "adapter": (execution or {}).get("adapter"),
        "case_status_counts": counts,
        "cases": cases,
        "requirement_coverage": coverage,
        "coverage_scope": "Requirement-to-test traceability; not measured line, branch or behavior coverage.",
        "failures": [
            c
            for c in cases
            if c["status"] in {"failed", "error"} or (c["status"] == "not_executed" and c["message"])
        ],
        "unmapped_results": unmapped,
        "updates": updates,
        "stack": stack or {},
        "provenance": provenance or {},
        "warnings": list((execution or {}).get("warnings", [])),
        "limitations": [
            "Local-process execution is not a security sandbox.",
            "A verified requirement means its linked generated tests passed; it is not proof of correctness.",
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
        "| Case | Level | Actor | Pri | Status | File |",
        "|---|---|---|---|---|---|",
    ]
    for c in report["cases"]:
        lines.append(
            f"| {c['title'][:70]} | {c['level']} | {c['actor']} | {c['priority']} | {c['status']} | {c['file'] or ''} |"
        )
    lines += [
        "",
        "## Requirement traceability",
        "",
        "Linked generated tests are not measured line, branch or complete behavior coverage.",
        "",
        "| State | Requirement |",
        "|---|---|",
    ]
    lines += [f"| {r['state']} | {r['text'][:110]} |" for r in report["requirement_coverage"]]
    if report["failures"]:
        lines += ["", "## Failures", ""]
        lines += [
            f"- **{c['title']}** (`{c['file']}`)\n  ```\n  {c['message'][:600]}\n  ```"
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

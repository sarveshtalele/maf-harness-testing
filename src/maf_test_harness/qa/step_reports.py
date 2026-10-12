"""Deterministic per-step Markdown reports filled from saved artifacts (no model, no clock, no randomness).

Each workflow step has one template in ``qa/templates``. A step that has no artifact renders a "Not run" report;
nothing here raises for missing or partial data. Validation checks are computed from the artifacts themselves.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from string import Template
from typing import Any

from .report import SECTIONS, case_table_rows, design_cases, md_cell, render_case_table

TEMPLATES = Path(__file__).with_name("templates")
NOT_RUN = "_Not run: this step produced no artifact in this run._"
# report name -> workflow stage whose artifact feeds it
STEPS: dict[str, str] = {
    "00-summary": "",
    "01-repository-analysis": "repo_analysis",
    "02-requirements": "requirements",
    "03-test-plan": "test_planning",
    "04-test-design": "test_design",
    "05-test-generation": "test_generation",
    "06-environment-setup": "environment_setup",
    "07-test-execution": "test_execution",
    "08-qa-verdict": "test_execution",
}
Check = tuple[str, bool, str]


def run_completed_at(run_dir: Path) -> str | None:
    """Completion time recorded in run evidence: the newest ``at`` stamp in the audit log (None if none)."""
    stamps: list[str] = []
    try:
        for line in (Path(run_dir) / "audit.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                stamp = json.loads(line).get("at")
            except (ValueError, AttributeError):
                continue
            if isinstance(stamp, str):
                stamps.append(stamp)
    except OSError:
        return None
    return max(stamps) if stamps else None


def _table(headers: list[str], rows: list[list[Any]], empty: str = "_None._") -> str:
    if not rows:
        return empty
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(md_cell(c, 160) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _bullets(items: list[Any], empty: str = "_None._") -> str:
    return "\n".join(f"- {md_cell(i, 400)}" for i in items) if items else empty


def _checks(checks: list[Check]) -> str:
    return _table(["Check", "Result", "Detail"], [[n, "PASS" if ok else "FAIL", d] for n, ok, d in checks])


def _kv(pairs: list[tuple[str, Any]]) -> str:
    return _table(["Item", "Value"], [[k, v] for k, v in pairs])


def _dup(values: list[str]) -> list[str]:
    return sorted({v for v in values if values.count(v) > 1})


# ---- per-step builders: artifact -> (sections, checks) ----------------------------------------------------------


def _repo(a: dict, ctx: dict) -> tuple[dict, list[Check]]:
    langs = a.get("languages") or {}
    sections = {
        "inputs": _kv([("Repository", a.get("repo_url")), ("Commit", a.get("commit_sha"))]),
        "results": _kv(
            [
                ("Files", a.get("file_count", 0)),
                ("Source files", a.get("source_file_count", len(a.get("source_files") or []))),
                ("Languages", ", ".join(f"{k} ({v})" for k, v in sorted(langs.items())) or "none detected"),
                ("Frameworks", ", ".join(sorted(a.get("frameworks") or [])) or "none detected"),
                ("Build files", ", ".join(sorted(a.get("package_files") or [])) or "none"),
                ("Existing test files", len(a.get("test_files") or [])),
                ("Existing test frameworks", ", ".join(sorted(a.get("test_frameworks") or [])) or "none"),
            ]
        ),
    }
    listed = len(a.get("source_files") or [])
    checks = [
        ("Repository identified", bool(a.get("repo_name") and a.get("repo_url")), a.get("repo_name") or ""),
        ("Commit recorded", bool(a.get("commit_sha")), (a.get("commit_sha") or "missing")[:12]),
        ("Source files inventoried", listed > 0, f"{listed} files"),
        (
            "Source file count matches inventory",
            a.get("source_file_count", listed) == listed,
            f"declared {a.get('source_file_count', listed)}, listed {listed}",
        ),
    ]
    return sections, checks


def _reqs(a: dict, ctx: dict) -> tuple[dict, list[Check]]:
    model = a.get("requirement_model") or {}
    reqs = sorted(model.get("requirements") or [], key=lambda r: str(r.get("id")))
    criteria = model.get("acceptance_criteria") or []
    sections = {
        "inputs": _kv([("Repository", ctx["repo"]), ("Source files in scope", ctx["source_files"])]),
        "results": f"{len(reqs)} requirements, {len(criteria)} acceptance criteria.\n\n"
        + _table(
            ["ID", "Category", "Actor", "Requirement", "Evidence"],
            [
                [
                    r.get("id"),
                    r.get("category"),
                    r.get("actor"),
                    r.get("text"),
                    ", ".join(r.get("source_files") or []) or r.get("evidence_file") or "none",
                ]
                for r in reqs
            ],
        ),
        "extra": "**Gaps**\n\n"
        + _bullets(a.get("gaps") or [])
        + "\n\n**Assumptions**\n\n"
        + _bullets(a.get("assumptions") or [])
        + "\n\n**Exclusions**\n\n"
        + _bullets(a.get("exclusions") or []),
    }
    ids = [str(r.get("id")) for r in reqs]
    parents = {c.get("requirement_id") for c in criteria}
    checks = [
        ("At least one requirement", bool(reqs), f"{len(reqs)} requirements"),
        ("Requirement IDs unique", not _dup(ids), ", ".join(_dup(ids)) or "all unique"),
        (
            "Every requirement has source evidence",
            all(r.get("source_files") or r.get("evidence_file") for r in reqs),
            ", ".join(str(r.get("id")) for r in reqs if not (r.get("source_files") or r.get("evidence_file")))
            or "all evidenced",
        ),
        (
            "Acceptance criteria reference known requirements",
            parents <= set(ids),
            ", ".join(sorted(str(p) for p in parents - set(ids))) or "all resolved",
        ),
    ]
    return sections, checks


def _plan(a: dict, ctx: dict) -> tuple[dict, list[Check]]:
    areas = a.get("areas") or []
    sections = {
        "inputs": _kv([("Requirements", ctx["requirements"]), ("Objective", a.get("objective"))]),
        "results": _table(
            ["Area ID", "Area", "Level", "Risk", "Scenarios", "Evidence files"],
            [
                [
                    x.get("area_id") or "",
                    x.get("name"),
                    x.get("level"),
                    x.get("risk"),
                    len(x.get("scenarios") or []),
                    ", ".join(x.get("evidence_files") or []),
                ]
                for x in sorted(areas, key=lambda x: str(x.get("name")))
            ],
        )
        + "\n\n**Frameworks:** "
        + (", ".join(a.get("recommended_frameworks") or []) or "none")
        + "\n\n**Execution order**\n\n"
        + "\n".join(f"{i}. {md_cell(s, 300)}" for i, s in enumerate(a.get("execution_order") or [], 1)),
        "extra": "**Controls**\n\n"
        + _table(
            ["Control", "Requirement", "Plan area", "Level"],
            [
                [
                    c.get("control_id"),
                    c.get("requirement_id"),
                    f"{c.get('plan_area_id')} {c.get('plan_area_name')}",
                    c.get("level"),
                ]
                for c in a.get("controls") or []
            ],
            empty="_No controls._",
        )
        + "\n\n**Setup actions**\n\n"
        + _bullets(a.get("setup_actions") or [])
        + "\n\n**Assumptions**\n\n"
        + _bullets(a.get("assumptions") or [])
        + "\n\n**Gaps**\n\n"
        + _bullets(a.get("gaps") or []),
    }
    names = [str(x.get("name")) for x in areas]
    checks = [
        ("At least one test area", bool(areas), f"{len(areas)} areas"),
        ("Area names unique", not _dup(names), ", ".join(_dup(names)) or "all unique"),
        ("Every area has scenarios", all(x.get("scenarios") for x in areas), f"{len(areas)} areas checked"),
        (
            "Execution order defined",
            bool(a.get("execution_order")),
            f"{len(a.get('execution_order') or [])} steps",
        ),
    ]
    return sections, checks


def _design(a: dict, ctx: dict) -> tuple[dict, list[Check]]:
    cases = design_cases(a)
    ids = [str(c.get("id")) for c in cases]
    gaps = a.get("gaps_report") or {}
    witnesses = gaps.get("pre_existing_test_witnesses") or {}
    excluded = list(gaps.get("excluded_requirements") or [])
    uncovered = list(gaps.get("uncovered_requirements") or [])
    unexplained = sorted(set(uncovered) - set(witnesses) - set(excluded))
    reviews = {r.get("case_id"): r for r in a.get("design_reviews") or []}
    notes = [str(n) for n in a.get("host_notes") or []]
    retargets = [n for n in notes if n.startswith("retargeted")]
    other = [n for n in notes if not n.startswith("retargeted")]
    budget = ctx["budget"]
    confidences = [r["confidence"] for r in reviews.values() if isinstance(r.get("confidence"), int)]
    sections = {
        "inputs": _kv([("Requirements", ctx["requirements"]), ("Test plan areas", ctx["areas"])]),
        "test_cases": render_case_table(case_table_rows(a)),
        "results": _kv(
            [(s, len(a.get(f"{s}_tests") or [])) for s in SECTIONS] + [("Total cases", len(cases))]
        )
        + "\n\n"
        + _table(
            [
                "ID",
                "Control",
                "Title",
                "Type",
                "Level",
                "Pri",
                "Requirements",
                "Confidence",
                "Review verdict",
            ],
            [
                [
                    c.get("id"),
                    ", ".join(c.get("control_ids") or []),
                    c.get("title"),
                    c.get("type"),
                    c.get("level"),
                    c.get("priority"),
                    ", ".join(
                        sorted(
                            link["id"]
                            for link in c.get("traceability_links", [])
                            if link.get("type") == "requirement"
                        )
                    ),
                    reviews.get(c.get("id"), {}).get("confidence", c.get("confidence", "")),
                    reviews.get(c.get("id"), {}).get("verdict", c.get("reviewer_verdict", "not reviewed")),
                ]
                for c in sorted(cases, key=lambda c: str(c.get("id")))
            ],
        ),
        "review": (
            f"{len(reviews)} of {len(cases)} cases reviewed"
            + (f"; mean confidence {sum(confidences) / len(confidences):.1f}" if confidences else "")
            + ".\n\n"
            + _table(
                ["Case", "Verdict", "Confidence", "Reason", "Suggested target"],
                [
                    [
                        r.get("case_id"),
                        r.get("verdict"),
                        r.get("confidence"),
                        r.get("reason"),
                        r.get("suggested_target") or "",
                    ]
                    for r in sorted(reviews.values(), key=lambda r: str(r.get("case_id")))
                ],
            )
        ),
        "adjustments": "**Retargets**\n\n"
        + _bullets(retargets)
        + "\n\n**Case budget trims**\n\n"
        + _bullets(
            [
                f"{', '.join(map(str, t.get('case_ids') or []))}: {t.get('reason', '')}"
                for t in (
                    [gaps["budget_trimmed"]]
                    if isinstance(gaps.get("budget_trimmed"), dict)
                    else gaps.get("budget_trimmed") or []
                )
                if isinstance(t, dict) and t.get("case_ids")
            ]
        )
        + "\n\n**Other host notes**\n\n"
        + _bullets(other)
        + "\n\n**Model budget**\n\n"
        + (
            f"{budget.get('calls', 0)} model calls, {budget.get('reported_tokens', 0)} reported tokens, "
            f"charged {budget.get('charged', 0)}."
            if budget
            else "_No budget usage recorded._"
        ),
        "witnesses": _table(
            ["Requirement", "Existing test evidence"],
            [
                [
                    rid,
                    "; ".join(f"{w.get('test_file')}:{w.get('line')}" for w in ws if isinstance(w, dict))
                    or "present",
                ]
                for rid, ws in sorted(witnesses.items())
            ],
        ),
        "extra": "**Excluded requirements (with reason in design gaps)**\n\n"
        + _bullets(sorted(excluded))
        + "\n\n**Unexplained gaps**\n\n"
        + _bullets(unexplained)
        + "\n\n**Design notes**\n\n"
        + (md_cell(gaps.get("notes"), 800) or "_None._"),
    }
    reqs = ctx["requirement_ids"]
    linked = {
        i
        for i, tests in (a.get("traceability_matrix") or {}).get("requirement_to_tests", {}).items()
        if tests
    }
    accounted = linked | set(witnesses) | set(excluded)
    checks = [
        ("At least one test case", bool(cases), f"{len(cases)} cases"),
        ("Case IDs unique", not _dup(ids), ", ".join(_dup(ids)) or "all unique"),
        (
            "Every case traces to a requirement or criterion",
            all(c.get("traceability_links") for c in cases),
            ", ".join(str(c.get("id")) for c in cases if not c.get("traceability_links")) or "all traced",
        ),
        (
            "Every requirement accounted for (case, existing test or documented exclusion)",
            not reqs or set(reqs) <= accounted,
            ", ".join(sorted(set(reqs) - accounted)) or f"{len(reqs)} requirements accounted for",
        ),
        ("No unexplained gaps", not unexplained, ", ".join(unexplained) or "none"),
        (
            "Every case independently reviewed",
            all(str(c.get("id")) in reviews or c.get("reviewer_verdict") for c in cases),
            f"{len(reviews)} of {len(cases)} reviewed",
        ),
        (
            "No case rejected by the design review",
            not any(r.get("verdict") == "reject" for r in reviews.values()),
            ", ".join(sorted(str(i) for i, r in reviews.items() if r.get("verdict") == "reject"))
            or "none rejected",
        ),
    ]
    return sections, checks


def _generation(a: dict, ctx: dict) -> tuple[dict, list[Check]]:
    files = a.get("files") or []
    failures = a.get("generation_failures") or []
    failed = {c for f in failures for c in f.get("case_ids", [])}
    case_ids = ctx["case_ids"]
    per_case: dict[str, list[str]] = {}
    for f in files:
        for cid in f.get("case_ids", []):
            per_case.setdefault(cid, []).append(f.get("path"))
    reviews = {r.get("case_id"): r for r in a.get("case_reviews") or []}
    paths = [str(f.get("path")) for f in files]
    sections = {
        "inputs": _kv(
            [
                ("Approved design cases", len(case_ids)),
                ("Language / framework", f"{a.get('selected_language')} / {a.get('selected_framework')}"),
            ]
        ),
        "results": _kv(
            [
                ("Files generated", len(files)),
                ("Cases generated", len(per_case)),
                ("Generation failures", len(failed)),
            ]
        )
        + "\n\n"
        + _table(
            ["Case", "Control", "File", "Reviewer verdict", "Confidence"],
            [
                [
                    cid,
                    ctx["control_by_case"].get(cid, ""),
                    ", ".join(per_case.get(cid, [])) or ("generation failed" if cid in failed else "none"),
                    "generation_failed"
                    if cid in failed and cid not in per_case
                    else reviews.get(cid, {}).get("verdict", ""),
                    reviews.get(cid, {}).get("confidence", ""),
                ]
                for cid in sorted(case_ids)
            ],
        ),
        "failures": _table(
            ["Batch", "Cases", "Attempts", "Last error"],
            [
                [f.get("batch"), ", ".join(f.get("case_ids", [])), f.get("attempts"), f.get("last_error")]
                for f in failures
            ],
            empty="_No generation failures._",
        ),
    }
    unknown = sorted(set(per_case) - set(case_ids)) if case_ids else []
    multi = sorted(c for c, p in per_case.items() if len(p) != 1)
    missing = sorted(set(case_ids) - set(per_case) - failed)
    checks = [
        (
            "Every case has exactly one file or a recorded generation failure",
            not multi and not missing,
            ", ".join(multi + missing) or "all cases mapped",
        ),
        ("No file claims an unknown case", not unknown, ", ".join(unknown) or "none"),
        ("Generated file paths unique", not _dup(paths), ", ".join(_dup(paths)) or "all unique"),
        ("Every file has content", all(f.get("content") for f in files), f"{len(files)} files"),
        (
            "Static validation found no uncovered case",
            not (a.get("validation") or {}).get("uncovered_case_ids"),
            ", ".join((a.get("validation") or {}).get("uncovered_case_ids") or []) or "none",
        ),
        ("No generation failures", not failures, f"{len(failed)} cases failed"),
    ]
    return sections, checks


def _env(a: dict, ctx: dict) -> tuple[dict, list[Check]]:
    report = a.get("report") or {}
    steps = report.get("steps") or []
    missing = (ctx["state"].get("environment") or {}).get("missing_packages") or []
    sections = {
        "inputs": _kv([("Ecosystem", a.get("ecosystem")), ("Isolation", a.get("isolation"))]),
        "results": _kv(
            [
                ("Status", a.get("status") or report.get("status")),
                ("Toolchains", ", ".join(map(str, a.get("toolchains") or [])) or "none"),
            ]
        )
        + "\n\n"
        + _table(
            ["Step", "Status", "Seconds"],
            [[s.get("name"), s.get("status"), s.get("duration_seconds", "")] for s in steps],
            empty="_No install steps recorded._",
        )
        + "\n\n**Warnings**\n\n"
        + _bullets(sorted(map(str, report.get("warnings") or [])))
        + "\n\n**Notes**\n\n"
        + _bullets(a.get("notes") or []),
    }
    bad = [s.get("name") for s in steps if s.get("status") not in {"command_succeeded", "passed", "skipped"}]
    status = a.get("status") or report.get("status")
    checks = [
        ("Environment setup did not fail", status not in {"setup_failed", "failed"}, f"status {status}"),
        ("Every install step succeeded", not bad, ", ".join(map(str, bad)) or f"{len(steps)} steps"),
        ("No declared package left uninstalled", not missing, ", ".join(sorted(missing)) or "none"),
    ]
    return sections, checks


def _execution(a: dict, ctx: dict) -> tuple[dict, list[Check]]:
    qa = a.get("qa_report") or {}
    ex = a.get("execution") or {}
    cases = qa.get("cases") or []
    counts = qa.get("case_status_counts") or {}
    baseline = qa.get("baseline_regression") or []
    failures = qa.get("failures") or []
    sections = {
        "inputs": _kv(
            [
                ("Runner", qa.get("adapter") or ex.get("adapter") or "n/a"),
                ("Generated files", ctx["files"]),
                ("Execution status", qa.get("execution_status") or ex.get("status") or "not_run"),
            ]
        ),
        "results": _kv([(k, v) for k, v in sorted(counts.items())] + [("Total cases", len(cases))])
        + "\n\n"
        + _table(
            ["Case", "Control", "Title", "Status", "File"],
            [
                [
                    c.get("id"),
                    ", ".join(c.get("control_ids") or []),
                    c.get("title"),
                    c.get("status"),
                    c.get("file") or "",
                ]
                for c in sorted(cases, key=lambda c: str(c.get("id")))
            ],
        ),
        "failures": _table(
            ["Case", "Status", "Message"],
            [
                [c.get("id"), c.get("status"), (c.get("message") or "")[:500]]
                for c in sorted(failures, key=lambda c: str(c.get("id")))
            ],
            empty="_No failures._",
        ),
        "baseline": f"Generated suite: **{qa.get('generated_verdict', 'n/a')}**.\n\n"
        + _table(
            ["Scope", "Verdict", "Tests", "Reason"],
            [[b.get("scope"), b.get("verdict"), b.get("tests"), b.get("reason")] for b in baseline],
            empty="_No existing regression scope was planned._",
        ),
    }
    pending = sorted(c["id"] for c in cases if c.get("status") in {"not_run", "not_executed"})
    checks = [
        (
            "Execution status recorded",
            bool(qa.get("execution_status") or ex.get("status")),
            str(ex.get("status", "missing")),
        ),
        ("Every case has a result", not pending, ", ".join(pending) or f"{len(cases)} cases"),
        (
            "Status counts add up to the case total",
            sum(counts.values()) == len(cases),
            f"{sum(counts.values())} of {len(cases)}",
        ),
        (
            "No executed test is unmapped to an approved case",
            not qa.get("unmapped_results"),
            f"{len(qa.get('unmapped_results') or [])} unmapped",
        ),
        (
            "No failed or errored case",
            not (counts.get("failed") or counts.get("error")),
            f"{counts.get('failed', 0)} failed, {counts.get('error', 0)} errors",
        ),
        (
            "No generation-failed case",
            not counts.get("generation_failed"),
            f"{counts.get('generation_failed', 0)} cases",
        ),
    ]
    return sections, checks


def _verdict(a: dict, ctx: dict) -> tuple[dict, list[Check]]:
    qa = a.get("qa_report") or {}
    if not qa:
        return {}, []
    cov = qa.get("requirement_coverage") or []
    counts = qa.get("case_status_counts") or {}
    verdict = qa.get("verdict")
    unexplained = [r["id"] for r in cov if r.get("requirement_state") == "unexplained"]
    sections = {
        "inputs": _kv(
            [
                ("Execution status", qa.get("execution_status")),
                ("Generated suite verdict", qa.get("generated_verdict")),
            ]
        ),
        "results": _kv(
            [
                ("Verdict", verdict),
                ("Case status", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"),
                (
                    "Requirement basis",
                    ", ".join(
                        f"{k}={v}" for k, v in sorted((qa.get("requirement_state_counts") or {}).items())
                    ),
                ),
                (
                    "Requirement status",
                    ", ".join(
                        f"{k}={v}" for k, v in sorted((qa.get("requirement_status_counts") or {}).items())
                    ),
                ),
                ("Line / branch / mutation coverage", "not measured"),
            ]
        ),
        "coverage": "Static traceability only; not measured coverage.\n\n"
        + _table(
            ["Requirement", "Basis", "State", "Cases", "Text"],
            [
                [
                    r.get("id"),
                    r.get("requirement_state"),
                    r.get("state"),
                    ", ".join(r.get("cases") or []),
                    r.get("text"),
                ]
                for r in sorted(cov, key=lambda r: str(r.get("id")))
            ],
        ),
    }
    bad = bool(counts.get("failed") or counts.get("error"))
    checks = [
        (
            "Verdict is a known value",
            verdict in {"PASS", "PASS_WITH_SKIPS", "FAIL", "INCONCLUSIVE"},
            str(verdict),
        ),
        (
            "PASS is never reported with failed or errored cases",
            not (bad and str(verdict).startswith("PASS")),
            f"verdict {verdict}",
        ),
        (
            "PASS is never reported with unexecuted or generation-failed cases",
            not (
                str(verdict).startswith("PASS")
                and (counts.get("not_executed") or counts.get("not_run") or counts.get("generation_failed"))
            ),
            f"verdict {verdict}",
        ),
        (
            "Every requirement has a coverage state",
            all(r.get("state") for r in cov),
            f"{len(cov)} requirements",
        ),
        ("No unexplained requirement gaps", not unexplained, ", ".join(unexplained) or "none"),
        (
            "Runtime coverage disclosed as not measured",
            (qa.get("runtime_coverage") or {}).get("line") == "not_measured",
            "static traceability only",
        ),
    ]
    return sections, checks


def _artifact(state: dict, stage: str) -> dict | None:
    arts = state.get("artifacts") or {}
    value = arts.get(stage)
    if value is None and stage == "environment_setup":
        env = state.get("environment")
        return {"status": env.get("status"), **(env.get("plan") or {})} if env else None
    if stage == "test_execution" and isinstance(value, dict) and "qa_report" not in value:
        return None
    return value if isinstance(value, dict) else None


def _context(run_dir: Path, state: dict) -> dict:
    arts = state.get("artifacts") or {}
    repo = arts.get("repo_analysis") or {}
    reqs = (arts.get("requirements") or {}).get("requirement_model", {}).get("requirements", [])
    design = arts.get("test_design") or {}
    cases = design_cases(design)
    return {
        "state": state,
        "repo": repo.get("repo_url") or state.get("repo_url") or "",
        "source_files": len(repo.get("source_files") or []),
        "requirements": len(reqs),
        "requirement_ids": [str(r.get("id")) for r in reqs],
        "areas": len((arts.get("test_planning") or {}).get("areas") or []),
        "case_ids": [str(c.get("id")) for c in cases],
        "control_by_case": {str(c.get("id")): ", ".join(c.get("control_ids") or []) for c in cases},
        "files": len((arts.get("test_generation") or {}).get("files") or []),
        "budget": state.get("budget") or {},
    }


def _meta(run_dir: Path, state: dict, name: str, status: str) -> str:
    stage = STEPS[name]
    models = state.get("stage_models") or {}
    if stage:
        model = models.get(stage) or "none (deterministic host step)"
    else:
        model = ", ".join(sorted({str(m) for m in models.values()})) or "none recorded"
    arts = state.get("artifacts") or {}
    repo = arts.get("repo_analysis") or {}
    commit = (repo.get("commit_sha") or "")[:12]
    ref = state.get("repo_ref") or "default branch"
    rows = [("Run ID", Path(run_dir).name)]
    if state.get("run_name"):
        rows.append(("Run name", state["run_name"]))
    rows += [
        ("Repository", state.get("repo_url") or repo.get("repo_url") or "unknown"),
        ("Ref", f"{ref} ({commit})" if commit else ref),
        ("Model", model),
        ("Generated at", run_completed_at(run_dir) or "not recorded"),
        ("Step status", status),
    ]
    return _kv(rows)


def _summary(run_dir: Path, state: dict, results: dict[str, tuple[str, list[Check]]]) -> dict:
    qa = ((state.get("artifacts") or {}).get("test_execution") or {}).get("qa_report") or {}
    rows = [
        [name, status, f"{sum(ok for _, ok, _ in checks)}/{len(checks)}" if checks else "n/a"]
        for name, (status, checks) in results.items()
        if name != "00-summary"
    ]
    failed = [f"{name}: {n}" for name, (_, checks) in results.items() for n, ok, _ in checks if not ok]
    ran = sum(s == "Completed" for n, (s, _) in results.items() if n != "00-summary")
    return {
        "inputs": _kv([("Workflow steps", len(rows)), ("Repository", state.get("repo_url") or "unknown")]),
        "results": _kv(
            [
                ("QA verdict", qa.get("verdict", "not available")),
                ("Generated suite verdict", qa.get("generated_verdict", "not available")),
                ("Steps completed", f"{ran} of {len(rows)}"),
            ]
        )
        + "\n\n"
        + _table(["Report", "Status", "Checks passed"], rows),
        "checks": None,
        "_failed": failed,
    }


FILLERS: dict[str, tuple[Callable[[dict, dict], tuple[dict, list[Check]]], str]] = {
    "01-repository-analysis": (_repo, "repo_analysis"),
    "02-requirements": (_reqs, "requirements"),
    "03-test-plan": (_plan, "test_planning"),
    "04-test-design": (_design, "test_design"),
    "05-test-generation": (_generation, "test_generation"),
    "06-environment-setup": (_env, "environment_setup"),
    "07-test-execution": (_execution, "test_execution"),
    "08-qa-verdict": (_verdict, "test_execution"),
}


class _Default(dict):
    def __missing__(self, key: str) -> str:
        return NOT_RUN


def _render(name: str, values: dict) -> str:
    template = Template((TEMPLATES / f"{name}.md").read_text(encoding="utf-8"))
    return template.safe_substitute(_Default(values)).rstrip("\n") + "\n"


def build_step_reports(run_dir: Path, state: dict) -> dict[str, str]:
    """``{"00-summary": markdown, ..., "08-qa-verdict": markdown}`` in workflow order; never raises on missing data."""
    run_dir = Path(run_dir)
    ctx = _context(run_dir, state)
    results: dict[str, tuple[str, list[Check]]] = {}
    sections: dict[str, dict] = {}
    for name, (fill, stage) in FILLERS.items():
        artifact = _artifact(state, stage)
        try:
            values, checks = fill(artifact, ctx) if artifact else ({}, [])
        except Exception as exc:  # a malformed artifact must not stop report generation
            values, checks = (
                {},
                [("Report could be built from the artifact", False, f"{type(exc).__name__}: {exc}")],
            )
        if artifact and not values and not checks:
            artifact = None
        results[name] = ("Completed" if artifact else "Not run", checks)
        sections[name] = {**values, "checks": _checks(checks) if checks else NOT_RUN}
    summary = _summary(run_dir, state, results)
    failed = summary.pop("_failed")
    summary["checks"] = (
        _bullets(failed, "_Every computed check passed._")
        if any(s == "Completed" for s, _ in results.values())
        else NOT_RUN
    )
    results["00-summary"] = (
        "Completed" if any(s == "Completed" for s, _ in results.values()) else "Not run",
        [],
    )
    sections["00-summary"] = summary
    out = {}
    for name in STEPS:
        status = results[name][0]
        out[name] = _render(name, {**sections[name], "meta": _meta(run_dir, state, name, status)})
    return out

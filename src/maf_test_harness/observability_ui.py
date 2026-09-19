"""Streamlit rendering of the observability report (kept out of ``app.py`` so it can be tested with the report alone)."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from .artifact_presenter import humanize
from .observability import render_markdown

RESULT_ORDER = ["passed", "failed", "error", "skipped", "not_executed"]


def _frame(rows: list[dict[str, Any]], columns: list[str] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if columns:
        frame = frame.reindex(columns=[c for c in columns if c in frame.columns] or frame.columns)
    return frame


def _table(
    rows: list[dict[str, Any]], columns: list[str] | None = None, empty: str = "Nothing recorded."
) -> None:
    if not rows:
        st.caption(empty)
        return
    st.dataframe(_frame(rows, columns), hide_index=True, width="stretch")


def render_observability(report: dict[str, Any], key_prefix: str = "obs") -> None:
    """One page per run: what ran, which skills and tools were invoked, what it cost, and how requirements trace to results."""
    run, totals = report["run"], report["totals"]
    st.subheader("Observability and traceability")
    st.caption(
        "Rebuilt from this run's directory: `trace.jsonl` (MAF middleware), `audit.jsonl` (host events) and `run.json` (state). "
        "Prompts, model responses and complete generated files are never part of the trace."
    )
    if report["trace_level"] != "full":
        st.info(
            "This run predates `trace.jsonl`: tools and skills are reconstructed from the recorded model runs, so tool "
            "arguments and timings are unavailable. New runs record them."
        )

    row1 = st.columns(4)
    row1[0].metric("Provider calls", totals["provider_calls"])
    row1[1].metric("Tokens (reported)", f"{totals['tokens']:,}")
    row1[2].metric("Model runs", totals["model_runs"])
    row1[3].metric(
        "Wall time", f"{totals['wall_seconds']:.0f} s" if totals["wall_seconds"] is not None else "n/a"
    )
    row2 = st.columns(4)
    row2[0].metric("Skill loads", totals["skill_loads"])
    row2[1].metric("Tool calls", totals["tool_calls"])
    row2[2].metric("Human gates", totals["human_gates"])
    row2[3].metric("Auto-fixes / build checks", f"{totals['autofix']} / {totals['build_checks']}")

    overview, skills, tools, timeline, trace, checks, export = st.tabs(
        ["Overview", "Skills", "Tools", "Timeline", "Traceability", "Integrity checks", "Export"]
    )

    with overview:
        st.markdown(
            "**Stages** — who ran, which skills were assigned and really loaded, and what each stage cost."
        )
        _table(
            [
                {
                    "stage": humanize(s["stage"]),
                    "actor": s["actor"],
                    "assigned skills": ", ".join(s["assigned_skills"]) or "—",
                    "loaded (verified)": ("✓ " if s["skills_verified"] else "")
                    + (", ".join(s["loaded_skills"]) or "—"),
                    "model runs": s["model_runs"],
                    "tool calls": s["tool_calls"],
                    "tokens": s["tokens"] if s["tokens"] is not None else "—",
                    "repairs": s["repairs"],
                    "errors": s["errors"],
                    "approved": "yes" if s["approved"] else "no",
                }
                for s in report["stages"]
            ]
        )
        with_tokens = [s for s in report["stages"] if s["tokens"]]
        if with_tokens:
            st.markdown("**Tokens by stage** (from provider-reported usage)")
            st.bar_chart(
                pd.DataFrame(
                    {"tokens": [s["tokens"] for s in with_tokens]},
                    index=[humanize(s["stage"]) for s in with_tokens],
                )
            )
        st.markdown("**Model runs**")
        _table(
            [
                {
                    "#": r["seq"],
                    "stage": humanize(r["stage"]),
                    "phase": r["phase"],
                    "skills": ", ".join(str(x) for x in r["skills"] if x),
                    "status": r["status"],
                    "temperature": r["temperature"],
                    "ms": r["duration_ms"],
                    "tokens": r["tokens"] if r["tokens"] is not None else "—",
                    "tools": ", ".join(r["tool_calls"]) or "—",
                }
                for r in report["model_runs"]
            ],
            empty="No model runs recorded.",
        )

    with skills:
        st.markdown(
            "**Skills invoked** — MAF `SkillsProvider`: advertised, then loaded with `load_skill`; scripts only from the reviewed allow-list."
        )
        _table(
            [
                {
                    "skill": s["skill"],
                    "origin": s["origin"],
                    "role": s["role"],
                    "stages": ", ".join(humanize(x) for x in s["stages"]),
                    "loads": s["loads"],
                    "scripts run": ", ".join(s["scripts"]) or "—",
                    "package sha256": s["package_sha256"][:12],
                }
                for s in report["skills"]
            ],
            empty="No skills were used in this run.",
        )
        skill_calls = [c for c in report["tool_calls"] if c["category"].startswith("skill")]
        if skill_calls:
            st.markdown("**Skill tool calls** (`load_skill`, `read_skill_resource`, `run_skill_script`)")
            _table(
                [
                    {
                        "t (s)": c["t"],
                        "stage": humanize(c["stage"]),
                        "tool": c["name"],
                        "arguments": ", ".join(f"{k}={v}" for k, v in c["arguments"].items()),
                        "ok": "✓" if c["ok"] else "✗",
                        "ms": c["duration_ms"],
                    }
                    for c in skill_calls
                ]
            )

    with tools:
        st.markdown(
            "**Tool usage by name.** Harness tools that were never called are listed with 0 calls: the host applies "
            "the same checks itself, so an unused tool is a finding about the model, not a gap in the harness."
        )
        _table(
            [
                {
                    "tool": t["tool"],
                    "category": t["category"],
                    "calls": t["calls"],
                    "failures": t["failures"],
                    "total ms": t["ms"],
                    "stages": t["stages"] or "—",
                }
                for t in report["tool_summary"]
            ]
        )
        st.markdown("**Call log**")
        calls = report["tool_calls"]
        stage_options = sorted({c["stage"] for c in calls if c["stage"]})
        category_options = sorted({c["category"] for c in calls})
        left, middle, right = st.columns(3)
        picked_stages = left.multiselect("Stage", stage_options, key=f"{key_prefix}-tool-stage")
        picked_categories = middle.multiselect(
            "Category", category_options, key=f"{key_prefix}-tool-category"
        )
        only_failed = right.checkbox("Only failures", key=f"{key_prefix}-tool-failed")
        shown = [
            c
            for c in calls
            if (not picked_stages or c["stage"] in picked_stages)
            and (not picked_categories or c["category"] in picked_categories)
            and (not only_failed or not c["ok"])
        ]
        st.caption(f"{len(shown)} of {len(calls)} call(s)")
        _table(
            [
                {
                    "#": c["seq"],
                    "t (s)": c["t"],
                    "stage": humanize(c["stage"]),
                    "phase": c["phase"],
                    "tool": c["name"],
                    "category": c["category"],
                    "arguments": ", ".join(f"{k}={v}" for k, v in c["arguments"].items()) or "—",
                    "ok": "✓" if c["ok"] else "✗",
                    "ms": c["duration_ms"],
                    "result chars": c["result_chars"],
                }
                for c in shown
            ],
            empty="No tool calls match.",
        )

    with timeline:
        rows = report["timeline"]
        lanes = sorted({r["lane"] for r in rows})
        picked_lanes = st.multiselect("Lanes", lanes, default=lanes, key=f"{key_prefix}-lanes")
        shown_rows = [r for r in rows if r["lane"] in picked_lanes]
        timed = [r for r in shown_rows if r["t"] is not None]
        if timed:
            try:
                import altair as alt

                chart = (
                    alt.Chart(pd.DataFrame(timed))
                    .mark_circle(size=60, opacity=0.7)
                    .encode(
                        x=alt.X("t:Q", title="seconds since the first event"),
                        y=alt.Y("lane:N", title=None),
                        color=alt.Color("stage:N", title="stage"),
                        tooltip=["t", "lane", "stage", "label", "detail"],
                    )
                    .properties(height=180)
                )
                st.altair_chart(chart, width="stretch")
            except Exception:  # the table below carries the same data
                pass
        st.caption(
            f"{len(shown_rows)} event(s): human gates, host actions, model runs, skill and tool calls in time order"
        )
        _table(
            [
                {
                    "t (s)": r["t"],
                    "lane": r["lane"],
                    "stage": humanize(r["stage"]),
                    "event": r["label"],
                    "detail": r["detail"],
                }
                for r in shown_rows
            ]
        )

    with trace:
        chain = report["traceability"]
        produced = chain["produced_by"]
        st.markdown(
            "**Chain:** requirement → test case → generated file → result. "
            f"Skills that produced each link: requirements by `{', '.join(produced['requirements']) or '—'}`; cases by "
            f"`{', '.join(produced['test_design']) or '—'}`; source by `{', '.join(produced['test_generation']) or '—'}`; "
            f"results by {produced['test_execution']}."
        )
        counts = chain["case_status_counts"]
        if counts:
            st.caption("Case results: " + ", ".join(f"{k} {counts[k]}" for k in RESULT_ORDER if k in counts))
        flat = [
            {
                "requirement": row["requirement"],
                "requirement text": (row["text"] or "")[:90],
                "state": row["state"],
                "actor": row["actor"],
                "case": case["case"],
                "result": case["status"],
                "test file": case["file"] or "—",
            }
            for row in chain["chain"]
            for case in (row["cases"] or [{"case": "—", "file": "", "status": "—"}])
        ]
        states = sorted({r["state"] for r in chain["chain"]})
        picked = st.multiselect("Requirement state", states, key=f"{key_prefix}-req-state")
        _table(
            [r for r in flat if not picked or r["state"] in picked],
            empty="No traceability data yet (design not approved).",
        )
        if chain["requirements_without_cases"]:
            st.warning("Requirements without a test case: " + ", ".join(chain["requirements_without_cases"]))
        if chain["orphan_cases"]:
            st.warning("Cases not linked to a requirement: " + ", ".join(chain["orphan_cases"]))

    with checks:
        _table(
            [
                {"check": c["check"], "result": "PASS" if c["ok"] else "FAIL", "detail": c["detail"]}
                for c in report["checks"]
            ]
        )
        telemetry = run.get("telemetry") or {}
        if telemetry:
            st.caption(
                f"OpenTelemetry: mode `{telemetry.get('mode')}`, enabled `{telemetry.get('enabled')}`. "
                + (telemetry.get("reason") or "")
                + " Message content is never captured."
            )

    with export:
        st.download_button(
            "Download report (Markdown)",
            render_markdown(report),
            file_name=f"observability-{run['id']}.md",
            mime="text/markdown",
            key=f"{key_prefix}-md",
        )
        st.download_button(
            "Download report (JSON)",
            json.dumps(report, indent=2, default=str),
            file_name=f"observability-{run['id']}.json",
            mime="application/json",
            key=f"{key_prefix}-json",
        )

"""The Observability report shows what the hooks, recovery engine, typed tools and diagnosis agent did."""

import json

import pytest

from maf_test_harness.monitoring.report import build_report, render_markdown


def write_run(tmp_path, audit, environment_reports=(), execution_reports=()):
    (tmp_path / "run.json").write_text(json.dumps({"status": "complete", "state": {}}))
    (tmp_path / "audit.jsonl").write_text("".join(json.dumps(e) + "\n" for e in audit))
    for folder, reports in (("environment", environment_reports), ("executions", execution_reports)):
        for n, report in enumerate(reports, 1):
            target = tmp_path / folder / f"attempt-{n}"
            target.mkdir(parents=True)
            (target / "report.json").write_text(json.dumps(report))
    return tmp_path


AUDIT = [
    {"event": "hook_event", "hook_event": "StageGate", "stage": "repo_analysis", "data": {}},
    {"event": "hook_event", "hook_event": "StageGate", "stage": "requirements", "data": {}},
    {"event": "hook_event", "hook_event": "OnEnvError", "stage": "environment_setup", "data": {}},
    {
        "event": "hook_decision",
        "hook_event": "PreToolUse",
        "tool": "write_test_file",
        "hook": "builtin:path_scope",
        "verdict": "deny",
        "reason": "path escapes",
        "enforced": True,
    },
    {
        "event": "hook_decision",
        "hook_event": "PreToolUse",
        "tool": "read_file",
        "hook": "user:strict",
        "verdict": "deny",
        "reason": "trial",
        "enforced": False,
    },
    {"event": "hook_error", "hook": "user:boom", "hook_event": "PreToolUse", "error": "RuntimeError: bug"},
    {"event": "typed_tool", "tool": "diagnose_env_error", "ok": True, "code": "ENV-PY-001"},
    {"event": "typed_tool", "tool": "diagnose_env_error", "ok": True, "code": "ENV-PY-001"},
    {"event": "typed_tool", "tool": "read_file", "ok": False, "code": "FILE-SCOPE"},
]
ENV_REPORT = {
    "status": "ready",
    "recoveries": [{"step": "install", "recipe": "retry-longer", "note": "timeout 900s -> 1800s"}],
    "steps": [
        {
            "name": "install",
            "recovery": [
                {"recipe": "retry-longer", "status": "applied", "note": "x"},
                {"recipe": "provision-python", "status": "needs_approval", "code": "ENV-PY-003"},
            ],
        }
    ],
    "agent_diagnosis": {
        "summary": "needs a compiler",
        "category": "environment",
        "suggested_fixes": ["install MSVC"],
    },
}


def test_the_report_summarises_hooks_recovery_typed_tools_and_agent_notes(tmp_path):
    run = write_run(tmp_path, AUDIT, [ENV_REPORT])
    automation = build_report(run)["automation"]
    hooks = automation["hooks"]
    assert hooks["events"] == {"StageGate": 2, "OnEnvError": 1}
    assert hooks["enforced_denies"] == 1 and hooks["would_block"] == 1 and hooks["errors"] == 1
    assert hooks["disabled"] is False
    assert {d["hook"] for d in hooks["decisions"]} == {"builtin:path_scope", "user:strict", "user:boom"}
    assert automation["typed_tools"][0] == {"tool": "diagnose_env_error", "calls": 2, "failures": 0}
    assert {"tool": "read_file", "calls": 1, "failures": 1} in automation["typed_tools"]
    applied = [r for r in automation["recoveries"] if "note" in r and r["recipe"] == "retry-longer"]
    assert applied and applied[0]["phase"] == "setup" and applied[0]["attempt"] == "attempt-1"
    assert any(
        r.get("status") == "needs_approval" and r["recipe"] == "provision-python"
        for r in automation["recoveries"]
    )
    assert automation["agent_diagnoses"][0]["summary"] == "needs a compiler"


def test_hooks_disabled_is_visible(tmp_path):
    run = write_run(tmp_path, [{"event": "hooks_disabled", "reason": "HARNESS_HOOKS=off"}])
    assert build_report(run)["automation"]["hooks"]["disabled"] is True


def test_a_quiet_run_has_an_empty_but_complete_automation_section(tmp_path):
    automation = build_report(write_run(tmp_path, []))["automation"]
    assert (
        automation["typed_tools"] == []
        and automation["recoveries"] == []
        and automation["agent_diagnoses"] == []
    )
    assert automation["hooks"]["decisions"] == []


def test_execution_reports_are_read_too(tmp_path):
    run = write_run(
        tmp_path, [], execution_reports=[{"status": "blocked", "agent_diagnosis": {"summary": "no app"}}]
    )
    assert build_report(run)["automation"]["agent_diagnoses"][0]["phase"] == "execution"


def test_markdown_has_the_automation_section(tmp_path):
    text = render_markdown(build_report(write_run(tmp_path, AUDIT, [ENV_REPORT])))
    assert "## Automation: hooks, recovery, typed tools" in text
    assert "1 enforced deny, 1 would-block" in text and "| diagnose_env_error | 2 | 0 |" in text
    assert "Automatic recoveries applied: 1" in text and "Diagnosis agent notes: 1" in text
    assert text.index("## Automation") < text.index("## Stages")


def test_the_observability_page_renders_the_automation_tab(tmp_path):
    AppTest = pytest.importorskip(
        "streamlit.testing.v1", reason="Legacy UI is not installed on new-ui"
    ).AppTest

    run = write_run(tmp_path, AUDIT, [ENV_REPORT])
    script = (
        "import sys\n"
        "sys.path.insert(0, 'src')\n"
        "from maf_test_harness.monitoring.report import build_report\n"
        "from maf_test_harness.monitoring.ui import render_observability\n"
        f"render_observability(build_report(__import__('pathlib').Path({str(run)!r})))\n"
    )
    app = AppTest.from_string(script).run(timeout=30)
    assert not app.exception, app.exception
    labels = [m.label for m in app.metric]
    assert "Enforced denies" in labels and "Would block (evaluate-only)" in labels
    assert any("Automation" in str(t.label) for t in app.tabs)


def test_the_overview_shows_the_prompt_cache_table(tmp_path):
    AppTest = pytest.importorskip(
        "streamlit.testing.v1", reason="Legacy UI is not installed on new-ui"
    ).AppTest

    trace = [
        {
            "seq": 1,
            "kind": "chat_call",
            "stage": "test_generation",
            "status": "ok",
            "tokens": {"input": 1000, "output": 50, "total": 1050, "cached": 800},
            "prompt_chars": 4000,
            "prefix_shared_chars": 3000,
            "at": "2026-01-01T00:00:00+00:00",
        }
    ]
    run = write_run(tmp_path, [])
    (run / "trace.jsonl").write_text("".join(json.dumps(e) + "\n" for e in trace))
    script = (
        "import sys\n"
        "sys.path.insert(0, 'src')\n"
        "from maf_test_harness.monitoring.report import build_report\n"
        "from maf_test_harness.monitoring.ui import render_observability\n"
        f"render_observability(build_report(__import__('pathlib').Path({str(run)!r})))\n"
    )
    app = AppTest.from_string(script).run(timeout=30)
    assert not app.exception, app.exception
    assert any("Prompt cache" in m.value for m in app.markdown)

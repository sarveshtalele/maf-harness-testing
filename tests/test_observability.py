"""Run trace (MAF middleware), the observability report rebuilt from disk, the trace CLI and optional telemetry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_framework import ChatResponse, Content, Message
from test_native_maf import ScriptedClient, fresh_state

import maf_test_harness.maf_runtime as runtime_module
import maf_test_harness.telemetry as telemetry
from maf_test_harness import cli
from maf_test_harness.agent_tools import HarnessToolbox
from maf_test_harness.code_intel import RepoContext
from maf_test_harness.maf_runtime import MAFHarnessRuntime
from maf_test_harness.observability import build_report, render_markdown
from maf_test_harness.runtime_service import RuntimeService
from maf_test_harness.trace import TraceRecorder, read_trace, redact, summarize_arguments, tool_category

USAGE = {"input_token_count": 10, "output_token_count": 10, "total_token_count": 20}


class TwoPhaseClient(ScriptedClient):
    """Prime turn: load_skill then READY. Structured turn: one harness tool call (find_symbol), then JSON."""

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.calls += 1

        def reply(*contents):
            return ChatResponse(
                messages=[Message(role="assistant", contents=list(contents))], usage_details=USAGE
            )

        if self.calls == 1:
            return reply(
                Content.from_function_call(
                    call_id="c1",
                    name="load_skill",
                    arguments=json.dumps({"skill_name": "generic-test-generator"}),
                )
            )
        if self.calls == 2:
            return reply(Content.from_text("READY"))
        if self.calls == 3:
            return reply(
                Content.from_function_call(call_id="c2", name="find_symbol", arguments='{"name": "Runner"}')
            )
        return reply(Content.from_text('{"hello":"world"}'))


def test_trace_recorder_sequence_redaction_and_torn_lines(tmp_path):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path)
    first = recorder.emit("tool_call", name="a")
    assert first["seq"] == 1 and "at" in first
    assert (
        TraceRecorder(path).emit("tool_call", name="b")["seq"] == 2
    )  # a new recorder continues the sequence
    with path.open("a") as stream:
        stream.write('{"seq": 3, "kind": "torn"\n')  # a torn line must not break reading
        stream.write("[1, 2]\n")
    assert [e["name"] for e in read_trace(path)] == ["a", "b"]
    assert read_trace(tmp_path / "missing.jsonl") == []


def test_arguments_are_summarised_and_secret_shaped_strings_redacted():
    long_text = "def test_x():\n    " + "x" * 400
    summary = summarize_arguments(
        {
            "content": long_text,
            "token": "sk-abcdef1234567890",
            "n": 3,
            "opt": None,
            "obj": {"k": "Bearer abcdefgh12345"},
        }
    )
    assert (
        "400" not in summary["content"]
        and "chars, sha256:" in summary["content"]
        and len(summary["content"]) < 200
    )
    assert summary["token"] == "[redacted]" and summary["n"] == 3 and summary["opt"] is None
    assert "abcdefgh12345" not in summary["obj"]
    assert summarize_arguments("sk-abcdef1234567890") == {"value": "[redacted]"}
    assert redact("key ghp_abcdefghijklmnopqrstu") == "key [redacted]"
    assert tool_category("load_skill") == "skill_load" and tool_category("run_skill_script") == "skill_script"
    assert (
        tool_category("find_symbol", ("find_symbol",)) == "harness" and tool_category("shell") == "unexpected"
    )


@pytest.fixture
def traced_run(tmp_path, monkeypatch):
    """A real MAF harness run (scripted transport): skill loaded, one harness tool called, everything traced."""
    client = TwoPhaseClient()
    monkeypatch.setattr(runtime_module, "OpenAIChatCompletionClient", lambda **kwargs: client)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "engine.py").write_text("class Runner:\n    pass\n")
    toolbox = HarnessToolbox(lambda: RepoContext(root=repo, source_files=["engine.py"]))
    runtime = MAFHarnessRuntime(
        api_key="k",
        model="m",
        skills_root=Path("skills"),
        base_url="http://test",
        tool_provider=toolbox.tools_for,
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    runtime.recorder = TraceRecorder(run_dir / "trace.jsonl")
    state = fresh_state()
    service = RuntimeService()

    async def go():
        return await runtime.run_stage(
            stage="test_generation",
            skill_name="generic-test-generator",
            persona="P",
            prompt="Return JSON.",
            state=state,
            persist=lambda: None,
        )

    try:
        result = service.submit(go()).result(30)
    finally:
        service.close()
    (run_dir / "run.json").write_text(json.dumps({"status": "complete", "state": state}))
    return run_dir, state, result


def test_middleware_records_model_runs_provider_calls_and_tool_calls(traced_run):
    run_dir, state, result = traced_run
    assert result["artifact"] == {"hello": "world"}
    events = read_trace(run_dir / "trace.jsonl")
    kinds = [e["kind"] for e in events]
    assert kinds.count("chat_call") == 4 and kinds.count("model_run") == 2 and kinds.count("tool_call") == 2
    tools = {e["name"]: e for e in events if e["kind"] == "tool_call"}
    assert tools["load_skill"]["category"] == "skill_load" and tools["load_skill"]["arguments"] == {
        "skill_name": "generic-test-generator"
    }
    assert tools["find_symbol"]["category"] == "harness" and tools["find_symbol"]["ok"] is True
    assert all(isinstance(t["duration_ms"], int) for t in tools.values())
    prime, structured = [e for e in events if e["kind"] == "model_run"]
    assert prime["phase"] == "prime" and prime["tool_calls"] == ["load_skill"]
    assert structured["phase"] == "structured" and structured["tool_calls"] == ["find_symbol"]
    assert structured["status"] == "returned" and structured["skills"] == ["generic-test-generator"]
    chats = [e for e in events if e["kind"] == "chat_call"]
    assert {c["stage"] for c in chats} == {"test_generation"} and all(
        c["tokens"]["total"] == 20 for c in chats
    )
    assert sum(c["tokens"]["total"] for c in chats) == state["budget"]["reported_tokens"] == 80
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


def test_report_from_a_real_traced_run_answers_skills_tools_and_cost(traced_run):
    run_dir, _state, _ = traced_run
    report = build_report(run_dir, Path("skills"))
    assert report["trace_level"] == "full"
    assert report["totals"]["skill_loads"] == 1 and report["totals"]["harness_tool_calls"] == 1
    assert report["totals"]["provider_calls"] == 4 and report["totals"]["tokens"] == 80
    generation = next(s for s in report["stages"] if s["stage"] == "test_generation")
    assert generation["loaded_skills"] == ["generic-test-generator"] and generation["skills_verified"] is True
    assert generation["harness_tools_called"] == {"find_symbol": 1} and generation["tokens"] == 80
    by_tool = {t["tool"]: t for t in report["tool_summary"]}
    assert (
        by_tool["find_symbol"]["calls"] == 1 and by_tool["check_test"]["calls"] == 0
    )  # never-called tools are listed
    assert report["skills"][0]["skill"] == "generic-test-generator" and report["skills"][0]["loads"] == 1
    assert {c["check"]: c["ok"] for c in report["checks"]}[
        "Trace tokens match the daily-ledger accounting"
    ] is True
    lanes = {row["lane"] for row in report["timeline"]}
    assert {"model", "skill", "tool"} <= lanes
    assert "find_symbol" in render_markdown(report) and "generic-test-generator" in render_markdown(report)


def _write_run(tmp_path, state, audit=(), trace=()):
    (tmp_path / "run.json").write_text(json.dumps({"status": "complete", "state": state}))
    (tmp_path / "audit.jsonl").write_text("".join(json.dumps(e) + "\n" for e in audit))
    if trace:
        (tmp_path / "trace.jsonl").write_text("".join(json.dumps(e) + "\n" for e in trace))
    return tmp_path


def _provenance():
    return {
        "test_design": {
            "name": "test-design-generator",
            "package_sha256": "a" * 64,
            "companions": [{"name": "test-data-generator", "package_sha256": "b" * 64}],
        }
    }


def test_older_runs_without_a_trace_file_still_render_and_count_primed_loads_once(tmp_path):
    state = {
        "stages": ["test_design"],
        "skill_provenance": _provenance(),
        "skill_tool_trace": {
            "test_design": {
                "loaded_skills": ["test-design-generator", "test-data-generator"],
                "skill_loaded_via_maf": True,
            }
        },
        "invocations": [
            {
                "stage": "test_design",
                "skill": "test-design-generator",
                "companions": ["test-data-generator"],
                "status": "returned",
                "tool_calls": ["load_skill", "load_skill"],
            },
            {
                "stage": "test_design",
                "skill": "test-design-generator",
                "companions": ["test-data-generator"],
                "status": "returned",
                "tool_calls": ["load_skill", "load_skill", "find_symbol"],
            },
        ],
        "budget": {"calls": 3, "reported_tokens": 90},
    }
    report = build_report(_write_run(tmp_path, state), Path("skills"))
    assert report["trace_level"] == "summary"
    assert [c["name"] for c in report["tool_calls"]] == ["load_skill", "load_skill", "find_symbol"]
    assert report["totals"]["skill_loads"] == 2 and report["totals"]["harness_tool_calls"] == 1
    assert {c["check"]: c["detail"] for c in report["checks"]}["Trace detail level"].startswith(
        "summary only"
    )
    skills = {s["skill"]: s for s in report["skills"]}
    assert (
        skills["test-data-generator"]["origin"] == "upstream"
        and skills["test-design-generator"]["origin"] == "local"
    )


def test_integrity_checks_flag_missing_skills_unexpected_and_failed_tools_and_token_drift(tmp_path):
    state = {
        "stages": ["test_design"],
        "skill_provenance": _provenance(),
        "skill_tool_trace": {
            "test_design": {"loaded_skills": ["test-design-generator"], "skill_loaded_via_maf": False}
        },
        "budget": {"calls": 1, "reported_tokens": 999},
    }
    trace = [
        {
            "seq": 1,
            "at": "2026-01-01T00:00:00+00:00",
            "kind": "tool_call",
            "stage": "test_design",
            "phase": "prime",
            "name": "load_skill",
            "category": "skill_load",
            "arguments": {"skill_name": "test-design-generator"},
            "ok": True,
            "duration_ms": 4,
        },
        {
            "seq": 2,
            "at": "2026-01-01T00:00:01+00:00",
            "kind": "tool_call",
            "stage": "test_design",
            "phase": "structured",
            "name": "shell",
            "category": "unexpected",
            "arguments": {},
            "ok": False,
            "duration_ms": 1,
        },
        {
            "seq": 3,
            "at": "2026-01-01T00:00:02+00:00",
            "kind": "chat_call",
            "stage": "test_design",
            "status": "error",
            "duration_ms": 5,
            "error": "RateLimitError",
        },
        {
            "seq": 4,
            "at": "2026-01-01T00:00:03+00:00",
            "kind": "chat_call",
            "stage": "test_design",
            "status": "ok",
            "duration_ms": 5,
            "tokens": {"total": 20},
        },
    ]
    report = build_report(_write_run(tmp_path, state, trace=trace), Path("skills"))
    checks = {c["check"]: c for c in report["checks"]}
    assert not checks["Every assigned skill was loaded through MAF load_skill"]["ok"]
    assert "test-data-generator" in checks["Every assigned skill was loaded through MAF load_skill"]["detail"]
    assert (
        not checks["No unexpected tools were invoked"]["ok"]
        and "shell" in checks["No unexpected tools were invoked"]["detail"]
    )
    assert not checks["Tool calls succeeded"]["ok"] and not checks["Provider calls succeeded"]["ok"]
    assert not checks["Trace tokens match the daily-ledger accounting"]["ok"]
    assert report["totals"]["unexpected_tool_calls"] == 1


def test_traceability_chain_links_requirements_cases_files_and_results(tmp_path):
    qa = {
        "verdict": "FAIL",
        "adapter": "python-pytest",
        "cases": [
            {"id": "TC-1", "title": "adds", "level": "unit", "file": "tests/test_a.py", "status": "passed"},
            {
                "id": "TC-2",
                "title": "subtracts",
                "level": "unit",
                "file": "tests/test_b.py",
                "status": "failed",
            },
            {"id": "TC-3", "title": "loose", "level": "unit", "file": "tests/test_c.py", "status": "passed"},
        ],
        "requirement_coverage": [
            {"id": "REQ-1", "text": "add", "actor": "dev", "state": "verified", "cases": ["TC-1"]},
            {"id": "REQ-2", "text": "sub", "actor": "dev", "state": "failing", "cases": ["TC-2"]},
            {"id": "REQ-3", "text": "mul", "actor": "dev", "state": "untested", "cases": []},
        ],
    }
    state = {
        "stages": ["test_design"],
        "skill_provenance": _provenance(),
        "artifacts": {"test_execution": {"qa_report": qa}},
    }
    trace = build_report(_write_run(tmp_path, state), Path("skills"))["traceability"]
    assert trace["verdict"] == "FAIL" and trace["case_status_counts"] == {"passed": 1, "failed": 1}
    assert trace["produced_by"]["test_design"] == ["test-design-generator", "test-data-generator"]
    assert trace["produced_by"]["test_execution"] == "host runner (python-pytest)"
    assert [r["requirement"] for r in trace["chain"]] == ["REQ-1", "REQ-2", "REQ-3"]
    assert trace["chain"][1]["cases"][0] == {
        "case": "TC-2",
        "title": "subtracts",
        "level": "unit",
        "file": "tests/test_b.py",
        "status": "failed",
    }
    assert trace["requirements_without_cases"] == ["REQ-3"] and trace["orphan_cases"] == ["TC-3"]


def test_traceability_before_execution_comes_from_design_and_generation(tmp_path):
    state = {
        "stages": [],
        "artifacts": {
            "requirements": {
                "requirement_model": {"requirements": [{"id": "REQ-1", "text": "add", "actor": "dev"}]}
            },
            "test_design": {
                "positive_tests": [{"id": "TC-1", "title": "adds"}],
                "traceability_matrix": {"requirement_to_tests": {"REQ-1": ["TC-1"]}},
            },
            "test_generation": {"files": [{"path": "tests/test_a.py", "case_ids": ["TC-1"]}]},
        },
    }
    trace = build_report(_write_run(tmp_path, state), Path("skills"))["traceability"]
    assert trace["verdict"] is None
    assert trace["chain"][0]["cases"] == [
        {"case": "TC-1", "title": "adds", "level": "", "file": "tests/test_a.py", "status": "not_executed"}
    ]


def test_report_needs_a_harness_run_directory(tmp_path):
    with pytest.raises(FileNotFoundError, match="not a harness run directory"):
        build_report(tmp_path)


def test_trace_cli_prints_markdown_json_and_reports_a_missing_run(tmp_path, capsys):
    run_dir = _write_run(
        tmp_path,
        {
            "stages": ["test_design"],
            "skill_provenance": _provenance(),
            "skill_tool_trace": {
                "test_design": {
                    "loaded_skills": ["test-design-generator", "test-data-generator"],
                    "skill_loaded_via_maf": True,
                }
            },
        },
    )
    assert cli.main(["trace", str(run_dir)]) == 0
    assert "# Observability and traceability" in capsys.readouterr().out
    out = tmp_path / "report.json"
    assert cli.main(["trace", str(run_dir), "--format", "json", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["run"]["id"] == run_dir.name
    assert cli.main(["trace", str(tmp_path / "nope")]) == 2
    (tmp_path / "bad").mkdir()
    bad = _write_run(
        tmp_path / "bad",
        {
            "stages": ["test_design"],
            "skill_provenance": _provenance(),
            "skill_tool_trace": {"test_design": {"loaded_skills": [], "skill_loaded_via_maf": False}},
        },
    )
    assert cli.main(["trace", str(bad)]) == 1  # a failed integrity check is a non-zero exit


def test_telemetry_is_off_by_default_never_captures_content_and_survives_a_missing_sdk(monkeypatch):
    monkeypatch.setattr(telemetry, "_configured", None)
    status = telemetry.configure_telemetry("off")
    assert (
        status["enabled"] is False and status["sensitive_data"] is False and "no exporter" in status["reason"]
    )
    monkeypatch.setattr(telemetry, "_configured", None)
    assert "unknown HARNESS_OTEL" in telemetry.configure_telemetry("carrier-pigeon")["reason"]

    calls = {}
    import agent_framework.observability as observability

    def fake_configure(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr(observability, "configure_otel_providers", fake_configure)
    monkeypatch.setattr(telemetry, "_configured", None)
    assert telemetry.configure_telemetry("console")["enabled"] is True
    assert calls == {"enable_console_exporters": True, "enable_sensitive_data": False}

    def missing_sdk(**_):
        raise ModuleNotFoundError("No module named 'opentelemetry.sdk'")

    monkeypatch.setattr(observability, "configure_otel_providers", missing_sdk)
    monkeypatch.setattr(telemetry, "_configured", None)
    result = telemetry.configure_telemetry("otlp")
    assert (
        result["enabled"] is False
        and "pip install opentelemetry-sdk opentelemetry-exporter-otlp" in result["reason"]
    )
    monkeypatch.setattr(telemetry, "_configured", None)

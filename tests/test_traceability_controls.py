import csv
import io
import zipfile
from pathlib import Path

from fakes import ScriptedStageRuntime, clone_fixture
from test_step_reports import fixture

from maf_test_harness.qa.controls import (
    add_plan_controls,
    assign_controls,
    case_controls,
    plan_controls,
    summarize_trace,
    summary_text,
    trace_matrix,
    trace_rows,
)
from maf_test_harness.qa.packaging import build_deliverable
from maf_test_harness.qa.report import build_qa_report, render_markdown
from maf_test_harness.skills.contracts import case_marker_meta, parse_case_marker

AREA = {
    "name": "Core",
    "objective": "core arithmetic",
    "level": "unit",
    "risk": "high",
    "evidence_files": ["src/a.py", "src/b.py"],
    "scenarios": ["sums values"],
}


def _req(rid, source, symbol=""):
    return {"id": rid, "text": f"{rid} behaves", "source_files": [source], "source_symbol": symbol}


def _case(cid, rid, **extra):
    return {
        "id": cid,
        "title": f"{cid} title",
        "type": "positive",
        "level": "unit",
        "actor": "Developer",
        "traceability_links": [{"type": "requirement", "id": rid}],
        **extra,
    }


def _test_file(cid, name):
    return {
        "path": f"tests/test_{name}.py",
        "case_ids": [cid],
        "content": f"def test_{name}():\n    assert True\n",
    }


def _result(name, status, message=""):
    return {"name": f"test_{name}", "classname": f"tests.test_{name}", "status": status, "message": message}


def _state(requirements, plan_areas, cases, files, failures=(), execution=None, environment=None):
    reqs = {"requirement_model": {"requirements": requirements}}
    plan = add_plan_controls({"areas": plan_areas, "execution_order": ["unit"]}, reqs)
    design = {"positive_tests": cases, "traceability_matrix": {}, "gaps_report": {}}
    for case in cases:
        case["control_ids"] = case_controls(case, plan["controls"])
    return {
        "artifacts": {
            "repo_analysis": {"source_files": ["src/a.py", "src/b.py"]},
            "requirements": reqs,
            "test_planning": plan,
            "test_design": design,
            "test_generation": {"files": files, "generation_failures": list(failures)},
            **({"test_execution": {"execution": execution, "qa_report": {}}} if execution else {}),
        },
        "environment": environment or {"status": "ready"},
    }


def test_assign_controls_is_deterministic_and_maps_plan_areas_by_file_overlap():
    reqs = {
        "requirement_model": {
            "requirements": [
                _req("REQ-10", "src/b.py"),
                _req("REQ-2", "src/a.py"),
                _req("REQ-1", "src/zzz.py"),
            ]
        }
    }
    plan = {"areas": [{**AREA, "name": "First", "evidence_files": ["src/x.py"]}, {**AREA, "name": "Second"}]}
    controls = assign_controls(reqs, plan)
    assert [c["requirement_id"] for c in controls] == ["REQ-1", "REQ-2", "REQ-10"]
    assert [c["control_id"] for c in controls] == ["CTL-001", "CTL-002", "CTL-003"]
    assert [(c["plan_area_id"], c["plan_area_name"]) for c in controls] == [
        ("PLN-00", "Not planned"),
        ("PLN-02", "Second"),
        ("PLN-02", "Second"),
    ]
    assert assign_controls(reqs, plan) == controls  # same inputs, same IDs


def test_symbol_match_and_tie_break_keeps_first_planned_area():
    reqs = {"requirement_model": {"requirements": [_req("REQ-1", "src/none.py", "add_values")]}}
    plan = {"areas": [{**AREA, "name": "A", "scenarios": ["calls add_values"]}, {**AREA, "name": "B"}]}
    assert assign_controls(reqs, plan)[0]["plan_area_id"] == "PLN-01"
    plan["areas"][0]["scenarios"] = ["other"]
    assert assign_controls(reqs, plan)[0]["plan_area_id"] == "PLN-00"


def test_case_controls_match_exact_requirement_ids_only():
    reqs = {"requirement_model": {"requirements": [_req("REQ-1", "src/a.py"), _req("REQ-10", "src/a.py")]}}
    controls = assign_controls(reqs, {"areas": [AREA]})
    assert case_controls(_case("TC-1", "REQ-1"), controls) == ["CTL-001"]
    assert case_controls(_case("TC-10", "REQ-10"), controls) == ["CTL-002"]


def test_plan_controls_are_recomputed_for_runs_without_stored_controls():
    reqs = {"requirement_model": {"requirements": [_req("REQ-1", "src/a.py")]}}
    old_plan = {"areas": [AREA]}
    assert plan_controls(reqs, old_plan) == assign_controls(reqs, old_plan)


def test_case_header_carries_control_ids_and_old_headers_still_parse():
    case = {**_case("TC-1", "REQ-1"), "control_ids": ["CTL-001", "CTL-002"]}
    line = "# CASE_ID: TC-1 | RUN: r1 | REQ: REQ-1 | CTL: CTL-001, CTL-002 | TITLE: TC-1 title"
    assert case_marker_meta(case, "r1")["ctl"] == "CTL-001, CTL-002"
    parsed = parse_case_marker(line)
    assert parsed == {
        "case_id": "TC-1",
        "run": "r1",
        "req": ["REQ-1"],
        "ctl": ["CTL-001", "CTL-002"],
        "title": "TC-1 title",
    }
    old = parse_case_marker("# CASE_ID: TC-1 | RUN: r1 | REQ: REQ-1 | TITLE: TC-1 title")
    assert old["ctl"] == [] and old["title"] == "TC-1 title"
    assert case_marker_meta({"id": "TC-2", "title": "t"}, "r1") == {
        "run": "r1",
        "req": "",
        "ctl": "",
        "title": "t",
    }


def test_trace_matrix_reports_the_first_failing_step_for_each_control():
    requirements = [
        _req("REQ-1", "src/a.py"),  # passes
        _req("REQ-2", "src/a.py"),  # test fails
        _req("REQ-3", "src/a.py"),  # generation fails
        _req("REQ-4", "src/zzz.py"),  # no planned area
        _req("REQ-5", "src/a.py"),  # planned, but no case designed
    ]
    cases = [_case("TC-1", "REQ-1"), _case("TC-2", "REQ-2"), _case("TC-3", "REQ-3")]
    files = [_test_file("TC-1", "a"), _test_file("TC-2", "b")]
    failures = [
        {"batch": 1, "case_ids": ["TC-3"], "attempts": 2, "last_error": "model returned invalid JSON"}
    ]
    execution = {
        "status": "failed",
        "cases": [_result("a", "passed"), _result("b", "failed", "AssertionError: boom")],
    }
    state = _state([*requirements], [AREA], cases, files, failures, execution)
    state["artifacts"]["test_design"]["gaps_report"] = {
        "notes": "REQ-5 is unreachable from the public API; TC-9 is unrelated"
    }
    rows = {r["requirement_id"]: r for r in trace_matrix(state)}
    assert (rows["REQ-1"]["final_status"], rows["REQ-1"]["failed_at"], rows["REQ-1"]["reason"]) == (
        "passed",
        "",
        "",
    )
    assert (rows["REQ-2"]["final_status"], rows["REQ-2"]["failed_at"]) == ("failed", "test_execution")
    assert "boom" in rows["REQ-2"]["reason"] and rows["REQ-2"]["test_files"] == ["tests/test_b.py"]
    assert (rows["REQ-3"]["final_status"], rows["REQ-3"]["failed_at"], rows["REQ-3"]["generation"]) == (
        "generation_failed",
        "generation",
        "generation_failed",
    )
    assert "invalid JSON" in rows["REQ-3"]["reason"]
    assert (rows["REQ-4"]["final_status"], rows["REQ-4"]["failed_at"]) == ("not_designed", "planning")
    assert (rows["REQ-5"]["final_status"], rows["REQ-5"]["failed_at"]) == ("not_designed", "design")
    assert rows["REQ-5"]["reason"] == "REQ-5 is unreachable from the public API"


def test_multi_case_control_takes_the_worst_status_and_earliest_failed_step():
    cases = [_case("TC-1", "REQ-1"), _case("TC-2", "REQ-1")]
    files = [_test_file("TC-1", "a")]
    failures = [{"batch": 1, "case_ids": ["TC-2"], "attempts": 1, "last_error": "no file"}]
    execution = {"status": "failed", "cases": [_result("a", "failed", "boom")]}
    state = _state([_req("REQ-1", "src/a.py")], [AREA], cases, files, failures, execution)
    (row,) = trace_matrix(state)
    assert row["final_status"] == "generation_failed" and row["failed_at"] == "generation"
    assert [c["case_id"] for c in row["cases"]] == ["TC-1", "TC-2"]
    assert row["generation"] == "generation_failed" and row["execution"] == "not_executed"


def test_environment_failure_stops_cases_at_environment_setup():
    env = {"status": "failed", "plan": {"missing_prerequisites": ["jdk 21 missing"]}}
    state = _state(
        [_req("REQ-1", "src/a.py")],
        [AREA],
        [_case("TC-1", "REQ-1")],
        [_test_file("TC-1", "a")],
        environment=env,
    )
    (row,) = trace_matrix(state)
    assert (row["environment"], row["final_status"], row["failed_at"]) == (
        "failed",
        "not_executed",
        "environment_setup",
    )
    assert "jdk 21 missing" in row["reason"]


def test_markdown_has_traceability_section_and_control_columns():
    requirements = [_req("REQ-1", "src/a.py"), _req("REQ-2", "src/zzz.py")]
    cases = [_case("TC-1", "REQ-1")]
    state = _state(
        requirements,
        [AREA],
        cases,
        [_test_file("TC-1", "a")],
        execution={
            "status": "passed",
            "cases": [_result("a", "passed")],
        },
    )
    reqs = state["artifacts"]["requirements"]
    qa = build_qa_report(
        repo={"source_files": []},
        requirements=reqs,
        design=state["artifacts"]["test_design"],
        generation=state["artifacts"]["test_generation"],
        execution=state["artifacts"]["test_execution"]["execution"],
        plan=state["artifacts"]["test_planning"],
    )
    md = render_markdown(qa)
    assert (
        md.index("## Evidence scope") < md.index("## End-to-end traceability") < md.index("## Case results")
    )
    assert (
        "| Control | Requirement | Plan area | Test case(s) | Test file(s) | Generation | Execution | Status | Failed at | Reason |"
        in md
    )
    assert "| ID | Control | Case |" in md
    assert (
        "| CTL-001 | REQ-1 | PLN-01 Core | TC-1 | tests/test_a.py | generated | passed | passed | - |" in md
    )
    assert "| CTL-002 | REQ-2 | PLN-00 Not planned |  |  | n/a | n/a | not_designed | planning |" in md
    assert qa["traceability_summary"]["failed_at"] == {"planning": 1}
    assert "Controls: **2** · passed 1 · failed at: planning 1" in md


def test_deliverable_contains_traceability_json_and_csv(tmp_path: Path):
    (tmp_path / "repo").mkdir()
    state = fixture(tmp_path)
    with zipfile.ZipFile(build_deliverable(tmp_path, state)) as archive:
        names = archive.namelist()
        assert "harness-report/traceability.json" in names and "harness-report/traceability.csv" in names
        text = archive.read("harness-report/traceability.csv").decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][:3] == ["control_id", "requirement_id", "plan_area_id"]
    assert len(rows) >= 2


def test_workflow_assigns_plan_controls_and_case_control_ids(tmp_path: Path, monkeypatch):
    import maf_test_harness.workflow as wf
    from maf_test_harness.ui.runtime_service import RuntimeService
    from maf_test_harness.workflow import Harness

    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    runtime = ScriptedStageRuntime()
    h = Harness(
        tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=runtime, execute=False
    )
    svc = RuntimeService()

    def answer(**response):
        return svc.submit(h.respond(h.view["pending"]["id"], response)).result(30)

    try:
        svc.submit(h.start("https://github.com/acme/demo")).result(10)
        for _ in range(5):  # clone, repo review, requirements, planning, design
            answer(decision="approve")
        plan = h.state["artifacts"]["test_planning"]
        controls = plan["controls"]
        assert controls and all(c["control_id"].startswith("CTL-") for c in controls)
        assert all(area["area_id"].startswith("PLN-") for area in plan["areas"])
        cases = [
            c
            for key in ("positive_tests", "negative_tests")
            for c in h.state["artifacts"]["test_design"].get(key, [])
        ]
        assert cases and all("control_ids" in case for case in cases)
        assert any(case["control_ids"] for case in cases)
        rows = trace_matrix(h.state)
        assert len(rows) == len(controls)
    finally:
        svc.close()


def test_witness_excluded_and_skipped_outcomes_are_not_failures():
    requirements = [_req("REQ-1", "src/a.py"), _req("REQ-2", "src/a.py"), _req("REQ-3", "src/a.py")]
    execution = {
        "status": "passed",
        "cases": [
            {"name": "test_a", "classname": "tests.test_a", "status": "skipped", "message": "no fixture"}
        ],
    }
    state = _state(
        requirements, [AREA], [_case("TC-1", "REQ-1")], [_test_file("TC-1", "a")], execution=execution
    )
    state["artifacts"]["test_design"]["gaps_report"] = {
        "pre_existing_test_witnesses": {
            "REQ-2": [
                {"test_file": "tests/test_old.py", "symbol": "add", "line": 3, "kind": "direct_invocation"}
            ]
        },
        "excluded_requirements": ["REQ-3"],
        "notes": "REQ-3 needs a live database; unrelated",
    }
    rows = {r["requirement_id"]: r for r in trace_matrix(state)}
    assert (rows["REQ-1"]["final_status"], rows["REQ-1"]["failed_at"], rows["REQ-1"]["reason"]) == (
        "not_executed",
        "",
        "skipped: no fixture",
    )
    assert (rows["REQ-2"]["final_status"], rows["REQ-2"]["failed_at"], rows["REQ-2"]["reason"]) == (
        "covered_by_existing_test",
        "",
        "tests/test_old.py",
    )
    assert (rows["REQ-3"]["final_status"], rows["REQ-3"]["failed_at"], rows["REQ-3"]["reason"]) == (
        "excluded",
        "",
        "REQ-3 needs a live database",
    )
    assert summarize_trace(list(rows.values()))["failed_at"] == {}


def test_summary_line_omits_zero_parts_and_uses_pipeline_order():
    rows = [
        {"final_status": "passed", "failed_at": ""},
        {"final_status": "covered_by_existing_test", "failed_at": ""},
        {"final_status": "generation_failed", "failed_at": "generation"},
        {"final_status": "not_designed", "failed_at": "planning"},
    ]
    assert summary_text(summarize_trace(rows)) == (
        "Controls: **4** · passed 1 · covered by existing 1 · failed at: planning 1, generation 1"
    )
    assert summary_text(summarize_trace([])) == "Controls: **0**"


def test_run_without_a_plan_falls_through_to_design_not_planning():
    controls = assign_controls({"requirement_model": {"requirements": [_req("REQ-1", "src/a.py")]}}, None)
    assert controls[0]["plan_area_id"] == ""
    (row,) = trace_rows(controls, [], {}, None, {"gaps_report": {}})
    assert (row["final_status"], row["failed_at"]) == ("not_designed", "design")


def test_case_controls_ignore_non_requirement_links():
    controls = assign_controls(
        {"requirement_model": {"requirements": [_req("REQ-1", "src/a.py")]}}, {"areas": [AREA]}
    )
    case = {"id": "TC-1", "traceability_links": [{"type": "acceptance_criterion", "id": "REQ-1"}]}
    assert case_controls(case, controls) == []


def test_traceability_csv_cells_are_formula_safe():
    from maf_test_harness.qa.packaging import _traceability_csv

    row = {
        "control_id": "CTL-001",
        "requirement_id": "REQ-1",
        "plan_area_id": "PLN-01",
        "level": "unit",
        "environment": "ok",
        "final_status": "failed",
        "failed_at": "test_execution",
        "reason": "=cmd|' /C calc'!A0",
        "generation": "generated",
        "execution": "failed",
        "cases": [
            {
                "case_id": "TC-1",
                "title": "-2 != 3",
                "file": "tests/x.py",
                "generation": "generated",
                "execution": "failed",
            }
        ],
    }
    (parsed,) = list(csv.DictReader(io.StringIO(_traceability_csv([row]))))
    assert parsed["reason"] == "'=cmd|' /C calc'!A0"
    assert parsed["case_title"] == "'-2 != 3"


def test_model_written_case_marker_is_replaced_by_the_host_line():
    from maf_test_harness.skills.contracts import ensure_generated_case_markers

    payload = {
        "files": [
            {
                "path": "tests/test_x.py",
                "case_ids": ["TC-1"],
                "content": "# CASE_ID: TC-1 (model)\ndef test_x():\n    assert True\n",
            }
        ]
    }
    meta = {"TC-1": {"run": "r1", "req": "REQ-1", "ctl": "CTL-001", "title": "x"}}
    out = ensure_generated_case_markers(payload, meta)["files"][0]["content"]
    assert out.splitlines()[0] == "# CASE_ID: TC-1 | RUN: r1 | REQ: REQ-1 | CTL: CTL-001 | TITLE: x"
    assert out.count("CASE_ID: TC-1") == 1


def test_ctl_header_field_is_truncated_on_id_boundaries():
    ids = [f"CTL-{i:03d}" for i in range(1, 80)]
    meta = case_marker_meta({"id": "TC-1", "title": "t", "control_ids": ids}, "r1")
    kept = meta["ctl"].split(", ")
    assert len(meta["ctl"]) <= 200 and kept == ids[: len(kept)] and kept[-1].count("-") == 1


def test_case_reasons_in_the_trace_are_redacted(monkeypatch):
    import maf_test_harness.qa.controls as controls_module

    monkeypatch.setattr(controls_module, "redact", lambda text, **_: (text.replace("hunter2", "***"), 1))
    state = _state(
        [_req("REQ-1", "src/a.py")],
        [AREA],
        [_case("TC-1", "REQ-1")],
        [_test_file("TC-1", "a")],
        execution={"status": "failed", "cases": [_result("a", "failed", "login with hunter2 failed")]},
    )
    (row,) = trace_matrix(state)
    assert "hunter2" not in str(row["cases"]) and "login with ***" in row["cases"][0]["reason"]


def test_failed_step_comes_from_the_worst_outcome_not_a_milder_one():
    # TC-1 errored while running; TC-2 never ran because the environment failed. The control is an error at
    # test_execution, not "failed at environment_setup".
    cases = [_case("TC-1", "REQ-1"), _case("TC-2", "REQ-1")]
    files = [_test_file("TC-1", "a"), _test_file("TC-2", "b")]
    execution = {"status": "failed", "cases": [_result("a", "error", "boom")]}
    state = _state(
        [_req("REQ-1", "src/a.py")],
        [AREA],
        cases,
        files,
        execution=execution,
        environment={"status": "failed", "reason": "pip install failed", "steps": []},
    )
    (row,) = trace_matrix(state)
    assert row["final_status"] == "error" and row["failed_at"] == "test_execution"


def test_symbol_match_is_on_whole_words_only():
    reqs = {"requirement_model": {"requirements": [_req("REQ-1", "src/none.py", "run")]}}
    plan = {"areas": [{**AREA, "name": "A", "scenarios": ["jobs are running in the queue"]}]}
    assert assign_controls(reqs, plan)[0]["plan_area_id"] == "PLN-00"
    plan["areas"][0]["scenarios"] = ["call run to start it"]
    assert assign_controls(reqs, plan)[0]["plan_area_id"] == "PLN-01"

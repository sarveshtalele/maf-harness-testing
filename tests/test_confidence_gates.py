"""Confidence gates for planning, design and generated tests; the ledger, its API, the report and the ZIP."""

import asyncio
import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from fakes import FULL_PLAN, ScriptedStageRuntime, clone_fixture, plan_review_response
from test_traceability_controls import _case, _req, _result, _state, _test_file

import maf_test_harness.workflow as wf
from maf_test_harness.agent import prompt_settings
from maf_test_harness.qa import ledger
from maf_test_harness.qa.confidence import quote_verified, rubric_score
from maf_test_harness.qa.packaging import build_deliverable
from maf_test_harness.qa.report import build_qa_report, render_markdown
from maf_test_harness.ui.confidence_api import ConfidenceAPI
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow import Harness
from maf_test_harness.workflow_steps import plan_review
from maf_test_harness.workflow_steps.design_review import score_case
from maf_test_harness.workflow_steps.plan_review import score_area

ROOT_FILES = {"src/a.py", "src/b.py"}


class PlanReviewRuntime(ScriptedStageRuntime):
    def __init__(self, scores=None, verdict="approved"):
        super().__init__()
        self.scores = scores
        self.verdict = verdict
        self.plan_reviews = 0

    async def run_stage(self, *, stage, state, **kwargs):
        if (
            kwargs.get("response_format") is not None
            and kwargs["response_format"].__name__ == "PlanReviewDraft"
        ):
            self.plan_reviews += 1
            return plan_review_response(kwargs, verdict=self.verdict, scores=self.scores, reason="scripted")
        return await super().run_stage(stage=stage, state=state, **kwargs)


def drive(tmp_path, monkeypatch, runtime, until=("test_design",)):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    h = Harness(
        tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=runtime, execute=False
    )
    svc = RuntimeService()
    try:
        svc.submit(h.start("https://github.com/acme/demo")).result(10)
        for _ in range(30):
            pending = h.view.get("pending") or {}
            if pending.get("stage") in until or pending.get("kind") == "error":
                break
            svc.submit(h.respond(pending["id"], {"decision": "approve"})).result(60)
        return h
    finally:
        svc.close()


def pending_error(h) -> str:
    pending = h.view.get("pending") or {}
    return str(pending.get("error") or "") if pending.get("kind") == "error" else ""


# Plan gate ----------------------------------------------------------------------------------------------------------


def test_plan_gate_passes_with_full_rubric_and_records_each_area(tmp_path, monkeypatch):
    runtime = PlanReviewRuntime()
    h = drive(tmp_path, monkeypatch, runtime)
    assert pending_error(h) == "" and h.view["pending"]["stage"] == "test_design"
    plans = [e for e in ledger.entries(tmp_path) if e["kind"] == "plan"]
    assert plans and all(e["accepted"] and e["confidence"] == 100 and e["threshold"] == 95 for e in plans)


def test_plan_gate_blocks_design_after_repairs(tmp_path, monkeypatch):
    runtime = PlanReviewRuntime(scores={**FULL_PLAN, "level_fit": 0})  # 70 points: below the gate
    h = drive(tmp_path, monkeypatch, runtime)
    error = pending_error(h)
    assert error.startswith("Confidence gate: test planning 1 of 1 areas"), error
    assert h.view["pending"]["stage"] == "test_planning"
    assert "test_design" not in h.state["artifacts"]
    assert runtime.plan_reviews == 3  # three attempts, then the hard stop
    assert [e["round"] for e in ledger.entries(tmp_path) if e["kind"] == "plan"] == [1, 2, 3]


def test_plan_gate_is_advisory_when_full_accuracy_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_review, "REQUIRE_FULL_ACCURACY", False)
    runtime = PlanReviewRuntime(scores={**FULL_PLAN, "level_fit": 0})
    h = drive(tmp_path, monkeypatch, runtime)
    assert pending_error(h) == "" and h.view["pending"]["stage"] == "test_design"
    plan = [e for e in ledger.entries(tmp_path) if e["kind"] == "plan"]
    assert plan and all(e["gate"] == "advisory" and not e["accepted"] for e in plan)


def test_plan_area_caps_for_missing_evidence_uncovered_controls_and_verdicts():
    review = {"area_id": "PLN-01", "scores": dict(FULL_PLAN), "verdict": "approved", "reason": "ok"}
    area = {"area_id": "PLN-01", "evidence_files": ["nope.py"]}
    controls = [{"control_id": "CTL-001", "plan_area_id": "PLN-01", "requirement_id": "REQ-1"}]
    req_files = {"REQ-1": {"src/a.py"}}
    repo = {"src/a.py"}
    missing = score_area(review, area, controls, req_files, repo, 95)
    assert missing["confidence"] == 40 and missing["caps"][0]["rule"] == "evidence_missing"
    uncovered = score_area(review, {**area, "evidence_files": ["src/a.py"]}, [], req_files, repo, 95)
    assert uncovered["caps"][0]["rule"] == "no_controls" and uncovered["confidence"] == 60
    controls_elsewhere = score_area(
        review, {**area, "evidence_files": ["src/b.py"]}, controls, req_files, {"src/a.py", "src/b.py"}, 95
    )
    assert (
        controls_elsewhere["caps"][0]["rule"] == "controls_uncovered"
        and controls_elsewhere["confidence"] == 50
    )
    revise = score_area(
        {**review, "verdict": "revise"},
        {**area, "evidence_files": ["src/a.py"]},
        controls,
        req_files,
        repo,
        95,
    )
    assert revise["confidence"] == 79 and revise["accepted"] is False
    good = score_area(review, {**area, "evidence_files": ["src/a.py"]}, controls, req_files, repo, 95)
    assert good["confidence"] == 100 and good["accepted"] is True


# Design caps and quotes -----------------------------------------------------------------------------------------------


def test_quotes_are_whitespace_normalised_substrings_of_the_target_source():
    source = "1: def add(a, b):\n2:     return a + b\n"
    assert quote_verified("return a + b", source)
    assert quote_verified("return   a  +  b", source)
    assert not quote_verified("return a - b", source)
    assert not quote_verified("", source)


def test_design_case_caps_unresolved_target_and_unverified_numeric_expectation():
    review = {
        "case_id": "TC-1",
        "scores": {
            "source_proven": 30,
            "correct_target": 25,
            "expected_derivable": 25,
            "fixture_validity": 20,
        },
        "verdict": "approved",
        "derivation": "adds",
        "source_quote": "return a + b",
        "reason": "ok",
    }
    case = {"id": "TC-1", "target": "service.add", "expected_result": "returns 3"}
    assert score_case(review, case, "", 95)["confidence"] == 50  # target does not resolve
    numeric = {**case, "expected_result": "returns 3"}
    unverified = score_case({**review, "source_quote": "not in the source"}, numeric, "2: return a + b", 95)
    rules = {c["rule"]: c["cap"] for c in unverified["caps"]}
    assert rules == {"numeric_expected_unverified": 60} and unverified["scores"]["expected_derivable"] == 0
    assert unverified["confidence"] == 60 and unverified["accepted"] is False
    verified = score_case(review, case, "2: return a + b", 95)
    assert verified["confidence"] == 100 and verified["accepted"] is True


def test_rubric_points_are_clamped_and_verdict_caps_apply():
    scored = rubric_score(
        "design",
        {"source_proven": 99, "correct_target": 25, "expected_derivable": 25, "fixture_validity": 20},
        [],
        "reject",
    )
    assert scored["points"] == 100 and scored["confidence"] == 0


# Reviewer personas -----------------------------------------------------------------------------------------------------


class CaptureRuntime:
    def __init__(self):
        self.personas = []

    async def run_stage(self, *, stage, persona, prompt, state, role, **kwargs):
        self.personas.append((role, persona))
        return {"status": "completed", "artifact": {}}


def test_reviewer_override_replaces_its_persona_and_repository_prompt_is_never_added(tmp_path):
    prompt_settings.save(
        tmp_path / "prompts.json",
        {
            "repository_prompt": "SECRET REPOSITORY GUIDANCE",
            "personas": {"design_reviewer": "CUSTOM REVIEWER TEXT"},
        },
    )
    runtime = CaptureRuntime()
    h = Harness(tmp_path, skills_root=Path("skills"), runtime=runtime, execute=False)
    result = asyncio.run(
        h._call_model(
            stage="test_design",
            role="design_reviewer",
            persona="STACK BLOCK",
            prompt="{}",
            state={},
            persist=h.save,
            skill_name="generic-test-generator",
            companions=(),
        )
    )
    role, persona = runtime.personas[-1]
    assert role == "design_reviewer"
    assert persona.startswith("CUSTOM REVIEWER TEXT") and "STACK BLOCK" in persona
    assert "SECRET REPOSITORY GUIDANCE" not in persona
    assert result["persona_override_sha256"] == hashlib.sha256(b"CUSTOM REVIEWER TEXT").hexdigest()
    assert result["prompt_record_id"] and result["prompt_sha256"]


def test_default_reviewer_persona_is_not_marked_as_overridden_and_stage_authors_keep_guidance(tmp_path):
    prompt_settings.save(tmp_path / "prompts.json", {"repository_prompt": "SECRET REPOSITORY GUIDANCE"})
    runtime = CaptureRuntime()
    h = Harness(tmp_path, skills_root=Path("skills"), runtime=runtime, execute=False)
    common = {
        "prompt": "{}",
        "state": {},
        "persist": h.save,
        "skill_name": "generic-test-generator",
        "companions": (),
    }
    reviewed = asyncio.run(
        h._call_model(stage="test_planning", role="plan_reviewer", persona="STACK", **common)
    )
    authored = asyncio.run(h._call_model(stage="test_planning", persona="AUTHOR", **common))
    assert reviewed["persona_override_sha256"] is None
    assert "SECRET REPOSITORY GUIDANCE" not in runtime.personas[0][1]
    assert "SECRET REPOSITORY GUIDANCE" in runtime.personas[1][1]
    assert authored["persona_override_sha256"] is None and authored["prompt_record_id"]


# Ledger ----------------------------------------------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "kind",
    "subject_id",
    "control_ids",
    "batch",
    "round",
    "scores",
    "points",
    "caps",
    "notes",
    "confidence",
    "threshold",
    "gate",
    "accepted",
    "verdict",
    "reason",
    "derivations",
    "evidence",
    "prompt_record_id",
    "prompt_record_ids",
    "prompt_sha256",
    "persona_override_sha256",
    "model",
    "timestamp",
}


def test_ledger_record_has_every_traceability_field_and_counts_rounds(tmp_path):
    scored = {
        "scores": {"controls_covered": 40, "level_fit": 30, "scenarios_derivable": 30},
        "points": 100,
        "caps": [{"rule": "verdict_revise", "cap": 79, "reason": "r"}],
        "notes": [],
        "confidence": 79,
        "verdict": "revise",
        "reason": "x" * 900,
        "accepted": False,
        "derivations": [{"slot": "E1", "steps": "y" * 900}],
        "evidence": [{"quote": "src/a.py", "verified": True}],
    }
    calls = [
        {"prompt_record_id": "p1", "prompt_sha256": "s1", "persona_override_sha256": "o1", "model": "m1"}
    ]
    for _ in range(2):
        ledger.append_score(
            tmp_path,
            kind="plan",
            subject_id="PLN-01",
            control_ids=["CTL-001"],
            batch=None,
            scored=scored,
            threshold=95,
            calls=calls,
            gate="hard",
        )
    first, second = ledger.entries(tmp_path)
    assert set(first) >= REQUIRED_FIELDS
    assert (first["round"], second["round"], first["line"], second["line"]) == (1, 2, 1, 2)
    assert (
        first["prompt_record_id"] == "p1"
        and first["model"] == "m1"
        and first["persona_override_sha256"] == "o1"
    )
    assert len(first["reason"]) <= 600 and len(first["derivations"][0]["steps"]) <= ledger.TEXT_LIMIT


def test_test_generation_writes_ledger_records_for_test_plan_and_design(tmp_path, monkeypatch):
    runtime = PlanReviewRuntime()
    h = drive(tmp_path, monkeypatch, runtime, until=("test_design",))
    assert h.view["pending"]["stage"] == "test_design"
    svc = RuntimeService()
    try:
        svc.submit(h.respond(h.view["pending"]["id"], {"decision": "approve"})).result(120)
        for _ in range(20):
            pending = h.view.get("pending") or {}
            if "id" not in pending or pending.get("stage") in {"environment_setup", "test_execution"}:
                break
            if pending.get("kind") == "error":
                break
            svc.submit(h.respond(pending["id"], {"decision": "approve"})).result(120)
    finally:
        svc.close()
    kinds = {e["kind"] for e in ledger.entries(tmp_path)}
    assert {"plan", "design", "test"} <= kinds
    test_rows = [e for e in ledger.entries(tmp_path) if e["kind"] == "test"]
    assert all(e["threshold"] == 95 and e["prompt_record_id"] and e["model"] is not None for e in test_rows)


# API, report, ZIP, old runs ------------------------------------------------------------------------------------------


class ApiFixture(ConfidenceAPI):
    def __init__(self, path: Path):
        self.path = path

    def run_path(self, key):
        return self.path


def _write(run_dir: Path, *records):
    for record in records:
        ledger.append_score(
            run_dir,
            kind=record[0],
            subject_id=record[1],
            control_ids=record[2],
            batch=None,
            scored={
                "scores": {},
                "points": 100,
                "caps": [],
                "notes": [],
                "confidence": record[3],
                "verdict": "approved",
                "reason": "ok",
                "accepted": record[3] >= 95,
                "derivations": [],
                "evidence": [],
            },
            threshold=95,
            calls=[{"prompt_record_id": "p", "prompt_sha256": "s", "model": "m"}],
            gate="hard",
        )


def test_api_pages_and_filters_by_kind_and_subject(tmp_path):
    _write(tmp_path, ("plan", "PLN-01", [], 100), ("test", "TC-1", ["CTL-001"], 96), ("test", "TC-2", [], 80))
    api = ApiFixture(tmp_path)
    assert api.confidence("run")["total"] == 3
    only_tests = api.confidence("run", {"kind": ["test"]})
    assert [r["subject_id"] for r in only_tests["records"]] == ["TC-1", "TC-2"]
    by_control = api.confidence("run", {"subject": ["CTL-001"]})
    assert [r["subject_id"] for r in by_control["records"]] == ["TC-1"]
    page = api.confidence("run", {"limit": ["1"], "offset": ["1"]})
    assert page["records"][0]["subject_id"] == "TC-1" and page["total"] == 3
    with pytest.raises(ValueError):
        api.confidence("run", {"kind": ["bogus"]})


def test_api_of_a_run_without_ledger_is_empty(tmp_path):
    assert ApiFixture(tmp_path).confidence("old-run") == {
        "ledger": "confidence.jsonl",
        "total": 0,
        "offset": 0,
        "limit": 50,
        "records": [],
    }


def _report(confidence):
    reqs = {"requirement_model": {"requirements": [_req("REQ-1", "src/a.py")]}}
    state = _state(
        [_req("REQ-1", "src/a.py")],
        [
            {
                "name": "Core",
                "objective": "o",
                "level": "unit",
                "risk": "high",
                "evidence_files": ["src/a.py"],
                "scenarios": ["s"],
            }
        ],
        [_case("TC-1", "REQ-1")],
        [_test_file("TC-1", "a")],
        execution={"status": "passed", "cases": [_result("a", "passed")]},
    )
    return build_qa_report(
        repo={"source_files": []},
        requirements=reqs,
        design=state["artifacts"]["test_design"],
        generation=state["artifacts"]["test_generation"],
        execution=state["artifacts"]["test_execution"]["execution"],
        plan=state["artifacts"]["test_planning"],
        confidence=confidence,
    )


def test_report_has_a_confidence_traceability_section_with_gates_and_ledger_links(tmp_path):
    _write(tmp_path, ("plan", "PLN-01", [], 100), ("design", "TC-1", [], 97), ("test", "TC-1", [], 99))
    report = _report(ledger.entries(tmp_path))
    row = report["confidence"]["rows"][0]
    assert row["case_id"] == "TC-1" and row["test"]["link"].startswith("confidence.jsonl#L")
    assert report["confidence"]["gates"]["design"]["threshold"] == 95
    md = render_markdown(report)
    assert "## Confidence traceability" in md
    assert (
        "Confidence gates:" in md
        and "test planning >= 95" in md
        and "test design >= 95" in md
        and "generated tests >= 95" in md
    )
    json.dumps(report)  # the JSON report stays serialisable


def test_old_run_without_a_ledger_still_reports_and_says_so():
    report = _report(None)
    assert report["confidence"]["rows"] == [] and report["confidence"]["records"] == 0
    md = render_markdown(report)
    assert (
        "not recorded (run predates the confidence ledger)" in md and "## Confidence traceability" not in md
    )


def test_deliverable_zip_includes_the_ledger(tmp_path):
    _write(tmp_path, ("test", "TC-1", [], 99))
    state = {"artifacts": {"repo_analysis": {"repo_name": "demo"}}}
    with zipfile.ZipFile(build_deliverable(tmp_path, state)) as archive:
        assert "harness-report/confidence.jsonl" in archive.namelist()
        assert b'"subject_id": "TC-1"' in archive.read("harness-report/confidence.jsonl")


def test_deliverable_without_ledger_still_builds(tmp_path):
    state = {"artifacts": {"repo_analysis": {"repo_name": "demo"}}}
    with zipfile.ZipFile(build_deliverable(tmp_path, state)) as archive:
        assert "harness-report/confidence.jsonl" not in archive.namelist()


def test_persona_labels_name_the_three_reviewers():
    from maf_test_harness.agent.personas import PERSONA_LABELS, REVIEWER_ROLES

    assert PERSONA_LABELS == {
        "plan_reviewer": "Confidence reviewer (test planning)",
        "design_reviewer": "Confidence reviewer (test design)",
        "test_reviewer": "Confidence reviewer (generated tests)",
    }
    assert set(PERSONA_LABELS) == set(REVIEWER_ROLES)

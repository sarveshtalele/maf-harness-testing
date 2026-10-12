"""Confidence gates: reachable design and plan gates, recovery (repair feedback, per-run full accuracy), reviewer outage,
coverage kept through repairs, error remedies, epochs, quote strength, reviewer overrides, personas, ledger counters."""

import asyncio
import json
from pathlib import Path

import pytest
from fakes import FULL_DESIGN, FULL_PLAN, ScriptedStageRuntime
from test_confidence_gates import ApiFixture, PlanReviewRuntime, _report, drive, pending_error

from maf_test_harness import settings
from maf_test_harness.agent import prompt_settings
from maf_test_harness.agent.personas import PERSONAS
from maf_test_harness.agent.runtime import GatewayUnavailableError
from maf_test_harness.models import TestPlan
from maf_test_harness.qa import ledger
from maf_test_harness.qa.confidence import (
    ConfidenceReviewerUnavailable,
    confident_target,
    quote_verified,
)
from maf_test_harness.qa.controls import add_plan_controls
from maf_test_harness.qa.report import render_markdown
from maf_test_harness.qa.triage import remedy_for
from maf_test_harness.ui.api import _full_accuracy
from maf_test_harness.ui.errors import run_errors
from maf_test_harness.workflow import Harness
from maf_test_harness.workflow_steps import plan_review, test_review
from maf_test_harness.workflow_steps.design_review import requirement_evidence, score_case
from maf_test_harness.workflow_steps.plan_review import repository_files, score_area
from maf_test_harness.workflow_steps.planning import _design_coverage
from maf_test_harness.workflow_steps.planning_batches import plan_tests_batched

REVIEW_SCORES = dict(FULL_DESIGN)


class CapturingPlanRuntime(PlanReviewRuntime):
    def __init__(self, scores=None):
        super().__init__(scores=scores)
        self.author_prompts = []

    async def run_stage(self, *, stage, state, **kwargs):
        if stage == "test_planning" and kwargs.get("response_format") is TestPlan:
            self.author_prompts.append(kwargs["prompt"])
        return await super().run_stage(stage=stage, state=state, **kwargs)


class ReviewerDownRuntime(ScriptedStageRuntime):
    """The plan reviewer either loses its gateway or never returns a usable review."""

    def __init__(self, mode):
        super().__init__()
        self.mode = mode
        self.plan_reviews = 0

    async def run_stage(self, *, stage, state, **kwargs):
        if (
            kwargs.get("response_format") is not None
            and kwargs["response_format"].__name__ == "PlanReviewDraft"
        ):
            self.plan_reviews += 1
            if self.mode == "gateway":
                raise GatewayUnavailableError("[gateway] Connection error.")
            return {
                "status": "completed",
                "artifact": {"reviews": []},
            }  # wrong IDs: the contract repair is exhausted
        return await super().run_stage(stage=stage, state=state, **kwargs)


# 1. Design gate for non-code targets -----------------------------------------------------------------------------------

REQUIREMENTS = {
    "requirement_model": {
        "requirements": [
            {
                "id": "REQ-1",
                "text": "The checkout page shows the order total.",
                "evidence": "Spec section 2.",
            },
            {
                "id": "REQ-2",
                "text": "maf-harness run prints the run id on success.",
                "evidence": "README usage.",
            },
            {"id": "REQ-3", "text": "Unknown order ids are refused.", "evidence": "Route handler."},
        ],
        "acceptance_criteria": [],
        "validation_rules": [],
        "api_contracts": [{"method": "GET", "path": "/api/orders/{id}", "status_codes": ["200", "404"]}],
    }
}


def _review(case_id, quote, **over):
    return {
        "case_id": case_id,
        "scores": dict(REVIEW_SCORES),
        "verdict": "approved",
        "derivation": "The requirement states the behaviour.",
        "source_quote": quote,
        "reason": "ok",
        **over,
    }


@pytest.mark.parametrize(
    "case, quote, linked",
    [
        (
            {"id": "TC-UI", "target": "Checkout page", "expected_result": "The order total is shown"},
            "The checkout page shows the order total.",
            "REQ-1",
        ),
        (
            {"id": "TC-CLI", "target": "CLI: maf-harness run", "expected_result": "a run id is printed"},
            "maf-harness run prints the run id on success.",
            "REQ-2",
        ),
        (
            {"id": "TC-ROUTE", "target": "GET /api/orders/{id}", "expected_result": "404 for unknown ids"},
            "GET /api/orders/{id} 200 404",
            "REQ-3",
        ),
    ],
)
def test_non_code_targets_reach_100_with_a_verified_evidence_quote(case, quote, linked):
    assert not confident_target(case["target"])  # no code symbol: no target cap applies
    evidence = requirement_evidence(REQUIREMENTS, {linked})
    scored = score_case(_review(case["id"], quote), case, "", 95, evidence=evidence)
    assert scored["confidence"] == 100 and scored["accepted"] is True
    assert not [c for c in scored["caps"] if c["rule"] == "target_unresolved"]


@pytest.mark.parametrize(
    "case, linked",
    [
        ({"id": "TC-UI", "target": "Checkout page", "expected_result": "shows"}, "REQ-1"),
        ({"id": "TC-CLI", "target": "CLI: maf-harness run", "expected_result": "a run id"}, "REQ-2"),
        ({"id": "TC-ROUTE", "target": "GET /api/orders/{id}", "expected_result": "not found"}, "REQ-3"),
    ],
)
def test_non_code_targets_without_a_verified_quote_are_capped_below_the_gate(case, linked):
    evidence = requirement_evidence(REQUIREMENTS, {linked})
    scored = score_case(
        _review(case["id"], "invented sentence not in any evidence"), case, "", 95, evidence=evidence
    )
    assert (
        scored["confidence"] == 75 and scored["accepted"] is False
    )  # expected_derivable is unproven: 0 of 25
    assert scored["scores"]["expected_derivable"] == 0
    assert scored["caps"] == []  # no target cap for a non-code target


def test_code_target_still_needs_its_source_and_a_missing_source_is_capped():
    case = {"id": "TC-1", "target": "service.add", "expected_result": "returns 3"}
    scored = score_case(_review("TC-1", "return a + b"), case, "", 95, evidence="return a + b")
    assert scored["confidence"] == 50 and [c["rule"] for c in scored["caps"]] == ["target_unresolved"]


def test_requirement_evidence_is_limited_to_the_linked_requirements_and_contracts():
    text = requirement_evidence(REQUIREMENTS, {"REQ-1"})
    assert "The checkout page shows the order total." in text and "Unknown order ids" not in text
    assert "GET /api/orders/{id} 200 404" in text  # API contracts are approved evidence for any linked case


# 2. Plan gate reachability -----------------------------------------------------------------------------------------------

PLAN_STATE = {
    "artifacts": {
        "repo_analysis": {
            "tracked_files": [
                "src/api/orders.py",
                "src/api/users.py",
                "config/app.yaml",
                "pyproject.toml",
                "docs/security.md",
                "tests/test_orders.py",
            ],
            "source_files": ["src/api/orders.py", "src/api/users.py"],
            "test_files": ["tests/test_orders.py"],
            "package_files": ["pyproject.toml"],
        },
        "requirements": {
            "requirement_model": {
                "requirements": [
                    {"id": "REQ-1", "source_files": ["src/api/orders.py"]},
                    {"id": "REQ-2", "source_files": ["src/api/users.py"]},
                    {"id": "REQ-3", "source_files": ["config/app.yaml"]},
                ]
            }
        },
    }
}


def test_evidence_may_be_any_tracked_repository_file():
    files = repository_files(PLAN_STATE)
    assert {"config/app.yaml", "pyproject.toml", "docs/security.md"} <= files
    area = {"area_id": "PLN-09", "evidence_files": ["pyproject.toml"]}
    review = {"area_id": "PLN-09", "scores": dict(FULL_PLAN), "verdict": "approved", "reason": "ok"}
    scored = score_area(review, area, [], {}, files, 95)
    assert "evidence_missing" not in [c["rule"] for c in scored["caps"]]
    assert scored["evidence"] == [{"quote": "pyproject.toml", "exists": True}]


def test_realistic_multi_area_plan_with_configs_and_a_cross_cutting_area_passes():
    artifact = {
        "areas": [
            {
                "area_id": "PLN-01",
                "level": "unit",
                "evidence_files": ["src/api/orders.py", "tests/test_orders.py"],
            },
            {
                "area_id": "PLN-02",
                "level": "integration",
                "evidence_files": ["config/app.yaml", "pyproject.toml"],
            },
            {"area_id": "PLN-03", "level": "unit", "evidence_files": ["src/api/orders.py"]},
            {"area_id": "PLN-04", "level": "security", "evidence_files": ["docs/security.md"]},
        ],
        "controls": [
            {"control_id": "CTL-001", "plan_area_id": "PLN-01", "requirement_id": "REQ-1"},
            {"control_id": "CTL-002", "plan_area_id": "PLN-02", "requirement_id": "REQ-3"},
            {"control_id": "CTL-003", "plan_area_id": "PLN-03", "requirement_id": "REQ-2"},  # same directory
        ],
    }
    req_files = plan_review._requirement_files(PLAN_STATE)
    files = repository_files(PLAN_STATE)
    results = [
        score_area(
            {"area_id": a["area_id"], "scores": dict(FULL_PLAN), "verdict": "approved", "reason": "ok"},
            a,
            artifact["controls"],
            req_files,
            files,
            95,
        )
        for a in artifact["areas"]
    ]
    assert all(r["accepted"] and r["confidence"] == 100 for r in results), [
        (r["area_id"], r["caps"]) for r in results
    ]
    security = results[3]
    assert security["caps"] == [] and [n["rule"] for n in security["notes"]] == ["no_controls"]


# 3. Recovery ------------------------------------------------------------------------------------------------------------


def test_gate_error_lists_the_three_options(tmp_path):
    message = plan_review.gate_message(
        [{"area_id": "PLN-01", "confidence": 70, "verdict": "revise", "reason": "r"}], 1, {}
    )
    assert "(1) revise the plan" in message
    assert "(2) edit the test planning reviewer prompt" in message
    assert "(3) start the run with full accuracy off" in message


def test_full_accuracy_resolves_per_run_then_environment(monkeypatch):
    monkeypatch.setenv("HARNESS_REQUIRE_FULL_ACCURACY", "1")
    assert settings.full_accuracy({}) is True and settings.full_accuracy(None) is True
    assert settings.full_accuracy({"require_full_accuracy": False}) is False
    monkeypatch.setenv("HARNESS_REQUIRE_FULL_ACCURACY", "0")
    assert settings.full_accuracy({}) is False
    assert settings.full_accuracy({"require_full_accuracy": True}) is True
    assert plan_review.gate_on({"require_full_accuracy": False}) is False
    assert _full_accuracy(None) is None and _full_accuracy(False) is False
    with pytest.raises(ValueError):
        _full_accuracy("no")


def test_retry_after_a_gate_failure_repairs_with_the_reviewer_feedback(tmp_path, monkeypatch):
    from maf_test_harness.ui.runtime_service import RuntimeService

    runtime = CapturingPlanRuntime(scores={**FULL_PLAN, "level_fit": 0})
    h = drive(tmp_path, monkeypatch, runtime)
    assert pending_error(h).startswith("Confidence gate: test planning")
    kept = h.state["gate_feedback"]["test_planning"]
    assert "Independent confidence review flagged these plan areas" in kept
    first_prompt = runtime.author_prompts[0]
    runtime.scores = None  # the reviewer now approves, so the retry can pass
    svc = RuntimeService()
    try:
        svc.submit(h.respond(h.view["pending"]["id"], {"decision": "retry"})).result(120)
    finally:
        svc.close()
    retry_prompt = runtime.author_prompts[-1]
    assert retry_prompt != first_prompt
    assert kept in retry_prompt  # the kept feedback is the repair of the retried attempt
    assert "test_planning" not in h.state.get("gate_feedback", {})  # cleared once the stage passed


# 4. Reviewer outage -----------------------------------------------------------------------------------------------------


def test_gateway_outage_of_a_reviewer_stops_at_once_without_author_repairs(tmp_path, monkeypatch):
    runtime = ReviewerDownRuntime("gateway")
    h = drive(tmp_path, monkeypatch, runtime)
    error = pending_error(h)
    assert error.startswith("Confidence reviewer unavailable: test planning gateway unreachable"), error
    assert runtime.calls.count("test_planning") == 1  # the author was not asked again
    assert runtime.plan_reviews == 1


def test_exhausted_reviewer_contract_repair_is_a_reviewer_outage(tmp_path, monkeypatch):
    runtime = ReviewerDownRuntime("contract")
    h = drive(tmp_path, monkeypatch, runtime)
    assert pending_error(h).startswith(
        "Confidence reviewer unavailable: test planning returned no usable review"
    )
    assert runtime.calls.count("test_planning") == 1


def test_reviewer_outage_is_advisory_when_full_accuracy_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_REQUIRE_FULL_ACCURACY", "0")
    h = drive(tmp_path, monkeypatch, ReviewerDownRuntime("gateway"))
    assert pending_error(h) == "" and h.view["pending"]["stage"] == "test_design"
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "plan_review_advisory" in audit


def test_test_reviewer_outage_raises_the_distinct_error_only_when_the_gate_is_on(tmp_path):
    class Host:
        run_dir = tmp_path

        def audit(self, event, **details):
            pass

        def save(self):
            pass

    error = ConfidenceReviewerUnavailable("Confidence reviewer unavailable: generated tests down")
    with pytest.raises(ConfidenceReviewerUnavailable):
        test_review._reviewer_down(Host(), {"require_full_accuracy": True}, 1, error)
    assert test_review._reviewer_down(Host(), {"require_full_accuracy": False}, 1, error) == []


def test_errors_tab_classifies_outages_and_gate_failures_with_a_remedy(tmp_path):
    outage = "Confidence reviewer unavailable: test planning gateway unreachable: [gateway] Connection error."
    gate = "Confidence gate: test design 1 of 1 cases are below the 95 confidence gate."
    view = {"pending": {"error": outage, "stage": "test_planning"}}
    items = run_errors(tmp_path, view)["items"]
    assert items[0]["category"] == "confidence_reviewer_unavailable"
    assert items[0]["remedy"].startswith("A confidence reviewer") and "No author repair" in items[0]["remedy"]
    gate_items = run_errors(tmp_path, {"pending": {"error": gate, "stage": "test_design"}})["items"]
    assert (
        gate_items[0]["category"] == "confidence_gate"
        and "HARNESS_REQUIRE_FULL_ACCURACY=0" in gate_items[0]["remedy"]
    )
    assert remedy_for(outage) == items[0]["remedy"]


def test_report_names_disabled_design_review_instead_of_not_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DESIGN_REVIEW", "0")
    md = render_markdown(_report(None))
    assert "test design: design review disabled" in md
    assert "test design: not recorded" not in md
    monkeypatch.setenv("HARNESS_DESIGN_REVIEW", "1")
    assert "test design: not recorded" in render_markdown(_report(None))


# 5. Repairs keep coverage ------------------------------------------------------------------------------------------------


def test_design_coverage_is_the_set_of_linked_requirements_and_controls():
    design = {
        "positive_tests": [
            {
                "id": "TC-1",
                "traceability_links": [{"type": "requirement", "id": "REQ-1"}],
                "control_ids": ["CTL-001"],
            }
        ],
        "negative_tests": [
            {"id": "TC-2", "traceability_links": [{"type": "requirement", "id": "REQ-2"}], "control_ids": []}
        ],
    }
    assert _design_coverage(design) == {"link:REQ-1", "link:REQ-2", "control:CTL-001"}
    assert _design_coverage({"positive_tests": [{"id": "TC-3"}]}) == set()


# 6. Ledger: epochs, counters, streaming -----------------------------------------------------------------------------------


def _record(run_dir, kind, subject, confidence, epoch=0, batch=None):
    scored = {
        "scores": {},
        "points": confidence,
        "caps": [],
        "notes": [],
        "confidence": confidence,
        "verdict": "approved",
        "reason": "ok",
        "accepted": confidence >= 95,
        "derivations": [],
        "evidence": [{"quote": "src/a.py", "exists": True}],
    }
    return ledger.append_score(
        run_dir,
        kind=kind,
        subject_id=subject,
        control_ids=[],
        batch=batch,
        scored=scored,
        threshold=95,
        calls=[{"prompt_record_id": "p", "prompt_sha256": "s", "model": "m"}],
        gate="hard",
        epoch=epoch,
    )


def test_latest_epoch_is_the_default_and_all_keeps_the_history(tmp_path):
    _record(tmp_path, "test", "TC-REMOVED", 99, epoch=0)
    _record(tmp_path, "test", "TC-KEPT", 97, epoch=1)
    api = ApiFixture(tmp_path)
    assert [r["subject_id"] for r in api.confidence("run")["records"]] == ["TC-KEPT"]
    assert [r["subject_id"] for r in api.confidence("run", {"epoch": ["all"]})["records"]] == [
        "TC-REMOVED",
        "TC-KEPT",
    ]
    assert [r["subject_id"] for r in api.confidence("run", {"epoch": ["0"]})["records"]] == ["TC-REMOVED"]
    with pytest.raises(ValueError):
        api.confidence("run", {"epoch": ["newest"]})
    assert _record(tmp_path, "test", "TC-KEPT", 98, epoch=1)["round"] == 2  # rounds count inside one epoch
    assert _record(tmp_path, "test", "TC-KEPT", 98, epoch=2)["round"] == 1


def test_report_uses_only_the_latest_epoch(tmp_path):
    _record(tmp_path, "plan", "PLN-01", 100, epoch=0)
    _record(tmp_path, "plan", "PLN-01", 100, epoch=1)
    report = _report(ledger.entries(tmp_path))
    assert report["confidence"]["records"] == 1


def test_round_counter_is_rebuilt_from_the_file_and_entries_stream_with_a_cap(tmp_path):
    for _ in range(2):
        _record(tmp_path, "design", "TC-1", 96)
    ledger._rounds.clear()  # a fresh process: the counter is rebuilt once from the file
    ledger._counted_runs.clear()
    assert _record(tmp_path, "design", "TC-1", 96)["round"] == 3
    capped = ledger.entries(tmp_path, limit=2)
    assert [r["line"] for r in capped] == [2, 3]


def test_plan_scores_of_batches_are_recorded_against_the_final_merged_area_ids(tmp_path, monkeypatch):
    class BatchHost:
        def __init__(self):
            self.run_dir = tmp_path
            self.events = []

        async def _model_artifact(self, stage, state, subset, prompt_batch):
            plan = {
                "objective": "o",
                "recommended_frameworks": ["pytest"],
                "setup_actions": [],
                "areas": [
                    {
                        "area_id": f"PLN-0{n}",
                        "name": f"b{prompt_batch} area {n}",
                        "objective": "o",
                        "level": "unit",
                        "risk": "low",
                        "evidence_files": [f"pkg{prompt_batch}/a{n}.py"],
                        "scenarios": ["s"],
                    }
                    for n in (1, 2)
                ],
                "execution_order": ["x"],
                "assumptions": [],
                "gaps": [],
            }
            for area in plan["areas"]:  # the reviewer's record, written under the batch-local ID
                _record(self.run_dir, "plan", area["area_id"], 100, batch=prompt_batch)
            return plan

        async def validate(self, stage, state):
            draft = {k: v for k, v in state["draft"].items() if k != "controls"}
            draft["areas"] = [
                {k: v for k, v in a.items() if k not in {"area_id", "origins"}} for a in draft["areas"]
            ]
            plan = TestPlan.model_validate(draft).model_dump()
            return add_plan_controls(plan, {"requirement_model": {"requirements": []}})

        def audit(self, event, **details):
            self.events.append(event)

        def save(self):
            pass

    from test_planning_batches import _reqs  # the planning batch fixture: three batches for 81 requirements

    host = BatchHost()
    state = {"artifacts": {"requirements": _reqs(81)}}
    plan = asyncio.run(plan_tests_batched(host, state))
    final = [a["area_id"] for a in plan["areas"]]
    plan_records = [e for e in ledger.entries(tmp_path) if e["kind"] == "plan"]
    subjects = [e["subject_id"] for e in plan_records]
    assert set(subjects) == set(final) and len(final) == len(set(final)) == 6
    assert all(not s.startswith("batch") for s in subjects)
    # Batch 3's PLN-02 became the sixth final area: its record moved there and keeps its batch-local ID.
    moved = [e for e in plan_records if e["batch"] == 3 and e["local_subject_id"] == "PLN-02"]
    assert moved and moved[0]["subject_id"] == "PLN-06"


# 7. Quote strength -------------------------------------------------------------------------------------------------------


def test_trivial_quotes_are_unverified_and_full_lines_or_long_substrings_verify():
    source = "1: def f(a):\n2:     if a:\n3:         return 0\n"
    for trivial in ("a", "0", "if"):
        assert not quote_verified(trivial, source), trivial
    assert quote_verified("return 0", source)  # an exact full source line
    assert quote_verified("def f(a):", source)
    assert quote_verified(
        "def f(a): if a:", source
    )  # 12 non-space characters, a verbatim substring across lines
    assert not quote_verified("if a: return 0", source)  # 11 non-space characters and not a full line


# 8. Reviewer overrides ---------------------------------------------------------------------------------------------------


def test_whole_prompt_overrides_cannot_target_a_reviewer(tmp_path):
    for key in (
        "design_reviewer",
        "plan_reviewer:1",
        "test_generation:review-1",
        "test_planning:plan-review-2",
    ):
        with pytest.raises(ValueError):
            prompt_settings.validate({"overrides": {key: "replacement prompt"}})
    accepted = prompt_settings.validate({"overrides": {"test_generation:1": "replacement prompt"}})
    assert accepted["overrides"] == {"test_generation:1": "replacement prompt"}


class PromptRuntime:
    def __init__(self):
        self.prompts = []

    async def run_stage(self, *, stage, persona, prompt, state, role, **kwargs):
        self.prompts.append(prompt)
        return {"status": "completed", "artifact": {}}


def test_a_legacy_reviewer_override_is_never_applied_to_a_reviewer_call(tmp_path):
    (tmp_path / "prompts.json").write_text(
        json.dumps(
            {
                "repository_prompt": "",
                "personas": {},
                "overrides": {"test_design:review-1": "REPLACED REVIEWER PROMPT"},
            }
        ),
        encoding="utf-8",
    )
    runtime = PromptRuntime()
    h = Harness(tmp_path, skills_root=Path("skills"), runtime=runtime, execute=False)
    asyncio.run(
        h._call_model(
            stage="test_design",
            role="design_reviewer",
            persona="STACK",
            prompt="HOST PROMPT",
            state={},
            persist=h.save,
            skill_name="generic-test-generator",
            companions=(),
            prompt_batch="review-1",
        )
    )
    assert runtime.prompts == ["HOST PROMPT"]


# 9. Custom personas and names --------------------------------------------------------------------------------------------


def test_a_custom_persona_file_without_reviewer_keys_gets_the_builtin_reviewer_texts(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt_settings.paths, "prompts_dir", lambda: tmp_path)
    (tmp_path / "personas.json").write_text(json.dumps({"test_planning": "CUSTOM PLANNER"}), encoding="utf-8")
    personas = prompt_settings.defaults()["personas"]
    assert personas["test_planning"] == "CUSTOM PLANNER"
    for reviewer in ("plan_reviewer", "design_reviewer", "test_reviewer"):
        assert personas[reviewer] == PERSONAS[reviewer].instructions()
    assert prompt_settings.validate({"personas": {"test_planning": "CUSTOM PLANNER"}})["personas"][
        "design_reviewer"
    ]


def test_test_reviewer_persona_title_is_the_confidence_reviewer_for_generated_tests():
    assert PERSONAS["test_reviewer"].name == "Confidence Reviewer: generated tests"


# 10. Rerun epoch and in-loop coverage rejection ---------------------------------------------------------------------------


def test_rerun_from_a_step_increments_the_ledger_epoch(tmp_path, monkeypatch):
    from maf_test_harness.ui.runtime_service import RuntimeService

    runtime = PlanReviewRuntime()
    h = drive(tmp_path, monkeypatch, runtime)
    assert h.view["pending"]["stage"] == "test_design"
    before = int(h.state.get("ledger_epoch") or 0)
    svc = RuntimeService()
    try:
        svc.submit(h.rerun("test_planning", h.view["pending"]["id"])).result(300)
    finally:
        svc.close()
    assert int(h.state["ledger_epoch"]) == before + 1
    assert int(h.view["state"]["ledger_epoch"]) == before + 1  # persisted with the run view


def _case(title, req_id):
    return {
        "title": title,
        "priority": "P1",
        "level": "unit",
        "actor": "Developer",
        "target": "service.add",
        "description": "Two integers are added.",
        "preconditions": [],
        "data_notes": "1 and 2",
        "expected_result": "add(1, 2) returns 3",
        "steps": ["Call add(1, 2)", "Compare with 3"],
        "traceability_links": [{"type": "requirement", "id": req_id}],
    }


def test_design_repair_that_drops_a_covered_requirement_is_rejected_in_the_loop(tmp_path, monkeypatch):
    from fakes import design_review_response

    from maf_test_harness.models import DesignReviewDraft, TestDesignDraft

    class DroppingRuntime(ScriptedStageRuntime):
        """Two requirements. The first design covers both; every repair keeps REQ-2 only and excludes REQ-1 by a gap."""

        def __init__(self):
            super().__init__()
            self.design_authors = 0
            self.revise = False
            self.drop = False

        async def run_stage(self, *, stage, state, **kwargs):
            if stage == "requirements" and kwargs.get("response_format") is not None:
                result = await super().run_stage(stage=stage, state=state, **kwargs)
                result["artifact"]["requirements"].append(
                    {
                        "text": "add rejects nothing and returns a number",
                        "evidence": "service.py defines add(a, b) as a + b",
                        "source_files": ["service.py"],
                        "category": "functional",
                        "actor": "Developer",
                    }
                )
                return result
            if kwargs.get("response_format") is DesignReviewDraft and self.revise:
                return design_review_response(
                    kwargs,
                    verdict="revise",
                    reason="Scripted revision.",
                    scores=dict.fromkeys(FULL_DESIGN, 10),
                )
            if kwargs.get("response_format") is TestDesignDraft:
                ids = [
                    r["id"] for r in state["artifacts"]["requirements"]["requirement_model"]["requirements"]
                ]
                self.design_authors += 1
                if self.drop and self.design_authors > 1:
                    self.calls.append(stage)
                    return {
                        "status": "completed",
                        "artifact": {
                            "positive_tests": [_case("add keeps second requirement", ids[1])],
                            "negative_tests": [],
                            "boundary_tests": [],
                            "validation_tests": [],
                            "contract_tests": [],
                            "gaps": [f"{ids[0]} excluded: scripted repair drops its case."],
                        },
                    }
                self.calls.append(stage)
                return {
                    "status": "completed",
                    "artifact": {
                        "positive_tests": [_case("add returns sum", ids[0])],
                        "negative_tests": [_case("add keeps second requirement", ids[1])],
                        "boundary_tests": [],
                        "validation_tests": [],
                        "contract_tests": [],
                        "gaps": [],
                    },
                }
            return await super().run_stage(stage=stage, state=state, **kwargs)

    runtime = DroppingRuntime()
    h = drive(tmp_path, monkeypatch, runtime)
    assert h.view["pending"]["stage"] == "test_design", pending_error(h)
    runtime.revise, runtime.drop, runtime.design_authors = True, True, 0
    with pytest.raises(ValueError, match="Keep or replace, do not delete"):
        asyncio.run(h._model_artifact("test_design", h.state))
    assert (
        runtime.design_authors == 3
    )  # attempt 1 covered both; attempts 2 and 3 dropped REQ-1 and were not accepted
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "design_repair_dropped_coverage" in audit

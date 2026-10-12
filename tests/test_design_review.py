import json
from pathlib import Path

from fakes import FULL_DESIGN, ScriptedStageRuntime, clone_fixture, design_review_response

from maf_test_harness.agent.personas import chief_architect_persona
from maf_test_harness.qa import ledger
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow import Harness


def test_persona_contains_only_detected_stack_and_is_byte_stable():
    stack = {"ecosystems": ["python", "node"], "test_runner": "pytest", "web_ui": True}
    text = chief_architect_persona(stack, {"Python": 9, "TypeScript": 4})
    assert "Confidence Reviewer: generated tests" in text and len(text) < 2500
    assert "pytest fixtures" in text and "jest/vitest" in text and "UI:" in text
    assert "JUnit" not in text and "xUnit" not in text and "API:" not in text
    assert text == chief_architect_persona(dict(stack), {"Python": 9, "TypeScript": 4})
    assert "STACK EXPERTISE" not in chief_architect_persona({}, [])
    assert "JUnit" in chief_architect_persona({"ecosystems": ["maven"]}, ["Java"])


class DesignReviewRuntime(ScriptedStageRuntime):
    def __init__(self, scores=None, verdict="approved", quote=None):
        super().__init__()
        self.kwargs = dict(scores=scores, verdict=verdict, quote=quote)
        self.reviews = 0
        self.design_prompts = []

    async def run_stage(self, *, stage, state, **kwargs):
        fmt = kwargs.get("response_format")
        if fmt is not None and fmt.__name__ == "DesignReviewDraft":
            self.reviews += 1
            return design_review_response(
                kwargs,
                reason="Target is wrong." if self.kwargs["verdict"] == "revise" else "ok",
                **self.kwargs,
            )
        if stage == "test_design":
            self.design_prompts.append(kwargs["prompt"])
        return await super().run_stage(stage=stage, state=state, **kwargs)


def gate_error(h) -> str:
    """The hard stop of the design stage (a pending error), or "" when design reached its review."""
    pending = h.view.get("pending") or {}
    return str(pending.get("error") or "") if pending.get("kind") == "error" else ""


def run_design(tmp_path, monkeypatch, runtime):
    import maf_test_harness.workflow as wf

    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    h = Harness(
        tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=runtime, execute=False
    )
    svc = RuntimeService()
    try:
        svc.submit(h.start("https://github.com/acme/demo")).result(10)
        while h.view["pending"]["stage"] != "test_design":
            svc.submit(h.respond(h.view["pending"]["id"], {"decision": "approve"})).result(30)
        return h
    finally:
        svc.close()


def test_full_rubric_passes_the_gate_and_is_recorded(tmp_path, monkeypatch):
    runtime = DesignReviewRuntime()
    h = run_design(tmp_path, monkeypatch, runtime)
    design = h.state["artifacts"]["test_design"]
    case = design["positive_tests"][0]
    assert (case["confidence"], case["reviewer_verdict"]) == (100, "approved")
    assert design["design_reviews"][0]["case_id"] == case["id"]
    assert runtime.reviews == 1 and len(runtime.design_prompts) == 1
    entry = next(e for e in ledger.entries(tmp_path) if e["kind"] == "design")
    assert (entry["subject_id"], entry["accepted"], entry["threshold"], entry["gate"]) == (
        case["id"],
        True,
        95,
        "hard",
    )
    assert entry["prompt_record_id"] and entry["prompt_sha256"] and entry["model"] is not None
    assert entry["control_ids"] == case["control_ids"]


def test_points_below_the_gate_repair_then_block_generation(tmp_path, monkeypatch):
    low = {**FULL_DESIGN, "source_proven": 20}  # 90 points, below the default 95 gate
    runtime = DesignReviewRuntime(scores=low)
    h = run_design(tmp_path, monkeypatch, runtime)
    assert gate_error(h).startswith("Confidence gate: test design 1 of 1 cases")
    assert runtime.reviews == 3 and len(runtime.design_prompts) == 3  # three attempts, then the hard stop
    assert "confidence 90" in json.loads(runtime.design_prompts[1])["repair_feedback"]
    design_rounds = [e["round"] for e in ledger.entries(tmp_path) if e["kind"] == "design"]
    assert design_rounds == [1, 2, 3]  # every review pass is a score; the rounds count them


def test_revise_verdict_caps_at_79_and_reject_at_zero(tmp_path, monkeypatch):
    runtime = DesignReviewRuntime(verdict="revise")
    assert gate_error(run_design(tmp_path, monkeypatch, runtime)).startswith("Confidence gate: test design")
    entry = [e for e in ledger.entries(tmp_path) if e["kind"] == "design"][-1]
    assert entry["confidence"] == 79 and entry["caps"][0]["rule"] == "verdict_revise"
    assert entry["accepted"] is False


def test_unverified_numeric_expectation_is_capped_at_60(tmp_path, monkeypatch):
    runtime = DesignReviewRuntime(quote="this line is not in the target source")
    assert gate_error(run_design(tmp_path, monkeypatch, runtime)).startswith("Confidence gate: test design")
    last = [e for e in ledger.entries(tmp_path) if e["kind"] == "design"][-1]
    assert {c["rule"]: c["cap"] for c in last["caps"]}.get("numeric_expected_unverified") == 60
    assert last["confidence"] == 60


def test_per_run_threshold_above_the_rubric_blocks(tmp_path, monkeypatch):
    runtime = DesignReviewRuntime()  # 100 points
    monkeypatch.setattr(
        "maf_test_harness.workflow_steps.design_review.min_confidence", lambda state=None: 101
    )
    assert gate_error(run_design(tmp_path, monkeypatch, runtime)).startswith("Confidence gate: test design")


def test_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DESIGN_REVIEW", "0")
    runtime = DesignReviewRuntime()
    h = run_design(tmp_path, monkeypatch, runtime)
    assert (
        runtime.reviews == 0 and "confidence" not in h.state["artifacts"]["test_design"]["positive_tests"][0]
    )

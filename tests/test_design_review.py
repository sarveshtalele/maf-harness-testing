import json
from pathlib import Path

from fakes import ScriptedStageRuntime, clone_fixture, design_review_response

from maf_test_harness.agent.personas import chief_architect_persona
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow import Harness


def test_persona_contains_only_detected_stack_and_is_byte_stable():
    stack = {"ecosystems": ["python", "node"], "test_runner": "pytest", "web_ui": True}
    text = chief_architect_persona(stack, {"Python": 9, "TypeScript": 4})
    assert "Chief Principal Architect" in text and len(text) < 2500
    assert "pytest fixtures" in text and "jest/vitest" in text and "UI:" in text
    assert "JUnit" not in text and "xUnit" not in text and "API:" not in text
    assert text == chief_architect_persona(dict(stack), {"Python": 9, "TypeScript": 4})
    assert "STACK EXPERTISE" not in chief_architect_persona({}, [])
    assert "JUnit" in chief_architect_persona({"ecosystems": ["maven"]}, ["Java"])


class DesignReviewRuntime(ScriptedStageRuntime):
    def __init__(self, score):
        super().__init__()
        self.score = score
        self.reviews = 0
        self.design_prompts = []

    async def run_stage(self, *, stage, state, **kwargs):
        fmt = kwargs.get("response_format")
        if fmt is not None and fmt.__name__ == "DesignReviewDraft":
            self.reviews += 1
            return design_review_response(
                kwargs,
                confidence=self.score,
                verdict="approved" if self.score >= 60 else "revise",
                reason="ok" if self.score >= 60 else "Target is wrong.",
            )
        if stage == "test_design":
            self.design_prompts.append(kwargs["prompt"])
        return await super().run_stage(stage=stage, state=state, **kwargs)


def run_design(tmp_path, monkeypatch, runtime, confidence_thresholds=None):
    import maf_test_harness.workflow as wf

    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    h = Harness(
        tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=runtime, execute=False
    )
    svc = RuntimeService()
    try:
        svc.submit(
            h.start("https://github.com/acme/demo", confidence_thresholds=confidence_thresholds)
        ).result(10)
        while h.view["pending"]["stage"] != "test_design":
            svc.submit(h.respond(h.view["pending"]["id"], {"decision": "approve"})).result(30)
        return h.state["artifacts"]["test_design"]
    finally:
        svc.close()


def test_approved_case_gets_confidence(tmp_path, monkeypatch):
    runtime = DesignReviewRuntime(97)  # 97 clears the default hard gate (95): no repair pass
    design = run_design(tmp_path, monkeypatch, runtime)
    case = design["positive_tests"][0]
    assert (case["confidence"], case["reviewer_verdict"]) == (97, "approved")
    assert design["design_reviews"][0]["case_id"] == case["id"]
    assert runtime.reviews == 1 and len(runtime.design_prompts) == 1


def test_a_per_run_design_gate_is_effective(tmp_path, monkeypatch):
    """97 passes the default 95 gate but not a run that asked for 99: the architect's review repairs the case."""
    runtime = DesignReviewRuntime(97)
    run_design(tmp_path, monkeypatch, runtime, confidence_thresholds={"design": 99})
    assert len(runtime.design_prompts) == 2 and runtime.reviews == 2


def test_low_confidence_repairs_once_with_reason_then_marks(tmp_path, monkeypatch):
    runtime = DesignReviewRuntime(30)
    design = run_design(tmp_path, monkeypatch, runtime)
    assert len(runtime.design_prompts) == 2 and runtime.reviews == 2  # one repair, then keep and mark
    assert "Target is wrong." in json.loads(runtime.design_prompts[1])["repair_feedback"]
    case = design["positive_tests"][0]
    assert case["confidence"] == 30 and case["reviewer_verdict"] == "revise"
    assert case["reviewer_reason"] == "Target is wrong."


def test_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DESIGN_REVIEW", "0")
    runtime = DesignReviewRuntime(10)
    design = run_design(tmp_path, monkeypatch, runtime)
    assert runtime.reviews == 0 and "confidence" not in design["positive_tests"][0]

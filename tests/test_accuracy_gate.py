import asyncio
import json
import os
import re
from pathlib import Path

import pytest
from fakes import FULL_MARKS, ScriptedStageRuntime, clone_fixture, review_response

from maf_test_harness.qa.controls import _case_outcome
from maf_test_harness.qa.report import build_qa_report, render_markdown
from maf_test_harness.qa.triage import remedy_for
from maf_test_harness.ui.errors import categorize
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow import Harness
from maf_test_harness.workflow_steps import generation as generation_steps
from maf_test_harness.workflow_steps.test_review import review_generated_tests

PY_OK = "from service import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
BAD_CASE = {
    "title": "bad case",
    "priority": "P2",
    "level": "unit",
    "actor": "Developer",
    "target": "service.add",
    "description": "Checks a length instead of add.",
    "preconditions": [],
    "data_notes": "none",
    "expected_result": "length is 2",
    "steps": ["Check the length of a string"],
}


class ReviewHost:
    """Minimal host for review_generated_tests: returns one scripted review per supplied case."""

    def __init__(
        self, tmp_path, scores, confidence=95, verdict="supported", reason="Scripted reviewer reason."
    ):
        self.run_dir = tmp_path
        self.review = {"scores": scores, "confidence": confidence, "verdict": verdict, "reason": reason}

    async def _call_model(self, **kwargs):
        cases = json.loads(kwargs["prompt"])["approved_inputs"]["approved_cases"]
        return {
            "status": "completed",
            "artifact": {
                "reviews": [
                    {"case_id": c["id"], **self.review, "source_files": ["service.py"]} for c in cases
                ]
            },
        }

    def audit(self, *args, **kwargs):
        pass

    def save(self):
        pass


def review_fixture(tmp_path):
    (tmp_path / "repo").mkdir(exist_ok=True)
    (tmp_path / "repo" / "service.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    candidate = {"files": [{"path": "tests/test_add.py", "case_ids": ["TC-1"], "content": PY_OK}]}
    return candidate, [{"id": "TC-1", "target": "service.add"}]


REVIEW_STATE = {"artifacts": {"requirements": {}, "repo_analysis": {"source_files": ["service.py"]}}}


def scores(**changes):
    return {**FULL_MARKS, **changes}


def review(tmp_path, scores_, **kwargs):
    candidate, cases = review_fixture(tmp_path)
    host = ReviewHost(tmp_path, scores_, **kwargs)
    return asyncio.run(review_generated_tests(host, REVIEW_STATE, candidate, cases, 1))


def test_supported_review_with_a_score_below_the_threshold_is_rejected_with_the_weakest_dimension(tmp_path):
    low = scores(
        source_and_api_match=20,
        assertion_checks_real_behavior=25,
        fixture_validity=15,
        isolation_and_determinism=5,
        traceability=10,
    )  # 75 in total
    with pytest.raises(ValueError, match="Independent test review requires repair") as exc:
        review(tmp_path, low)
    message = str(exc.value)
    assert "TC-1: confidence 75 (threshold 80, verdict supported)" in message
    assert "weakest dimensions: isolation_and_determinism 5/15" in message
    assert "reviewer: Scripted reviewer reason." in message


def test_supported_95_with_target_not_invoked_is_capped_to_40_and_rejected(tmp_path):
    candidate, cases = review_fixture(tmp_path)
    candidate["files"][0]["content"] = "def test_other():\n    assert len('ab') == 2\n"
    host = ReviewHost(tmp_path, scores(traceability=5))  # 95 in total
    with pytest.raises(ValueError, match=r"confidence 40 .*target_not_invoked cap 40"):
        asyncio.run(review_generated_tests(host, REVIEW_STATE, candidate, cases, 1))


def test_supported_95_with_full_evidence_is_accepted_with_confidence_95(tmp_path):
    (result,) = review(tmp_path, scores(traceability=5))
    assert result["verdict"] == "supported"
    assert result["confidence"] == 95 and result["model_score"] == 95
    assert result["caps"] == []
    assert result["scores"]["traceability"] == 5


def test_missing_assertion_forces_rejection_even_with_full_marks(tmp_path):
    candidate, cases = review_fixture(tmp_path)
    candidate["files"][0]["content"] = "from service import add\n\ndef test_add():\n    add(1, 2)\n"
    host = ReviewHost(tmp_path, FULL_MARKS)
    with pytest.raises(ValueError, match="no_assertion cap 30"):
        asyncio.run(review_generated_tests(host, REVIEW_STATE, candidate, cases, 1))


class StubbornRuntime(ScriptedStageRuntime):
    """The extra `bad case` is generated without invoking its target, so the host caps it on every attempt."""

    async def run_stage(self, *, stage, state, **kwargs):
        result = await super().run_stage(stage=stage, state=state, **kwargs)
        for item in result.get("artifact", {}).get("files", []):
            if item["path"].endswith("bad_case.py"):
                item["lines"] = ["def test_bad_case():", "    assert len('ab') == 2"]
        return result


def test_accuracy_gate_blocks_execution_when_a_case_never_verifies(tmp_path, monkeypatch):
    import maf_test_harness.workflow as wf

    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 1)
    h = Harness(
        tmp_path,
        skills_root=Path("skills"),
        requirements_mode="model",
        runtime=StubbornRuntime(extra_cases=[BAD_CASE]),
    )
    svc = RuntimeService()
    try:
        svc.submit(h.start("https://github.com/acme/demo")).result(10)
        for _ in range(6):  # clone, repo review, requirements, planning, design, then generation runs
            if h.view["pending"].get("kind") == "error":
                break
            svc.submit(h.respond(h.view["pending"]["id"], {"decision": "approve"})).result(120)
        pending = h.view["pending"]
        # The gate fails the generation stage itself: it is retryable there, and no later stage was reached.
        assert pending["stage"] == "test_generation" and pending["kind"] == "error"
        message = pending["error"]
        assert message.startswith("Accuracy gate: 1 of 2 cases are not verified; execution is blocked.")
        assert "target_not_invoked cap 40" in message
        assert message.rstrip().endswith("or set HARNESS_REQUIRE_FULL_ACCURACY=0 to allow partial execution.")
    finally:
        svc.close()


class FlakyProvider:
    """TC-2 does not invoke its target on its first two attempts, then behaves."""

    def __init__(self):
        self.calls = {}

    async def run_stage(self, **kwargs):
        if review := review_response(kwargs):
            return review
        case = json.loads(kwargs["prompt"])["approved_inputs"]["approved_cases"][0]["id"]
        attempt = self.calls[case] = self.calls.get(case, 0) + 1
        good = ["def value():", "    return 2", "", "def test_value():", "    assert value() == 2"]
        lines = ["def test_x():", "    assert len('ab') == 2"] if case == "TC-2" and attempt <= 2 else good
        return {
            "status": "completed",
            "artifact": {
                "selected_language": "Python",
                "selected_framework": "pytest",
                "rationale": "native",
                "files": [{"path": f"test_{case}_{attempt}.py", "lines": lines, "case_ids": [case]}],
            },
        }


def flaky_state():
    return {
        "artifacts": {
            "repo_analysis": {"languages": {"Python": 1}, "source_files": ["service.py"]},
            "test_design": {
                "positive_tests": [
                    {"id": "TC-1", "target": "service.value"},
                    {"id": "TC-2", "target": "service.value"},
                ]
            },
        }
    }


def test_case_verified_on_a_later_round_passes_the_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 1)
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(generation_steps, "ACCURACY_ROUNDS", 2)
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "service.py").write_text("def value(): return 2", encoding="utf-8")
    host = Harness(tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=FlakyProvider())
    result = asyncio.run(host._generate_tests_batched(flaky_state()))
    assert result["verification_summary"] == {
        "enforced": True,
        "verified": 2,
        "total": 2,
        "min_confidence": 100,
        "threshold": 80,
    }
    assert result["case_verification"]["TC-2"]["rounds"] == 2
    assert result["case_verification"]["TC-1"]["rounds"] == 0
    assert result["generation_failures"] == []
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert audit.count("accuracy_round") == 2


def test_gate_off_keeps_the_fail_soft_path_and_reports_the_unverified_case(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 1)
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(generation_steps, "REQUIRE_FULL_ACCURACY", False)
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "service.py").write_text("def value(): return 2", encoding="utf-8")
    host = Harness(tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=FlakyProvider())
    result = asyncio.run(host._generate_tests_batched(flaky_state()))
    assert result["verification_summary"]["enforced"] is False
    assert result["verification_summary"]["verified"] == 1
    assert [f["case_ids"] for f in result["generation_failures"]] == [["TC-2"]]
    assert result["case_verification"]["TC-2"]["rounds"] == 0


def test_qa_report_shows_confidence_and_the_verification_line():
    design = {
        "positive_tests": [
            {
                "id": "TC-1",
                "title": "adds",
                "level": "unit",
                "priority": "P1",
                "actor": "Developer",
                "traceability_links": [],
            },
            {
                "id": "TC-2",
                "title": "other",
                "level": "unit",
                "priority": "P2",
                "actor": "Developer",
                "traceability_links": [],
            },
        ]
    }
    generation = {
        "files": [{"path": "tests/test_add.py", "case_ids": ["TC-1"], "content": PY_OK}],
        "case_reviews": [
            {
                "case_id": "TC-1",
                "confidence": 95,
                "model_score": 95,
                "verdict": "supported",
                "reason": "r",
                "scores": FULL_MARKS,
                "caps": [],
            }
        ],
        "generation_failures": [],
        "verification_summary": {
            "enforced": True,
            "verified": 1,
            "total": 2,
            "min_confidence": 95,
            "threshold": 85,
        },
    }
    report = build_qa_report(repo={}, requirements={}, design=design, generation=generation, execution=None)
    markdown = render_markdown(report)
    assert "| Pri | Confidence | Status | File |" in markdown
    assert re.search(r"\| TC-1 \|.*\| P1 \| 95 \| ", markdown)
    assert (
        "Verification: verified 1/2 cases (threshold 85, lowest verified confidence 95; full-accuracy gate)."
        in markdown
    )
    outcome = _case_outcome(
        {"id": "TC-1", "file": "tests/test_add.py", "status": "passed", "confidence": 95}, {}, "not_run", ""
    )
    assert outcome["confidence"] == 95


def test_accuracy_gate_has_its_own_error_category_and_remedy():
    message = "Accuracy gate: 1 of 2 cases are not verified; execution is blocked.\nTC-2: confidence 40"
    assert categorize(message) == "accuracy_gate"
    assert "HARNESS_REQUIRE_FULL_ACCURACY=0" in remedy_for(message)


GOOD_TEST = ["def value():", "    return 2", "", "def test_value():", "    assert value() == 2"]
BAD_TEST = ["def test_x():", "    assert len('ab') == 2"]  # never names the target: capped at 40


class CountingProvider:
    """Each case's first `bad_attempts[case]` generations do not invoke the target; later ones do."""

    def __init__(self, bad_attempts):
        self.bad_attempts = bad_attempts
        self.calls = {}

    async def run_stage(self, **kwargs):
        if review := review_response(kwargs):
            return review
        approved = [c["id"] for c in json.loads(kwargs["prompt"])["approved_inputs"]["approved_cases"]]
        files = []
        for case in approved:
            self.calls[case] = self.calls.get(case, 0) + 1
            bad = self.calls[case] <= self.bad_attempts.get(case, 0)
            files.append(
                {
                    "path": f"test_{case}_{self.calls[case]}.py",
                    "lines": BAD_TEST if bad else GOOD_TEST,
                    "case_ids": [case],
                }
            )
        return {
            "status": "completed",
            "artifact": {
                "selected_language": "Python",
                "selected_framework": "pytest",
                "rationale": "native",
                "files": files,
            },
        }


def counting_host(tmp_path, provider, ids=("TC-1", "TC-2")):
    (tmp_path / "repo").mkdir(exist_ok=True)
    (tmp_path / "repo" / "service.py").write_text("def value(): return 2", encoding="utf-8")
    state = {
        "artifacts": {
            "repo_analysis": {"languages": {"Python": 1}, "source_files": ["service.py"]},
            "test_design": {"positive_tests": [{"id": i, "target": "service.value"} for i in ids]},
        }
    }
    host = Harness(tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=provider)
    return host, state


def test_last_attempt_keeps_verified_cases_and_fails_only_the_rejected_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 2)
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(generation_steps, "REQUIRE_FULL_ACCURACY", False)
    provider = CountingProvider({"TC-2": 99})
    # Two batches: [TC-1, TC-2] is partly rejected on its last attempt, [TC-3] passes (1 of 2 batches failed).
    host, state = counting_host(tmp_path, provider, ids=("TC-1", "TC-2", "TC-3"))
    result = asyncio.run(host._generate_tests_batched(state))
    assert sorted(f["case_ids"][0] for f in result["files"]) == ["TC-1", "TC-3"]
    assert sorted(r["case_id"] for r in result["case_reviews"]) == ["TC-1", "TC-3"]
    assert [f["case_ids"] for f in result["generation_failures"]] == [["TC-2"]]
    assert "target_not_invoked" in result["generation_failures"][0]["last_error"]
    assert result["case_verification"]["TC-1"]["confidence"] == 100
    assert result["verification_summary"]["verified"] == 2
    assert result["verification_summary"]["total"] == 3


def test_rejected_ids_from_a_partial_batch_go_to_the_accuracy_rounds(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 2)
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(generation_steps, "ACCURACY_ROUNDS", 1)
    provider = CountingProvider({"TC-2": 2})  # bad on both batch attempts, good in the round
    host, state = counting_host(tmp_path, provider)
    result = asyncio.run(host._generate_tests_batched(state))
    assert result["verification_summary"]["verified"] == 2
    assert result["generation_failures"] == []
    assert provider.calls == {"TC-1": 2, "TC-2": 3}  # TC-1 is not regenerated by the round
    assert result["case_verification"]["TC-2"]["rounds"] == 1


def test_a_retry_regenerates_only_the_cases_missing_from_the_partial_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 2)
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(generation_steps, "REQUIRE_FULL_ACCURACY", False)
    provider = CountingProvider({"TC-2": 2})
    host, state = counting_host(tmp_path, provider, ids=("TC-1", "TC-2", "TC-3"))
    asyncio.run(host._generate_tests_batched(state))  # TC-2 fails on its last attempt
    assert provider.calls == {"TC-1": 2, "TC-2": 2, "TC-3": 1}
    result = asyncio.run(host._generate_tests_batched(state))  # retry: TC-2 now behaves
    assert provider.calls == {"TC-1": 2, "TC-2": 3, "TC-3": 1}  # the batch cache keeps TC-1 and TC-3
    assert sorted(i for f in result["files"] for i in f["case_ids"]) == ["TC-1", "TC-2", "TC-3"]
    assert len(result["files"]) == len({f["path"] for f in result["files"]})


def test_cache_is_invalidated_when_the_review_threshold_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 2)
    provider = CountingProvider({})
    host, state = counting_host(tmp_path, provider)
    asyncio.run(host._generate_tests_batched(state))
    asyncio.run(host._generate_tests_batched(state))
    assert provider.calls == {"TC-1": 1, "TC-2": 1}  # same settings: accepted batch is reused
    monkeypatch.setattr(generation_steps, "TEST_REVIEW_MIN_CONFIDENCE", 90)
    asyncio.run(host._generate_tests_batched(state))
    assert provider.calls == {"TC-1": 2, "TC-2": 2}


def test_gate_on_skips_the_early_ratio_raise_and_reports_the_unverified_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 1)
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(generation_steps, "ACCURACY_ROUNDS", 0)
    provider = CountingProvider({"TC-1": 99, "TC-2": 99, "TC-3": 99})
    host, state = counting_host(tmp_path, provider, ids=("TC-1", "TC-2", "TC-3"))
    with pytest.raises(ValueError) as exc:
        asyncio.run(host._generate_tests_batched(state))
    message = str(exc.value)
    assert message.startswith("Accuracy gate: 3 of 3 cases are not verified; execution is blocked.")
    assert "failed for" not in message
    assert (
        "Retry the generation stage (verified cases are kept; Rerun-from-step or Revise regenerate everything)"
        in message
    )


def test_gate_reasons_ignore_reviews_recorded_before_this_generation_run(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 1)
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(generation_steps, "ACCURACY_ROUNDS", 0)
    provider = CountingProvider({"TC-2": 99})
    host, state = counting_host(tmp_path, provider)
    stale = {
        "case_id": "TC-2",
        "verdict": "supported",
        "confidence": 10,
        "reason": "STALE reason from an earlier run.",
        "scores": FULL_MARKS,
        "caps": [],
    }
    state["test_reviews"] = [{"batch": 1, "reviews": [stale]}]
    with pytest.raises(ValueError) as exc:
        asyncio.run(host._generate_tests_batched(state))
    assert "STALE" not in str(exc.value)
    assert "TC-2: confidence 40" in str(exc.value)


def test_default_review_threshold_is_80_and_the_model_need_not_emit_confidence(tmp_path, monkeypatch):
    import subprocess
    import sys

    from maf_test_harness.models import TestCaseReview

    env = {k: v for k, v in os.environ.items() if k != "HARNESS_TEST_REVIEW_MIN_CONFIDENCE"}
    code = "from maf_test_harness import settings; print(settings.TEST_REVIEW_MIN_CONFIDENCE)"
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=Path.cwd()
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "80"
    review_model = TestCaseReview.model_validate(
        {
            "case_id": "TC-1",
            "scores": FULL_MARKS,
            "verdict": "supported",
            "reason": "r",
            "source_files": ["service.py"],
        }
    )
    assert review_model.confidence == 0

    seen = []

    class PromptHost(ReviewHost):
        async def _call_model(self, **kwargs):
            seen.append(kwargs["prompt"])
            return await super()._call_model(**kwargs)

    candidate, cases = review_fixture(tmp_path)
    asyncio.run(review_generated_tests(PromptHost(tmp_path, FULL_MARKS), REVIEW_STATE, candidate, cases, 1))
    assert "each dimension should be at least 85% of its maximum" in seen[0]

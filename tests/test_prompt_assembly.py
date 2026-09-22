"""Prompt assembly for prefix caching: stable content first, so consecutive calls of a stage share a long prefix."""

import json
from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
from test_execution_stage import local_pytest_plan

import maf_test_harness.workflow as wf
from maf_test_harness.llm import prompts
from maf_test_harness.ui.runtime_service import RuntimeService

# --- the assembler -------------------------------------------------------------------------------------------


def test_stable_keys_come_first_in_the_declared_order():
    sections = {"repair_feedback": "r", "assignment": "a", "custom": 1, "output_contract": {"x": 1}}
    text = prompts.build_prompt(sections)
    assert list(json.loads(text)) == ["assignment", "output_contract", "repair_feedback", "custom"]


def test_unlisted_keys_keep_their_insertion_order_after_the_listed_ones():
    assert list(prompts.ordered({"z": 1, "y": 2, "assignment": 3}, prompts.STAGE_ORDER)) == [
        "assignment",
        "z",
        "y",
    ]


def test_the_prompt_is_compact_json_with_the_same_content():
    sections = {"assignment": "do it", "output_contract": {"type": "object"}, "extra": [1, 2]}
    text = prompts.build_prompt(sections)
    assert " " not in text.replace("do it", "") and json.loads(text) == sections


def test_generation_prompt_orders_approved_inputs_stable_first():
    inputs = {
        "approved_cases": [1],
        "batch": {"index": 1},
        "requirements": {},
        "test_plan": {"a": 1},
        "generation_policy": {},
    }
    parsed = json.loads(
        prompts.generation_prompt({"repair_feedback": "x", "approved_inputs": inputs, "assignment": "a"})
    )
    assert list(parsed) == ["assignment", "approved_inputs", "repair_feedback"]
    assert list(parsed["approved_inputs"]) == [
        "test_plan",
        "generation_policy",
        "requirements",
        "batch",
        "approved_cases",
    ]


def test_two_calls_that_differ_only_in_the_tail_share_everything_before_it():
    head = {"assignment": "a" * 500, "output_contract": {"schema": "s" * 2000}}
    first = prompts.build_prompt({**head, "repair_feedback": None})
    second = prompts.build_prompt(
        {**head, "repair_feedback": "attempt 1 was rejected because of this reason"}
    )
    assert prompts.shared_prefix_chars(first, second) > len(json.dumps(head)) - 5
    assert prompts.prefix_ratio(first, second) > 95


def test_prefix_helpers_on_empty_and_identical_input():
    assert prompts.prefix_ratio("", "") == 0.0 and prompts.prefix_ratio("abc", "abc") == 100.0


# --- the real generation pipeline -------------------------------------------------------------------------------


CASE = {
    "priority": "P2",
    "level": "unit",
    "actor": "Developer",
    "target": "service.add",
    "description": "Another input pair.",
    "preconditions": [],
    "data_notes": "2 and 2",
    "expected_result": "add(2, 2) returns 4",
    "steps": ["Call add(2, 2)", "Compare with 4"],
}


class Recording(ScriptedStageRuntime):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.generation_prompts = []

    async def run_stage(self, *, stage, state, **kwargs):
        if stage == "test_generation":
            self.generation_prompts.append(kwargs["prompt"])
        return await super().run_stage(stage=stage, state=state, **kwargs)


@pytest.fixture
def drive(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    monkeypatch.setattr(wf, "build_plan", local_pytest_plan)
    monkeypatch.setattr(wf, "build_environment_plan", local_env_plan)
    monkeypatch.setattr(wf, "TEST_GENERATION_BATCH_SIZE", 1)
    service = RuntimeService()

    def make(runtime):
        host = wf.Harness(
            tmp_path / "run", skills_root=Path("skills"), runtime=runtime, cache_dir=tmp_path / "cache"
        )
        service.submit(host.start("https://github.com/acme/demo")).result(15)

        def answer(**response):
            return service.submit(host.respond(host.view["pending"]["id"], response)).result(120)

        return host, answer

    yield make
    service.close()


def test_consecutive_generation_batches_share_a_long_prefix(drive):
    runtime = Recording(extra_cases=[{"title": "add two", **CASE}, {"title": "add three", **CASE}])
    host, answer = drive(runtime)
    for _ in range(8):  # up to and including the generation review
        answer(decision="approve")
        if host.view["pending"]["stage"] == "test_execution":
            break
    seen = runtime.generation_prompts
    assert len(seen) >= 3, "three approved cases at batch size 1 make three model calls"
    schema_chars = len(json.dumps(json.loads(seen[0])["output_contract"], separators=(",", ":")))
    for earlier, later in zip(seen, seen[1:], strict=False):
        shared = prompts.shared_prefix_chars(earlier, later)
        assert shared > schema_chars, "the shared prefix reaches past the output schema"
        assert list(json.loads(earlier))[:3] == ["assignment", "output_contract", "json_rules"]
    cases = [json.loads(p)["approved_inputs"]["approved_cases"][0]["title"] for p in seen[:3]]
    assert len(set(cases)) == 3, "the per-batch content is what differs, and it comes after the shared part"
    ratio = prompts.prefix_ratio(seen[0], seen[1])
    assert ratio > 30, f"expected a substantial shared prefix, got {ratio}%"


def test_stage_prompts_start_with_the_assignment_and_the_schema_and_end_with_feedback(drive):
    seen = []

    class Spy(ScriptedStageRuntime):
        async def run_stage(self, *, stage, state, **kwargs):
            seen.append((stage, kwargs["prompt"]))
            return await super().run_stage(stage=stage, state=state, **kwargs)

    host, answer = drive(Spy())
    for _ in range(4):
        answer(decision="approve")
    stage_prompts = {stage: json.loads(prompt) for stage, prompt in seen if stage != "test_generation"}
    assert stage_prompts
    for stage, prompt in stage_prompts.items():
        keys = list(prompt)
        assert keys[:3] == ["assignment", "output_contract", "json_rules"], stage
        assert keys[-2:] == ["revision_feedback", "repair_feedback"], stage

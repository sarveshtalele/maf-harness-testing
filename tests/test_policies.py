"""Workflow policies: token budget, design rules, and approval-before-generation."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import ScriptedStageRuntime, clone_fixture

import maf_test_harness.workflow as wf
from maf_test_harness.maf_runtime import BudgetGuard
from maf_test_harness.runtime_service import RuntimeService
from maf_test_harness.settings import RUN_TOKEN_LIMIT


class Msg:
    def __init__(self, size):
        self.size = size

    def to_dict(self):
        return {"text": "x" * self.size}


def call(guard, size, output=100):
    async def next_():
        return None

    context = SimpleNamespace(messages=[Msg(size)], options={"max_output_tokens": output}, result=None)
    asyncio.run(guard.process(context, next_))


def test_only_limit_is_two_million_tokens_per_run():
    assert RUN_TOKEN_LIMIT == 2_000_000
    budget = {"charged": 0, "calls": 0}
    guard = BudgetGuard(budget, lambda: None)
    call(guard, 900_000)  # a single very large prompt is fine: there is no per-call input limit
    for _ in range(400):  # and there is no provider-call-count limit
        call(guard, 10)
    assert budget["calls"] == 401 and "input_limit" not in budget and "call_limit" not in budget


def test_run_budget_is_enforced_with_a_clear_message():
    budget = {"charged": RUN_TOKEN_LIMIT - 10, "calls": 5}
    with pytest.raises(ValueError, match="2,000,000 tokens exhausted"):
        call(BudgetGuard(budget, lambda: None), 1000)
    assert budget["calls"] == 5  # refused before any provider call was counted


@pytest.fixture
def drive(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    service = RuntimeService()

    def make(runtime, **kwargs):
        host = wf.Harness(
            tmp_path / "run", skills_root=Path("skills"), runtime=runtime, execute=False, **kwargs
        )
        service.submit(host.start("https://github.com/acme/demo")).result(15)

        def answer(**response):
            return service.submit(host.respond(host.view["pending"]["id"], response)).result(60)

        return host, answer

    yield make
    service.close()


def test_test_source_is_generated_only_after_the_design_is_approved(drive):
    runtime = ScriptedStageRuntime()
    host, answer = drive(runtime)
    for _ in range(4):  # clone, repo, requirements, plan
        answer(decision="approve")
    assert host.view["pending"]["stage"] == "test_design" and host.view["pending"]["kind"] == "review"
    assert "test_generation" not in runtime.calls  # design is shown to the human first
    assert host.view["pending"]["details"]["artifact"]["positive_tests"]
    answer(decision="revise", feedback="also cover subtraction")
    assert "test_generation" not in runtime.calls and host.view["pending"]["stage"] == "test_design"
    answer(decision="approve")
    assert host.view["pending"]["stage"] == "test_generation" and runtime.calls.count("test_generation") >= 1
    assert host.state["approved"]["test_design"]  # generation ran only on the approved design hash


def design_runtime(mutate):
    runtime = ScriptedStageRuntime()
    original, prompts = runtime.run_stage, []

    async def run(**kwargs):
        result = await original(**kwargs)
        if kwargs["stage"] == "test_design":
            prompts.append(kwargs["prompt"])
            mutate(result["artifact"], len(prompts))
        return result

    runtime.run_stage = run
    return runtime, prompts


def test_design_without_unit_cases_is_sent_back_then_accepted_when_fixed(drive):
    def mutate(artifact, attempt):
        if attempt == 1:
            artifact["positive_tests"][0]["level"] = "integration"

    runtime, prompts = design_runtime(mutate)
    host, answer = drive(runtime)
    for _ in range(4):
        answer(decision="approve")
    assert len(prompts) == 2 and "no unit-level test case" in prompts[1]
    levels = {c["level"] for c in host.view["pending"]["details"]["artifact"]["positive_tests"]}
    assert levels == {"unit"}


def test_every_actor_needs_cases_or_a_stated_reason(drive):
    def mutate(artifact, attempt):
        if attempt == 1:
            artifact["positive_tests"][0]["actor"] = "system"

    runtime, prompts = design_runtime(mutate)
    host, answer = drive(runtime)
    for _ in range(4):
        answer(decision="approve")
    assert len(prompts) == 2 and "Actor(s) without any test case: Developer" in prompts[1]


def test_unresolved_policy_gaps_reach_the_human_instead_of_blocking(drive):
    runtime, prompts = design_runtime(
        lambda artifact, attempt: artifact["positive_tests"][0].update(level="integration")
    )
    host, answer = drive(runtime)
    for _ in range(4):
        answer(decision="approve")
    assert len(prompts) == wf.MODEL_REPAIR_ATTEMPTS
    assert host.view["pending"]["kind"] == "review"
    assert "no unit-level test case" in host.view["pending"]["details"]["artifact"]["gaps_report"]["notes"]
    json.dumps(host.view["pending"]["details"])

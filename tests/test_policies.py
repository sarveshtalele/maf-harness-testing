"""Workflow policies: token budget, design rules, and approval-before-generation."""

import asyncio
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import ScriptedStageRuntime, clone_fixture

import maf_test_harness.workflow as wf
from maf_test_harness.agent.runtime import BudgetGuard
from maf_test_harness.llm.usage import DailyLimitError, DailyUsageLedger
from maf_test_harness.settings import DAILY_TOKEN_LIMIT
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow_support import MODEL_REPAIR_ATTEMPTS


class Msg:
    def __init__(self, size):
        self.size = size

    def to_dict(self):
        return {"text": "x" * self.size}


def call(guard, size, output=100, fail=False, usage=None):
    async def next_():
        if fail:
            raise RuntimeError("provider down")

    result = SimpleNamespace(usage_details=usage) if usage else None
    context = SimpleNamespace(messages=[Msg(size)], options={"max_output_tokens": output}, result=result)
    asyncio.run(guard.process(context, next_))


def make_guard(tmp_path, limit=DAILY_TOKEN_LIMIT, today=None):
    ledger = DailyUsageLedger(tmp_path / "usage", limit=limit, **({"today": today} if today else {}))
    return BudgetGuard({"charged": 0, "calls": 0}, lambda: None, ledger), ledger


def test_the_only_limit_is_a_daily_cap_of_five_million_tokens():
    assert DAILY_TOKEN_LIMIT == 50_000_000
    import maf_test_harness.settings as settings

    assert not hasattr(settings, "RUN_TOKEN_LIMIT")  # the old per-run cap is gone


def test_no_per_call_or_call_count_or_per_run_limit(tmp_path):
    guard, ledger = make_guard(tmp_path)
    for _ in range(8):  # very large prompts are fine, and one run may exceed the former 2M per-run cap
        call(guard, 900_000)
    for _ in range(400):  # and there is no provider-call-count limit
        call(guard, 10)
    assert guard.budget["calls"] == 408
    assert guard.budget["charged"] > 2_000_000 - 1  # one run may exceed the former 2M per-run cap
    assert "limit" not in guard.budget and ledger.snapshot()["used"] > 0


def test_daily_cap_is_shared_across_runs_and_refuses_with_a_clear_message(tmp_path):
    ledger = DailyUsageLedger(tmp_path / "usage", limit=1_000)
    first = BudgetGuard({"charged": 0, "calls": 0}, lambda: None, ledger)
    second = BudgetGuard(
        {"charged": 0, "calls": 0}, lambda: None, ledger
    )  # another run, same machine and day
    call(first, 300, output=100)  # reserves ~200 tokens
    call(second, 300, output=100)
    with pytest.raises(DailyLimitError, match="Daily token cap of 1,000 reached"):
        call(second, 3_000, output=100)
    assert second.budget["calls"] == 1  # refused before any provider call was counted


def test_ledger_settles_to_reported_usage_and_failed_calls_are_not_charged(tmp_path):
    guard, ledger = make_guard(tmp_path, limit=1_000_000)
    call(
        guard,
        3_000,
        output=500,
        usage={"input_token_count": 40, "output_token_count": 10, "total_token_count": 50},
    )
    assert ledger.snapshot()["used"] == 50 and guard.budget["reported_tokens"] == 50
    with pytest.raises(RuntimeError):
        call(guard, 3_000, output=500, fail=True)
    assert ledger.snapshot()["used"] == 50 and guard.budget["calls"] == 2  # refunded


def test_the_cap_resets_on_a_new_local_day(tmp_path):
    day = {"now": date(2026, 9, 19)}
    guard, ledger = make_guard(tmp_path, limit=1_000, today=lambda: day["now"])
    call(guard, 300, output=100)
    with pytest.raises(DailyLimitError):
        call(guard, 3_000, output=100)
    day["now"] = date(2026, 9, 20)
    call(guard, 300, output=100)  # fresh day, fresh allowance
    assert ledger.snapshot() == {
        "date": "2026-09-20",
        "used": ledger.snapshot()["used"],
        "limit": 1_000,
        "remaining": 1_000 - ledger.snapshot()["used"],
    }


def test_concurrent_reservations_cannot_overshoot_the_cap(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    ledger = DailyUsageLedger(tmp_path / "usage", limit=1_000)

    def claim(_):
        try:
            ledger.reserve(100)
            return 1
        except DailyLimitError:
            return 0

    with ThreadPoolExecutor(16) as pool:
        granted = sum(pool.map(claim, range(40)))
    assert granted == 10 and ledger.snapshot()["used"] == 1_000


@pytest.fixture
def drive(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    service = RuntimeService()

    def make(runtime, **kwargs):
        host = wf.Harness(
            tmp_path / "run",
            skills_root=Path("skills"),
            requirements_mode="model",
            runtime=runtime,
            execute=False,
            **kwargs,
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


def test_unresolved_policy_gaps_remain_blocked_after_final_repair(drive):
    runtime, prompts = design_runtime(
        lambda artifact, attempt: artifact["positive_tests"][0].update(level="integration")
    )
    host, answer = drive(runtime)
    for _ in range(4):
        answer(decision="approve")
    assert len(prompts) == MODEL_REPAIR_ATTEMPTS
    assert host.view["pending"]["kind"] == "error"
    assert "no unit-level test case" in host.view["pending"]["error"]
    json.dumps(host.view["pending"]["details"])


def test_dotenv_is_read_without_overriding_the_environment_or_importing_unrelated_keys(tmp_path, monkeypatch):
    from maf_test_harness.settings import load_dotenv

    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "OPENAI_API_KEY=sk-from-file\n"
        "export OPENAI_BASE_URL='https://gw.example  # not a comment inside quotes'\n"
        'OPENAI_MODEL="my-model"   \n'
        "HARNESS_DAILY_TOKEN_LIMIT=123   # inline comment\n"
        "LLM_MODEL=\n"
        "AWS_SECRET_ACCESS_KEY=must-not-load\n"
        "garbage line\n"
    )
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "HARNESS_DAILY_TOKEN_LIMIT",
        "LLM_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "already-set")  # the real environment wins
    load_dotenv(env)
    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-from-file"
    assert os.environ["OPENAI_BASE_URL"] == "https://gw.example  # not a comment inside quotes"
    assert os.environ["OPENAI_MODEL"] == "already-set"
    assert os.environ["HARNESS_DAILY_TOKEN_LIMIT"] == "123"
    assert "LLM_MODEL" not in os.environ and "AWS_SECRET_ACCESS_KEY" not in os.environ
    load_dotenv(tmp_path / "missing.env")  # a missing file is fine

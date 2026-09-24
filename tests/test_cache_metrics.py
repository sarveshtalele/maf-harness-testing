"""Prompt-cache accounting: cached input tokens and prompt-prefix stability are recorded and reported."""

import asyncio
from types import SimpleNamespace

from maf_test_harness.agent.runtime import BudgetGuard
from maf_test_harness.llm.usage import DailyUsageLedger
from maf_test_harness.monitoring import report as observability


class Msg:
    def __init__(self, text):
        self.text = text

    def to_dict(self):
        return {"role": "user", "text": self.text}


def call(guard, text, usage):
    context = SimpleNamespace(messages=[Msg(text)], options={"max_output_tokens": 10}, result=None)

    async def call_next():
        context.result = SimpleNamespace(usage_details=usage)

    asyncio.run(guard.process(context, call_next))


def test_budget_guard_records_cached_tokens_and_prefix_stability(tmp_path):
    events, budget = [], {}
    guard = BudgetGuard(
        budget,
        lambda: None,
        DailyUsageLedger(tmp_path, limit=10**9),
        emit=lambda kind, **kw: events.append(kw),
    )
    usage = {"input_token_count": 100, "output_token_count": 5, "total_token_count": 105}
    call(guard, "SYSTEM RULES " * 50 + "batch one", {**usage, "cache_read_input_token_count": 0})
    call(guard, "SYSTEM RULES " * 50 + "batch two", {**usage, "cache_read_input_token_count": 80})
    assert budget["input_tokens"] == 200 and budget["cached_tokens"] == 80
    assert events[0]["prefix_shared_chars"] == 0  # nothing to repeat on the first call
    assert events[1]["tokens"]["cached"] == 80
    assert events[1]["prefix_shared_chars"] > 0.8 * events[1]["prompt_chars"]


def test_cache_report_aggregates_per_stage_and_flags_unreported_providers():
    chat = [
        {
            "status": "ok",
            "stage": "test_generation",
            "tokens": {"input": 100, "cached": 0},
            "prompt_chars": 300,
            "prefix_shared_chars": 0,
        },
        {
            "status": "ok",
            "stage": "test_generation",
            "tokens": {"input": 100, "cached": 90},
            "prompt_chars": 300,
            "prefix_shared_chars": 270,
        },
        {
            "status": "ok",
            "stage": "requirements",
            "tokens": {"input": 50, "cached": None},
            "prompt_chars": 100,
            "prefix_shared_chars": 0,
        },
        {"status": "error", "stage": "requirements"},
    ]
    report = observability._cache_report(chat)
    gen = report["stages"]["test_generation"]
    assert gen["hit_pct"] == 45.0 and gen["prefix_ratio"] == 45.0 and gen["calls"] == 2
    assert report["stages"]["requirements"]["hit_pct"] is None  # provider does not report cached tokens
    assert report["all"] == {"input": 250, "cached": 90, "hit_pct": 36.0}

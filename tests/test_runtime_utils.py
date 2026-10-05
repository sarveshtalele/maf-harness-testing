import pytest

from maf_test_harness.agent.runtime import AgentOutputError, extract_json_object


def test_extract_json_object_accepts_plain_json() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_accepts_fenced_json() -> None:
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_object_rejects_non_json() -> None:
    with pytest.raises(AgentOutputError):
        extract_json_object("no structured object here")


def test_extract_json_object_accepts_python_style_mapping() -> None:
    assert extract_json_object("Result: {'a': 1, 'ok': True}") == {"a": 1, "ok": True}


def test_model_operation_timeout_cancels_and_reports_retry(monkeypatch):
    import asyncio

    from maf_test_harness.agent import runtime

    cancelled = []

    class SlowAgent:
        async def run(self, prompt, **kwargs):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)

    monkeypatch.setattr(runtime, "MODEL_TIMEOUT_SECONDS", 0.01)
    adapter = runtime.MAFHarnessRuntime.__new__(runtime.MAFHarnessRuntime)
    with pytest.raises(runtime.AgentOutputError, match="timed out.*Retry"):
        asyncio.run(adapter._run_agent(SlowAgent(), "fixture"))
    assert cancelled == [True]

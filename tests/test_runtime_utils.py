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


def _gateway_down() -> Exception:
    """What MAF raises when the gateway is unreachable: a wrapper whose text and cause are the openai error."""
    import httpx
    import openai

    cause = openai.APIConnectionError(request=httpx.Request("POST", "http://gateway.test/v1/responses"))
    error = RuntimeError(
        "<class 'OpenAIChatClient'> service failed to complete the prompt: Connection error."
    )
    error.__cause__ = cause
    return error


class _ScriptedAgent:
    """Fails with ``error`` for the first ``failures`` runs, then returns ``reply``."""

    def __init__(self, failures, error=None, reply="ok"):
        self.failures, self.error, self.reply = failures, error, reply
        self.calls = 0

    async def run(self, prompt, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return self.reply


def _adapter():
    from maf_test_harness.agent import runtime

    adapter = runtime.MAFHarnessRuntime.__new__(runtime.MAFHarnessRuntime)
    adapter.recorder = None
    adapter._trace_context = {}
    return adapter


def test_transient_gateway_errors_are_retried_then_succeed(monkeypatch):
    import asyncio

    from maf_test_harness.agent import runtime

    monkeypatch.setattr(runtime, "GATEWAY_RETRY_DELAYS", (0, 0, 0))
    agent = _ScriptedAgent(failures=2, error=_gateway_down())
    assert asyncio.run(_adapter()._run_agent(agent, "p")) == "ok"
    assert agent.calls == 3


def test_persistent_gateway_outage_raises_gateway_unavailable(monkeypatch):
    import asyncio

    from maf_test_harness.agent import runtime

    monkeypatch.setattr(runtime, "GATEWAY_RETRY_DELAYS", (0, 0, 0))
    agent = _ScriptedAgent(failures=10**6, error=_gateway_down())
    with pytest.raises(runtime.GatewayUnavailableError, match="unreachable.*Retry the stage"):
        asyncio.run(_adapter()._run_agent(agent, "p"))
    assert agent.calls == len(runtime.GATEWAY_RETRY_DELAYS) + 1


def test_non_transient_error_is_raised_immediately(monkeypatch):
    import asyncio

    from maf_test_harness.agent import runtime

    monkeypatch.setattr(runtime, "GATEWAY_RETRY_DELAYS", (0, 0, 0))
    agent = _ScriptedAgent(failures=10**6, error=ValueError("bad request shape"))
    with pytest.raises(ValueError, match="bad request shape"):
        asyncio.run(_adapter()._run_agent(agent, "p"))
    assert agent.calls == 1


def test_stop_interrupts_the_gateway_retry_wait(monkeypatch):
    import asyncio
    import threading

    from maf_test_harness.agent import runtime
    from maf_test_harness.envs.platform_compat import RUN_CANCEL

    monkeypatch.setattr(runtime, "GATEWAY_RETRY_DELAYS", (60,))
    event = threading.Event()
    agent = _ScriptedAgent(failures=10**6, error=_gateway_down())

    async def scenario():
        async def press_stop():
            await asyncio.sleep(0.05)
            event.set()

        asyncio.get_running_loop().create_task(press_stop())
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(_adapter()._run_agent(agent, "p"), timeout=5)

    token = RUN_CANCEL.set(event)
    try:
        asyncio.run(scenario())
    finally:
        RUN_CANCEL.reset(token)
    assert agent.calls == 1  # the 60 s wait ended on Stop, no second attempt


def test_gateway_error_detection_walks_causes_and_args():
    import httpx
    import openai

    from maf_test_harness.agent.runtime import GatewayUnavailableError, _is_transient_gateway_error

    assert _is_transient_gateway_error(_gateway_down())
    assert _is_transient_gateway_error(
        RuntimeError("upstream said Server disconnected without sending a response")
    )
    request = httpx.Request("POST", "http://gateway.test")
    response = httpx.Response(503, request=request)
    assert _is_transient_gateway_error(openai.InternalServerError("busy", response=response, body=None))
    assert _is_transient_gateway_error(
        openai.RateLimitError("slow", response=httpx.Response(429, request=request), body=None)
    )
    assert not _is_transient_gateway_error(
        openai.BadRequestError("bad", response=httpx.Response(400, request=request), body=None)
    )
    wrapped_in_args = RuntimeError(openai.APIConnectionError(request=request))
    assert _is_transient_gateway_error(wrapped_in_args)
    assert not _is_transient_gateway_error(
        GatewayUnavailableError("gateway was unreachable (Connection error.)")
    )
    assert not _is_transient_gateway_error(ValueError("plain"))

import asyncio
from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime, clone_fixture

from maf_test_harness.runtime_service import RuntimeService
from maf_test_harness.workflow import Harness, required_inputs


def test_runtime_keeps_loop_open_across_calls():
    service = RuntimeService()

    async def loop():
        return asyncio.get_running_loop()

    first = service.submit(loop()).result(5)
    assert service.submit(loop()).result(5) is first
    assert not first.is_closed()
    service.close()
    assert first.is_closed()


def test_autonomous_workflow_requires_no_stage_forms():
    for stage in ("requirements", "test_planning", "test_design", "test_generation"):
        assert required_inputs(stage, {}) == {}


def test_native_workflow_pauses_before_clone_and_rejects_stale_response(tmp_path: Path):
    h = Harness(tmp_path, skills_root=Path("skills"))
    service = RuntimeService()
    try:
        view = service.submit(h.start("https://github.com/acme/demo")).result(10)
        assert view["pending"]["kind"] == "tool"
        assert view["pending"]["stage"] == "repo_analysis"
        assert not (tmp_path / "repo").exists()
        with pytest.raises(ValueError, match="Stale"):
            service.submit(h.respond("wrong", {"decision": "approve"})).result(10)
        result = service.submit(h.respond(view["pending"]["id"], {"decision": "stop"})).result(10)
        assert result["status"] == "stopped"
        assert not (tmp_path / "repo").exists()
    finally:
        service.close()


def test_workflow_without_execution_stage_ends_after_generation(tmp_path, monkeypatch):
    import maf_test_harness.workflow as wf

    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    runtime = ScriptedStageRuntime()
    h = Harness(tmp_path, skills_root=Path("skills"), runtime=runtime, execute=False)
    svc = RuntimeService()

    def answer(**response):
        result = svc.submit(h.respond(h.view["pending"]["id"], response)).result(30)
        assert (result.get("pending") or {}).get("kind") != "error", result.get("pending")
        return result

    try:
        svc.submit(h.start("https://github.com/acme/demo")).result(10)
        answer(decision="approve")  # clone
        assert h.view["pending"]["stage"] == "repo_analysis"
        answer(decision="approve")  # repo review -> requirements agent
        assert h.view["pending"]["stage"] == "requirements"
        answer(decision="approve")
        assert h.view["pending"]["stage"] == "test_planning"
        answer(decision="approve")
        assert h.view["pending"]["stage"] == "test_design"
        answer(decision="approve")
        assert h.view["pending"]["stage"] == "test_generation"
        final = answer(decision="approve")
        assert final["status"] == "complete"
        assert runtime.calls[:3] == ["requirements", "test_planning", "test_design"]
        assert runtime.calls.count("test_generation") >= 1
        assert h.state["artifacts"]["test_generation"]["execution_status"] == "pending_host_execution"
        assert not (tmp_path / "workspaces").exists()
        assert "without the execution stage" in h.state["completed_reason"]
        req = h.state["artifacts"]["requirements"]
        assert req["actors"][0]["name"] == "Developer"
        case = h.state["artifacts"]["test_design"]["positive_tests"][0]
        assert (case["level"], case["actor"], case["target"]) == ("unit", "Developer", "service.add")
    finally:
        svc.close()

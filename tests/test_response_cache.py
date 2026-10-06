"""Prefix-cache friendly design prompts and the local response cache (docs/CACHING.md)."""

import asyncio
import copy
import json
from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
from test_execution_stage import local_pytest_plan

import maf_test_harness.agent.runtime as runtime_module
import maf_test_harness.workflow as wf
from maf_test_harness.llm import prompts
from maf_test_harness.models import TestDesignDraft as DesignDraft
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow_steps import execution as execution_steps

STABLE = (
    "assignment",
    "output_contract",
    "json_rules",
    "repository",
    "stack_profile",
    "live_app",
    "selected_application",
    "evidence_file_paths",
    "traceability_rule",
)


class Capture(ScriptedStageRuntime):
    armed = False

    def __init__(self):
        super().__init__()
        self.prompts = []

    async def run_stage(self, *, stage, state, **kwargs):
        if stage == "test_design" and self.armed:
            self.prompts.append(kwargs["prompt"])
            raise RuntimeError("captured")
        return await super().run_stage(stage=stage, state=state, **kwargs)


def design_prompts(tmp_path, monkeypatch):
    """Two real design prompts (the real assembly in _model_artifact) for two different requirement batches."""
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    monkeypatch.setattr(execution_steps, "build_plan", local_pytest_plan)
    monkeypatch.setattr(execution_steps, "build_environment_plan", local_env_plan)
    runtime = Capture()
    service = RuntimeService()
    host = wf.Harness(
        tmp_path / "run",
        skills_root=Path("skills"),
        requirements_mode="model",
        runtime=runtime,
        cache_dir=tmp_path / "cache",
    )
    service.submit(host.start("https://github.com/acme/demo")).result(15)
    for _ in range(8):
        if host.view["pending"]["stage"] == "test_design":
            break
        service.submit(host.respond(host.view["pending"]["id"], {"decision": "approve"})).result(120)
    requirements = host.state["artifacts"]["requirements"]
    other = copy.deepcopy(requirements)
    first = other["requirement_model"]["requirements"][0]
    other["requirement_model"]["requirements"] = [{**first, "id": "REQ-OTHER-BATCH-42", "text": "other"}]
    runtime.armed = True
    for batch, source in enumerate((requirements, other), 1):
        with pytest.raises(RuntimeError, match="captured"):
            service.submit(
                host._model_artifact("test_design", host.state, source, prompt_batch=batch)
            ).result(30)
    service.close()
    return runtime.prompts


def test_design_batches_share_every_stable_section(tmp_path, monkeypatch):
    first, second = design_prompts(tmp_path, monkeypatch)
    shared = prompts.shared_prefix_chars(first, second)
    print("design shared prefix chars:", shared, "of", len(first), len(second))
    parsed = json.loads(first)
    keys = list(parsed)
    stable_end = keys.index("approved_inputs")
    assert set(keys[:stable_end]) <= set(STABLE)
    stable = {k: parsed[k] for k in keys[:stable_end]}
    assert shared >= len(json.dumps(stable, separators=(",", ":"))) - 1, "stable sections lead the prompt"
    # the per-batch ID list is only in the late section, never in the schema near the top
    later = json.loads(second)
    assert "REQ-OTHER-BATCH-42" not in json.dumps(later["output_contract"])
    assert "REQ-OTHER-BATCH-42" in json.dumps(later["approved_traceability_ids"])
    assert "enum" not in parsed["output_contract"]["$defs"]["TraceabilityLinkDraft"]["properties"]["id"]


# --- response cache ---------------------------------------------------------------------------------------------


@pytest.fixture
def rt(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_RESPONSE_CACHE_DIR", str(tmp_path / "responses"))
    monkeypatch.delenv("HARNESS_RESPONSE_CACHE", raising=False)
    return runtime_module.MAFHarnessRuntime(
        api_key="k", model="m", skills_root=Path("skills"), base_url="http://test", skill_mode="preloaded"
    )


def key(rt, temperature=0.0, prompt="p"):
    return rt._response_cache_path("test_design", ["s"], "persona", prompt, DesignDraft, temperature)


def test_cache_key_is_content_addressed_and_only_for_temperature_zero(rt, monkeypatch):
    assert key(rt) == key(rt) and key(rt) != key(rt, prompt="q")
    assert key(rt, temperature=0.3) is None
    monkeypatch.setenv("HARNESS_RESPONSE_CACHE", "0")
    assert key(rt) is None


class ProviderReached(Exception):
    pass


def reach_provider(rt):
    def stop(*_args, **_kwargs):
        raise ProviderReached

    rt._create_agent = stop


def store(rt, path, artifact):
    rt._last_response["test_design"] = {
        "path": path,
        "result": {"status": "completed", "artifact": artifact},
        "hit": False,
    }


def run(rt, temperature=0.0):
    return rt.run_stage(
        stage="test_design",
        skill_name="s",
        persona="persona",
        prompt="p",
        state={"budget": {}},
        persist=lambda: None,
        response_format=DesignDraft,
        temperature=temperature,
    )


def test_hit_returns_the_validated_artifact_without_a_provider_call(rt):
    events = []
    rt._emit = lambda kind, **fields: events.append((kind, fields))
    path = key(rt)
    store(rt, path, {"a": 1})
    rt.settle_response_cache("test_design", True)
    assert path.is_file()
    assert asyncio.run(run(rt)) == {"status": "completed", "artifact": {"a": 1}}  # no agent is ever built
    kind, fields = events[0]
    assert kind == "cache_hit" and fields["stage"] == "test_design"
    assert fields["tokens"]["input"] == 0 and len(fields["prompt_sha256"]) == 64
    rt.settle_response_cache("test_design", False)  # host validation rejected the cached answer
    assert not path.exists()


def test_temperature_above_zero_bypasses_the_cache(rt):
    store(rt, key(rt), {"a": 1})
    rt.settle_response_cache("test_design", True)
    reach_provider(rt)
    with pytest.raises(ProviderReached):  # goes past the cache to the provider path
        asyncio.run(run(rt, temperature=0.3))


def test_disabled_by_env(rt, monkeypatch):
    path = key(rt)
    store(rt, path, {"a": 1})
    rt.settle_response_cache("test_design", True)
    monkeypatch.setenv("HARNESS_RESPONSE_CACHE", "0")
    reach_provider(rt)
    with pytest.raises(ProviderReached):  # the stored entry is ignored
        asyncio.run(run(rt))


def test_rejected_answers_are_never_stored(rt):
    path = key(rt)
    store(rt, path, {})
    rt.settle_response_cache("test_design", False)
    assert not path.exists()

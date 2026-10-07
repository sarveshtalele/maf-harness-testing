"""Case budget: host-side trimming, prompt text, cache signature, API rules and time recommendations."""

import asyncio
import json
from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime
from test_ui_api import approve, local_api, wait  # noqa: F401

from maf_test_harness.skills.contracts import finalize_design
from maf_test_harness.ui.eta import budget_recommendations
from maf_test_harness.workflow_steps.design import design_tests_batched

SKILLS = Path(__file__).parents[1] / "skills"
URL = "https://example.test/repo"


def _source(n=2):
    return {
        "requirement_model": {
            "requirements": [{"id": f"REQ-{i}", "text": f"r{i}", "evidence": "e"} for i in range(1, n + 1)],
            "validation_rules": [],
            "acceptance_criteria": [],
        },
        "api_contracts": [],
    }


def _with_files(source):
    for r in source["requirement_model"]["requirements"]:
        r["source_files"] = ["a/x.py"]
    return source


def _case(title, priority, *reqs):
    return {
        "title": title,
        "priority": priority,
        "level": "unit",
        "actor": "System",
        "target": "f",
        "description": "d",
        "preconditions": [],
        "data_notes": "",
        "expected_result": "ok",
        "steps": ["go"],
        "traceability_links": [{"type": "requirement", "id": r} for r in reqs],
    }


def _design(**sections):
    return {
        f"{s}_tests": sections.get(s, [])
        for s in ("positive", "negative", "boundary", "validation", "contract")
    }


def _titles(design):
    return {
        c["title"]
        for s in ("positive", "negative", "boundary", "validation", "contract")
        for c in design[f"{s}_tests"]
    }


def test_trim_orders_by_priority_then_type_and_records_gaps():
    draft = _design(
        positive=[_case("pos P2", "P2", "REQ-1"), _case("pos P1", "P1", "REQ-1")],
        negative=[_case("neg P1", "P1", "REQ-1"), _case("neg P0", "P0", "REQ-1")],
        boundary=[_case("bnd P1", "P1", "REQ-1")],
    )
    design = finalize_design(draft, _source(1), URL, SKILLS, case_budget=2)
    assert _titles(design) == {"neg P0", "pos P1"}
    trimmed = design["gaps_report"]["budget_trimmed"]
    assert len(trimmed["case_ids"]) == 3 and trimmed["reason"] == "case budget 2 per requirement"
    assert (
        design["gaps_report"]["uncovered_requirements"] == ["REQ-2"]
        or design["gaps_report"]["uncovered_requirements"] == []
    )
    assert design["summary"]["total_tests_generated"] == 2


def test_multi_requirement_case_counts_toward_each_and_requirements_never_empty():
    draft = _design(
        positive=[_case("shared", "P0", "REQ-1", "REQ-2"), _case("only1", "P1", "REQ-1")],
        negative=[_case("only2", "P2", "REQ-2")],
    )
    design = finalize_design(draft, _source(2), URL, SKILLS, case_budget=1)
    # shared fills both; only1/only2 are over budget for their sole requirement.
    assert _titles(design) == {"shared"}
    assert design["gaps_report"]["uncovered_requirements"] == []
    solo = finalize_design(
        _design(positive=[_case("a", "P2", "REQ-1")] * 1), _source(1), URL, SKILLS, case_budget=1
    )
    assert _titles(solo) == {"a"} and "budget_trimmed" not in solo["gaps_report"]


def test_no_budget_keeps_everything():
    draft = _design(positive=[_case(f"c{i}", "P1", "REQ-1") for i in range(4)])
    assert len(_titles(finalize_design(draft, _source(1), URL, SKILLS))) == 4


def test_cache_signature_changes_with_budget():
    from maf_test_harness.workflow_support import case_budget

    assert case_budget({}) == 2 and case_budget({"case_budget": {"per_requirement": 5}}) == 5
    assert case_budget({"case_budget": {"per_requirement": 99}}) == 2
    state = {
        "repo_url": URL,
        "artifacts": {"requirements": _with_files(_source(1)), "test_planning": {}},
        "case_budget": {"per_requirement": 1},
    }

    class Host:
        async def _model_artifact(self, stage, state, subset, prompt_batch):
            state["draft"] = {"positive_tests": []}

        async def validate(self, stage, state):
            return {}

        def audit(self, *a, **k):
            pass

        def save(self):
            pass

    asyncio.run(design_tests_batched(Host(), state))
    first = state["accepted_design_batches"]["signature"]
    state["case_budget"] = {"per_requirement": 3}
    asyncio.run(design_tests_batched(Host(), state))
    assert state["accepted_design_batches"]["signature"] != first


def test_recommendations_math():
    out = budget_recommendations(191, 140, 3, avg_cases=2.2, design_batches=16, per_design_batch_s=45)
    rows = {r["per_requirement"]: r for r in out["rows"]}
    assert rows[1]["cases"] == 191 and rows[1]["batches"] == 64 and rows[1]["generation_min"] == 149
    assert rows[3]["cases"] == round(2.2 * 191) and rows[4]["cases"] == rows[3]["cases"]
    assert rows[1]["design_min"] == 12
    assert out["recommended"] == 1  # nothing fits 60 min: minimum 1
    assert (
        budget_recommendations(20, 140, 3)["recommended"] == 4
    )  # 2.2*20=44 cases -> 15 batches = 35 min; k=3,4 both cap at 2.2


def test_api_budget_validation_prompt_and_lock_after_design_approval(local_api, monkeypatch):  # noqa: F811
    api = local_api
    for bad in (0, 11, "2", True, {"per_requirement": 0}):
        with pytest.raises(ValueError, match="1 to 10"):
            api.start(
                {
                    "repo_url": "https://github.com/acme/demo",
                    "case_budget": bad if isinstance(bad, dict) else {"per_requirement": bad},
                }
            )
    prompts = []
    original = ScriptedStageRuntime.run_stage

    async def spy(self, *, stage, state, **kwargs):
        if stage == "test_design" and "prompt" in kwargs:
            prompts.append(kwargs["prompt"])
        return await original(self, stage=stage, state=state, **kwargs)

    monkeypatch.setattr(ScriptedStageRuntime, "run_stage", spy)
    key = api.start({"repo_url": "https://github.com/acme/demo", "case_budget": {"per_requirement": 1}})["id"]
    wait(api)
    while api.read(key)["pending"]["stage"] != "test_design":
        approve(api, key)
    assert any("at most 1 case(s) per requirement" in p for p in prompts)
    run = api.read(key)
    assert run["state"]["case_budget"] == {"per_requirement": 1}
    assert run["progress"]["case_budget"]["current"] == 1 and len(run["progress"]["case_budget"]["rows"]) == 4
    with pytest.raises(ValueError, match="1 to 10"):
        api.set_case_budget(key, {"per_requirement": 11})
    assert api.set_case_budget(key, {"per_requirement": 3})["per_requirement"] == 3
    assert api.read(key)["state"]["case_budget"] == {"per_requirement": 3}
    audit = (api.run_path(key) / "audit.jsonl").read_text()
    assert any(json.loads(line)["event"] == "case_budget_changed" for line in audit.splitlines())
    approve(api, key)  # approve test_design
    with pytest.raises(RuntimeError, match="already approved"):
        api.set_case_budget(key, {"per_requirement": 2})


def test_config_exposes_default(local_api):  # noqa: F811
    assert local_api.config()["case_budget_default"] == 2

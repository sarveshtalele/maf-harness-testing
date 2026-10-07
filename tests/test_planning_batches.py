import asyncio

from maf_test_harness.models import TestPlan as PlanModel
from maf_test_harness.ui.eta import run_eta
from maf_test_harness.workflow_steps.planning_batches import (
    PLANNING_BATCH_SIZE,
    PLANNING_THRESHOLD,
    merge_plans,
    plan_tests_batched,
    planning_is_batched,
)


def _reqs(n, per_dir=20):
    items = [{"id": f"R{i}", "source_files": [f"pkg{i // per_dir}/m{i}.py"], "text": "t"} for i in range(n)]
    return {"requirement_model": {"requirements": items, "acceptance_criteria": [], "validation_rules": []}}


def _plan(tag, level="unit", risk="low", n_areas=1):
    return {
        "objective": f"obj {tag}",
        "recommended_frameworks": [f"fw{tag}", "pytest"],
        "setup_actions": [f"setup {tag}", "shared"],
        "areas": [
            {
                "name": f"{tag}{i}",
                "objective": "o",
                "level": level,
                "risk": risk,
                "evidence_files": [f"{tag}/f{i}.py"],
                "scenarios": [f"s{tag}{i}"],
            }
            for i in range(n_areas)
        ],
        "execution_order": ["shared", f"step {tag}"],
        "assumptions": [],
        "gaps": ["g"],
    }


class Host:
    def __init__(self):
        self.calls, self.events, self.runtime = [], [], None

    async def _model_artifact(self, stage, state, subset, prompt_batch):
        self.calls.append(prompt_batch)
        return _plan(str(prompt_batch))

    async def validate(self, stage, state):
        return PlanModel.model_validate(state["draft"]).model_dump()

    def audit(self, event, **kw):
        self.events.append((event, kw))

    def save(self):
        pass


def test_batched_threshold_and_resume_skips_accepted():
    # Small components are packed up to PLANNING_BATCH_SIZE requirements, so 2 x size + 1 gives 3 batches.
    state = {"artifacts": {"requirements": _reqs(2 * PLANNING_BATCH_SIZE + 1)}}
    assert planning_is_batched(_reqs(PLANNING_THRESHOLD + 1))
    assert not planning_is_batched(_reqs(PLANNING_THRESHOLD))
    host = Host()
    plan = asyncio.run(plan_tests_batched(host, state))
    assert host.calls == [1, 2, 3]
    assert [e[1]["batch"] for e in host.events] == [1, 2, 3]
    assert host.events[0][0] == "planning_batch_accepted" and host.events[0][1]["total_batches"] == 3
    assert plan["recommended_frameworks"] == ["fw1", "pytest", "fw2", "fw3"]
    state["accepted_planning_batches"]["batches"].pop("3")  # resume after a crash during batch 3
    host2 = Host()
    asyncio.run(plan_tests_batched(host2, state))
    assert host2.calls == [3]
    state["case_budget"] = {"per_requirement": 1}  # new signature: nothing reused
    host3 = Host()
    asyncio.run(plan_tests_batched(host3, state))
    assert host3.calls == [1, 2, 3]


def test_merge_deterministic_caps_and_schema_valid():
    plans = [
        _plan(str(i), level="unit" if i % 2 else "api", risk="high" if i == 7 else "low", n_areas=6)
        for i in range(10)
    ]
    plans[0]["setup_actions"] = [f"a{i}" for i in range(30)]
    merged = merge_plans(plans)
    assert merged == merge_plans(plans)
    plan = PlanModel.model_validate(merged)
    assert len(plan.areas) <= 24 and len(plan.setup_actions) == 20 and len(plan.recommended_frameworks) <= 8
    assert any(a.risk == "high" for a in plan.areas)
    assert all(len(a.scenarios) <= 8 and len(a.evidence_files) <= 8 for a in plan.areas)


def test_eta_planning_units(tmp_path):
    state = {
        "stages": ["test_planning"],
        "approved": {},
        "active_stage": "test_planning",
        "artifacts": {"requirements": _reqs(100)},
    }
    out = run_eta(tmp_path, {"state": state}, True, now=1767225600)["stages"][0]
    assert out["batch"] == {"done": 0, "total": 3} and out["remaining_s"] == 3 * 45  # 100 reqs packed by 40
    (tmp_path / "audit.jsonl").write_text(
        '{"event":"planning_batch_accepted","batch":2,"total_batches":4,"at":"2026-01-01T00:00:10+00:00"}\n'
    )
    out = run_eta(tmp_path, {"state": state}, True, now=1767225600)["stages"][0]
    assert out["batch"] == {"done": 2, "total": 4}
    small = {**state, "artifacts": {"requirements": _reqs(5)}}
    assert run_eta(tmp_path / "x", {"state": small}, True, now=1767225600)["stages"][0]["batch"] is None

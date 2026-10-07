"""Component-batched test planning: one compact plan per requirement batch, merged deterministically."""

from ..envs.platform_compat import raise_if_cancelled
from ..workflow_support import case_budget, digest
from .design import requirement_batches

PLANNING_THRESHOLD = 40  # above this many requirements one call risks truncation
PLANNING_BATCH_SIZE = 40
_RISK = {"low": 0, "medium": 1, "high": 2}


def planning_is_batched(requirements) -> bool:
    return len(requirements["requirement_model"]["requirements"]) > PLANNING_THRESHOLD


def _uniq(items, cap):
    return list(dict.fromkeys(i for i in items if i))[:cap]


def _component(area):
    files = area.get("evidence_files") or [""]
    return files[0].rpartition("/")[0]


def _merge_areas(areas, cap=24):
    """Concatenate; over the cap, fold areas sharing (level, component), then (level), keeping the higher risk."""
    for key in (lambda a: (a["level"], _component(a)), lambda a: (a["level"],)):
        if len(areas) <= cap:
            break
        folded: dict = {}
        for a in areas:
            into = folded.setdefault(key(a), {**a, "evidence_files": [], "scenarios": []})
            if _RISK[a["risk"]] > _RISK[into["risk"]]:
                into["risk"] = a["risk"]
            into["evidence_files"] = _uniq([*into["evidence_files"], *a["evidence_files"]], 8)
            into["scenarios"] = _uniq([*into["scenarios"], *a["scenarios"]], 8)
        areas = list(folded.values())
    return areas[:cap]


def merge_plans(plans):
    return {
        "objective": plans[0]["objective"],
        "recommended_frameworks": _uniq([f for p in plans for f in p["recommended_frameworks"]], 8),
        "setup_actions": _uniq([x for p in plans for x in p["setup_actions"]], 20),
        "areas": _merge_areas([a for p in plans for a in p["areas"]]),
        "execution_order": _uniq([x for p in plans for x in p["execution_order"]], 20),
        "assumptions": _uniq([x for p in plans for x in p["assumptions"]], 15),
        "gaps": _uniq([x for p in plans for x in p["gaps"]], 15),
    }


async def plan_tests_batched(host, state):
    requirements = state["artifacts"]["requirements"]
    ids = [r["id"] for r in requirements["requirement_model"]["requirements"]]
    signature = digest(
        {
            "ids": ids,
            "budget": case_budget(state),
            "model": getattr(getattr(host, "runtime", None), "model", None),
            "feedback": state.get("feedback", {}).get("test_planning"),
            "policy": "component-planning-v1",
        }
    )
    cache = state.setdefault("accepted_planning_batches", {})
    if cache.get("signature") != signature:
        cache.clear()
        cache.update(signature=signature, batches={})
    batches = list(requirement_batches(requirements, PLANNING_BATCH_SIZE))
    plans = []
    for index, (component, subset) in enumerate(batches, 1):
        raise_if_cancelled()
        plan = cache["batches"].get(str(index))
        if plan is None:
            plan = await host._model_artifact("test_planning", state, subset, prompt_batch=index)
            cache["batches"][str(index)] = plan
            host.audit(
                "planning_batch_accepted",
                stage="test_planning",
                component=component,
                batch=index,
                total_batches=len(batches),
                requirements=len(subset["requirement_model"]["requirements"]),
            )
            host.save()
        plans.append(plan)
    state["draft"] = merge_plans(plans)
    plan = await host.validate("test_planning", state)
    host.save()
    return plan

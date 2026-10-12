"""Component-batched test planning: one compact plan per requirement batch, merged deterministically."""

from ..envs.platform_compat import raise_if_cancelled
from ..qa.controls import area_id
from ..qa.ledger import remap_plan_subjects
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
            if a.get("origins") or "origins" in into:
                into["origins"] = _uniq([*into.get("origins", []), *a.get("origins", [])], 64)
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
    signature = digest(
        {
            "requirements": requirements,  # full content: revised text under the same IDs must re-plan
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
    batches = list(requirement_batches(requirements, PLANNING_BATCH_SIZE, pack=True))
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
        # Each batch numbers its areas from PLN-01, so an area's origin is "<batch>|<batch-local PLN ID>".
        plans.append(
            {
                **plan,
                "areas": [
                    {**a, "origins": [f"{index}|{a['area_id']}"]} if a.get("area_id") else dict(a)
                    for a in plan.get("areas") or []
                ],
            }
        )
    merged = merge_plans(plans)
    state["draft"] = merged
    plan = await host.validate("test_planning", state)
    # Plan confidence records were written under batch-local IDs; move them to the FINAL merged area IDs.
    mapping: dict[tuple[int, str], str] = {}
    for index, area in enumerate(merged["areas"], 1):
        for origin in area.get("origins", []):
            batch, local = origin.split("|", 1)
            mapping[(int(batch), local)] = area_id(index)
    if getattr(host, "run_dir", None) is not None:  # the ledger lives in the run folder
        remap_plan_subjects(host.run_dir, mapping)
    host.save()
    return plan

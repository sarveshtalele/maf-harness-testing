"""Component-batched design without dropping the approved requirement inventory."""

from collections import defaultdict
from pathlib import PurePosixPath

from ..envs.platform_compat import raise_if_cancelled
from ..workflow_support import digest

SECTIONS = ("positive", "negative", "boundary", "validation", "contract")
DESIGN_BATCH_SIZE = 12


def requirement_batches(requirements):
    groups = defaultdict(list)
    for requirement in requirements["requirement_model"]["requirements"]:
        groups[str(PurePosixPath(requirement["source_files"][0]).parent)].append(requirement)
    first = True
    for component, items in sorted(groups.items()):
        for start in range(0, len(items), DESIGN_BATCH_SIZE):
            selected = items[start : start + DESIGN_BATCH_SIZE]
            ids = {r["id"] for r in selected}
            model = requirements["requirement_model"]
            yield (
                component,
                {
                    **requirements,
                    "requirement_model": {
                        **model,
                        "requirements": selected,
                        "acceptance_criteria": [
                            c for c in model.get("acceptance_criteria", []) if c["requirement_id"] in ids
                        ],
                        "validation_rules": model.get("validation_rules", []) if first else [],
                    },
                    "api_contracts": requirements.get("api_contracts", []) if first else [],
                },
            )
            first = False


async def design_tests_batched(host, state):
    requirements = state["artifacts"]["requirements"]
    signature = digest(
        {
            "requirements": requirements,
            "plan": state["artifacts"]["test_planning"],
            "feedback": state.get("feedback", {}).get("test_design"),
            "policy": "strict-component-design-v1",
        }
    )
    cache = state.setdefault("accepted_design_batches", {})
    if cache.get("signature") != signature:
        cache.clear()
        state["design_reviews"] = {}
        cache.update(signature=signature, batches={})
    batches = list(requirement_batches(requirements))
    merged = {f"{section}_tests": [] for section in SECTIONS}
    merged["gaps"] = []
    for index, (component, subset) in enumerate(batches, 1):
        raise_if_cancelled()
        draft = cache["batches"].get(str(index))
        if draft is None:
            await host._model_artifact("test_design", state, subset, prompt_batch=index)
            draft = state["draft"]
            cache["batches"][str(index)] = draft
            host.audit(
                "design_batch_accepted",
                stage="test_design",
                component=component,
                batch=index,
                total_batches=len(batches),
                requirements=len(subset["requirement_model"]["requirements"]),
            )
            host.save()
        for section in SECTIONS:
            merged[f"{section}_tests"].extend(draft.get(f"{section}_tests", []))
        merged["gaps"].extend(draft.get("gaps", []))
    state["draft"] = merged
    design = await host.validate("test_design", state)
    design["batching"] = {
        "batch_count": len(batches),
        "max_requirements_per_call": DESIGN_BATCH_SIZE,
        "approved_requirements": len(requirements["requirement_model"]["requirements"]),
    }
    host.save()
    return design

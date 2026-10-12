"""Component-batched design without dropping the approved requirement inventory."""

from collections import defaultdict
from pathlib import PurePosixPath

from ..envs.platform_compat import raise_if_cancelled
from ..qa.oracles import is_gap
from ..workflow_support import case_budget, digest

SECTIONS = ("positive", "negative", "boundary", "validation", "contract")
DESIGN_BATCH_SIZE = 12


def _label(components: list[str]) -> str:
    return components[0] if len(components) == 1 else f"{components[0]} (+{len(components) - 1} more)"


def requirement_batches(requirements, size=DESIGN_BATCH_SIZE, pack=False):
    """Requirement subsets per component (``pack``: small components share a batch up to ``size``)."""
    groups = defaultdict(list)
    for requirement in requirements["requirement_model"]["requirements"]:
        if is_gap(requirement):
            continue  # demoted by the host: listed as a gap, never a case target
        groups[str(PurePosixPath(requirement["source_files"][0]).parent)].append(requirement)
    chunks: list[tuple[str, list]] = []
    if pack:
        names: list[str] = []
        current: list = []
        for component, items in sorted(groups.items()):
            for requirement in items:
                if len(current) == size:
                    chunks.append((_label(names), current))
                    names, current = [], []
                if component not in names:
                    names.append(component)
                current.append(requirement)
        if current:
            chunks.append((_label(names), current))
    else:
        for component, items in sorted(groups.items()):
            chunks.extend((component, items[i : i + size]) for i in range(0, len(items), size))
    first = True
    for component, selected in chunks:
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
            "case_budget": case_budget(state),
        }
    )
    cache = state.setdefault("accepted_design_batches", {})
    if cache.get("signature") != signature:
        cache.clear()
        state["design_reviews"] = {}
        cache.update(signature=signature, batches={})
    batches = list(requirement_batches(requirements, pack=True))
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

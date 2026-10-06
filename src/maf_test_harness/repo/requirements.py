"""Evidence-bound requirements from deterministic reverse-engineering facts."""

from collections import defaultdict
from pathlib import PurePosixPath

from ..models import RequirementsDraft, RequirementsInventoryDraft


def requirements_from_facts(analysis: dict, app_name: str, knowledge: str = "") -> dict:
    facts = analysis.get("facts", [])
    if not facts:
        raise ValueError(
            "No supported callable declarations were found. Requirements cannot be invented. Review source coverage and provide documented behavior or support for this language before retrying."
        )
    requirements = []
    gaps = list(analysis.get("gaps", []))
    selected = sorted(facts, key=lambda f: (f["source_file"], f["line"], f["symbol"]))
    for fact in selected:
        confidence = fact["confidence"]
        requirements.append(
            {
                "text": f"{fact['source_file']}:{fact['line']} contains {'a declaration candidate' if confidence != 'ast' else 'a callable declaration'} for {fact['symbol']}; review its source-defined behavior before designing tests.",
                "evidence": f"{fact['source_file']}:{fact['line']} ({confidence} declaration): {fact['evidence']}"[
                    :900
                ],
                "source_files": [fact["source_file"]],
                "category": "functional",
                "actor": "System",
                "source_symbol": fact["symbol"],
                "source_line": fact["line"],
            }
        )
    if any(f["confidence"] != "ast" for f in selected):
        gaps.append(
            "Non-Python declaration candidates use heuristics and need human verification; they are not semantic guarantees."
        )
    if knowledge:
        gaps.append(
            "User-pasted knowledge is retained as unverified context, not converted automatically into source-proven requirements."
        )
    # Do not invent status codes, constraints, actors, acceptance criteria or security assertions.
    draft = {
        "app_name": app_name,
        "summary": "Deterministic, source-linked callable surface inventory. Review the listed gaps before planning; this is not complete semantic reverse engineering.",
        "actors": [
            {
                "name": "System",
                "description": "Caller of the declared code interface; no end-user role is inferred.",
                "source_files": sorted({f["source_file"] for f in selected})[:6],
            }
        ],
        "requirements": requirements,
        "acceptance_criteria": [],
        "validation_rules": [],
        "api_contracts": [],
        "environment_needs": [],
        "exclusions": ["Runtime or compliance guarantees not proven by source declarations."],
        "assumptions": [],
        "gaps": gaps[:12],
    }
    # Validate bounded component batches, then merge all declarations without a total-run cap.
    components = defaultdict(list)
    for requirement in requirements:
        components[str(PurePosixPath(requirement["source_files"][0]).parent)].append(requirement)
    batches = []
    for component, items in sorted(components.items()):
        for start in range(0, len(items), 30):
            batch = RequirementsDraft.model_validate({**draft, "requirements": items[start : start + 30]})
            batches.append({"component": component, "requirements": len(batch.requirements)})
    draft["intake"] = {
        "discovered_declarations": len(facts),
        "represented_declarations": len(requirements),
        "declaration_intake_percent": round(100 * len(requirements) / len(facts), 2),
        "represented_source_files": len({f["source_file"] for f in facts}),
        "source_inventory": analysis.get("coverage", {}),
        "batches": batches,
        "scope": "All discovered declarations, not measured behavior or runtime coverage.",
    }
    return RequirementsInventoryDraft.model_validate(draft).model_dump()

"""Evidence-bound requirements from deterministic reverse-engineering facts."""

from ..models import RequirementsDraft


def requirements_from_facts(analysis: dict, app_name: str, knowledge: str = "") -> dict:
    facts = analysis.get("facts", [])
    if not facts:
        raise ValueError(
            "No supported callable declarations were found. Requirements cannot be invented. Review source coverage and provide documented behavior or support for this language before retrying."
        )
    requirements = []
    gaps = list(analysis.get("gaps", []))
    selected = facts[:30]
    for fact in selected:
        confidence = fact["confidence"]
        requirements.append(
            {
                "text": f"{fact['source_file']} contains {'a declaration candidate' if confidence != 'ast' else 'a callable declaration'} for {fact['symbol']}; review its source-defined behavior before designing tests.",
                "evidence": f"{fact['source_file']}:{fact['line']} ({confidence} declaration): {fact['evidence']}"[
                    :900
                ],
                "source_files": [fact["source_file"]],
                "category": "functional",
                "actor": "System",
            }
        )
    if any(f["confidence"] != "ast" for f in selected):
        gaps.append(
            "Non-Python declaration candidates use heuristics and need human verification; they are not semantic guarantees."
        )
    if len(facts) > 30:
        gaps.append(
            f"Requirement limit reached: 30/{len(facts)} declarations represented. Remaining declarations are retained in repository analysis."
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
    return RequirementsDraft.model_validate(draft).model_dump()

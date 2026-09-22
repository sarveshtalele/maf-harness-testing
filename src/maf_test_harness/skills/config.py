"""Which Agent Skills each workflow stage loads, and why.

Every stage has one *primary* skill (its contract) and zero or more *companion* skills that apply only when the
repository or the approved design calls for them. Selection is deterministic host logic over facts, never a model
decision; the runtime advertises exactly the selected skills and verifies that every one was loaded through MAF.
"""

from __future__ import annotations

from dataclasses import dataclass

# Kept for the stages that have no companions and for callers that need the primary name.
SKILL_NAMES = {
    "requirements": "repository-requirements",
    "test_planning": "test-planning",
    "test_design": "test-design-generator",
    "test_generation": "generic-test-generator",
}

# What each companion is for; shown to the model so it applies the skill for that purpose only.
COMPANION_PURPOSE = {
    "api-contract-analyzer": "structure explicit HTTP contracts (operation IDs, status codes, parameters, constraints) from an API specification file",
    "test-data-generator": "make test data deterministic: seeds, stable IDs, fixtures, payload shapes",
    "junit-generator": "follow JUnit conventions (naming, assertions, fixtures) for Java/Kotlin tests",
    "playwright-test-generator": "write resilient Playwright browser tests (locators, waits, assertions) for ui/e2e cases",
}


@dataclass(frozen=True)
class SkillSet:
    primary: str
    companions: tuple[str, ...] = ()

    @property
    def all(self) -> list[str]:
        return [self.primary, *self.companions]


def skills_for(stage: str, state: dict) -> SkillSet:
    """The skills a stage must load for this run (deterministic over the stack profile and approved design)."""
    primary = SKILL_NAMES[stage]
    companions: list[str] = []
    stack = state.get("stack") or {}
    languages = set((state.get("artifacts", {}).get("repo_analysis") or {}).get("languages") or {})
    if stage == "requirements" and stack.get("openapi_files"):
        companions.append("api-contract-analyzer")
    if stage in {"test_design", "test_generation"}:
        companions.append("test-data-generator")
    if stage == "test_generation":
        if languages & {"Java", "Kotlin"}:
            companions.append("junit-generator")
        design = state.get("artifacts", {}).get("test_design") or {}
        levels = {
            c.get("level")
            for k in ("positive", "negative", "boundary", "validation", "contract")
            for c in design.get(f"{k}_tests", []) or []
        }
        if levels & {"ui", "e2e", "accessibility"}:
            companions.append("playwright-test-generator")
    return SkillSet(primary, tuple(companions))

"""Which Agent Skills each workflow stage loads, and why.

Selection is manifest-driven: every skill folder carries a ``plugin.yaml`` (see docs/SKILL_PLUGINS.md) declaring its
stages, role and when it applies. Each stage has one *primary* skill (its contract) and zero or more *companions*
that apply only when the repository or approved design calls for them. Selection is deterministic host logic over
facts, never a model decision. Dropping a new skill folder into ``skills/`` registers it with no code change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .registry import SkillPlugin, discover_skills
from .trust import plugin_dir, trust

ROOT = Path(__file__).resolve().parents[3]
STAGE_ORDER = (
    "repo_analysis",
    "requirements",
    "test_planning",
    "test_design",
    "test_generation",
    "environment_setup",
    "test_execution",
    "qa",
)


def plugins() -> list[SkillPlugin]:
    """Valid, trusted plugins from the repository ``skills/`` folder plus the user plugin directory. Unverified skills
    (no lock entry, or any user plugin) take part in selection only after a user approved their current files."""
    repo = ROOT / "skills"
    return [p for p in discover_skills([repo, plugin_dir()]) if p.ok and trust(repo, p.path)["trusted"]]


def _repo_primaries(kind: str) -> dict[str, str]:
    """Primaries from the repository skills only (import-time constants; user plugins are picked up live)."""
    found: dict[str, SkillPlugin] = {}
    for p in discover_skills([ROOT / "skills"]):
        if p.ok and p.role == "primary" and p.kind == kind:
            for s in p.stages:
                if s not in found or p.priority > found[s].priority:
                    found[s] = p
    return {s: found[s].name for s in STAGE_ORDER if s in found}


# Derived from the manifests; kept for callers that need the primary name per stage.
SKILL_NAMES = _repo_primaries("model")
HOST_SKILL_NAMES = _repo_primaries("host")
# What each companion is for; shown to the model so it applies the skill for that purpose only.
COMPANION_PURPOSE = {
    p.name: p.purpose for p in discover_skills([ROOT / "skills"]) if p.ok and p.role == "companion"
}


def purpose_of(name: str) -> str | None:
    return next((p.purpose for p in plugins() if p.name == name), None)


@dataclass(frozen=True)
class SkillSet:
    primary: str
    companions: tuple[str, ...] = ()

    @property
    def all(self) -> list[str]:
        return [self.primary, *self.companions]


@lru_cache(maxsize=256)
def _glob_re(pattern: str) -> re.Pattern:
    """Glob with ``**``, ``*``, ``?`` and ``{a,b}`` to a regex over posix-style relative paths."""
    out, i, depth = "", 0, 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
            continue
        if c == "*":
            out += ".*" if pattern.startswith("**", i) else "[^/]*"
            i += 2 if pattern.startswith("**", i) else 1
            continue
        if c == "{":
            out, depth = out + "(?:", depth + 1
        elif c == "}" and depth:
            out, depth = out + ")", depth - 1
        elif c == "," and depth:
            out += "|"
        else:
            out += "." if c == "?" else re.escape(c)
        i += 1
    return re.compile(out + r"\Z")


def _facts(state: dict) -> dict:
    artifacts = state.get("artifacts", {})
    repo = artifacts.get("repo_analysis") or {}
    stack = state.get("stack") or {}
    design = artifacts.get("test_design") or {}
    return {
        "languages": set(repo.get("languages") or {}),
        "frameworks": {*(repo.get("frameworks") or []), *(stack.get("frameworks") or [])},
        "levels": {
            c.get("level")
            for k in ("positive", "negative", "boundary", "validation", "contract")
            for c in design.get(f"{k}_tests", []) or []
        },
        "files": [str(f).replace("\\", "/") for f in [*(repo.get("source_files") or []), *(repo.get("test_files") or [])]],
        "stack": stack,
        "run_modes": {state.get("run_mode") or "brownfield"},
    }


def _applies(plugin: SkillPlugin, facts: dict) -> bool:
    """AND across keys, OR within a key's list."""
    for key, wanted in plugin.applies_when.items():
        if key == "stack_flags":
            ok = any(facts["stack"].get(flag) for flag in wanted)
        elif key == "files_glob":
            ok = any(_glob_re(g).match(f) for g in wanted for f in facts["files"])
        else:
            ok = bool(facts[key] & set(wanted))
        if not ok:
            return False
    return True


def skills_for(stage: str, state: dict) -> SkillSet:
    """The skills a stage must load for this run (deterministic over the stack profile and approved design)."""
    host = stage == "repo_analysis" or (stage == "requirements" and state.get("requirements_mode") == "deterministic")
    available = plugins()
    primaries = [p for p in available if p.role == "primary" and stage in p.stages and (p.kind == "host") == host]
    if not primaries:
        raise KeyError(stage)
    primary = max(primaries, key=lambda p: (p.priority, p.name))
    if host:
        return SkillSet(primary.name)
    facts = _facts(state)
    companions = sorted(
        (
            p
            for p in available
            if p.role == "companion" and p.kind == "model" and stage in p.stages and _applies(p, facts)
        ),
        key=lambda p: (-p.priority, p.name),
    )
    return SkillSet(primary.name, tuple(p.name for p in companions))

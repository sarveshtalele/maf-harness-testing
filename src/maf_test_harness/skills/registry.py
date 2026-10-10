"""Agent Skills specification lint and provenance for the bundled skill packages.

Rules follow https://agentskills.io/specification and the Microsoft Agent Framework
"Agent Skills" documentation: ``name`` matches the directory, is 1-64 chars of lowercase
letters/digits/single hyphens; ``description`` is 1-1024 chars; ``compatibility`` <= 500;
``SKILL.md`` should stay under 500 lines.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)


@dataclass
class SkillReport:
    name: str
    path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sha256: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


def tree_sha256(skill_dir: Path) -> str:
    """Stable hash over every file in the skill package (path + bytes)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in skill_dir.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(path.relative_to(skill_dir).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def lint_skill(skill_dir: Path) -> SkillReport:
    report = SkillReport(name=skill_dir.name, path=str(skill_dir))
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        report.errors.append("SKILL.md is missing")
        return report
    text = skill_file.read_text(encoding="utf-8")
    report.sha256 = tree_sha256(skill_dir)
    match = FRONTMATTER_RE.match(text)
    if not match:
        report.errors.append("SKILL.md has no YAML frontmatter")
        return report
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        report.errors.append(f"frontmatter is not valid YAML: {exc}")
        return report
    if not isinstance(meta, dict):
        report.errors.append("frontmatter must be a mapping")
        return report
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not (1 <= len(name) <= 64) or not NAME_RE.fullmatch(name):
        report.errors.append("name must be 1-64 chars of lowercase letters/digits with single hyphens")
    elif name != skill_dir.name:
        report.errors.append(f"name {name!r} must match directory {skill_dir.name!r}")
    if not isinstance(description, str) or not description.strip():
        report.errors.append("description is required")
    elif len(description) > 1024:
        report.errors.append("description exceeds 1024 characters")
    compatibility = meta.get("compatibility")
    if compatibility is not None and (not isinstance(compatibility, str) or len(compatibility) > 500):
        report.errors.append("compatibility must be a string of at most 500 characters")
    metadata = meta.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            report.errors.append("metadata must be a mapping")
        elif any(not isinstance(v, (str, int, float, bool)) for v in metadata.values()):
            report.warnings.append("metadata values should be scalars; nested values are skipped by MAF")
    allowed = meta.get("allowed-tools")
    if allowed is not None and not isinstance(allowed, str):
        report.errors.append("allowed-tools must be a single space-delimited string, not a list")
    lines = len(text.splitlines())
    if lines > 500:
        report.errors.append(f"SKILL.md has {lines} lines; keep at most 500 and move detail to references/")
    return report


def lint_all(skills_root: Path) -> list[SkillReport]:
    return [
        lint_skill(p)
        for p in sorted(Path(skills_root).iterdir())
        if p.is_dir() and not p.name.startswith(".")
    ]


LOCK_FILE = "SKILLS.lock.json"


def load_lock(skills_root: Path) -> dict:
    """Upstream provenance: repository URL/commit, per-file hashes, audit results, reviewed-script allow-list."""
    path = Path(skills_root) / LOCK_FILE
    return (
        json.loads(path.read_text(encoding="utf-8"))
        if path.is_file()
        else {"skills": {}, "reviewed_scripts": {}}
    )


def verify_lock(skills_root: Path, names: list[str] | None = None) -> list[str]:
    """Problems for upstream skills whose files differ from the lock (edited, added, removed)."""
    root = Path(skills_root)
    problems = []
    for name, entry in load_lock(root).get("skills", {}).items():
        if names is not None and name not in names:
            continue
        directory = root / name
        current = {
            p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts
        }
        for rel in sorted(set(entry["files"]) | set(current)):
            if entry["files"].get(rel) != current.get(rel):
                kind = (
                    "missing"
                    if rel not in current
                    else "unexpected file"
                    if rel not in entry["files"]
                    else "modified"
                )
                problems.append(f"{name}/{rel}: {kind} (differs from {LOCK_FILE})")
    return problems


def reviewed_scripts(skills_root: Path, tier: str | None = None) -> dict[tuple[str, str], str]:
    """{(skill, relative script path): sha256} for scripts that were reviewed and may be executed."""
    return {
        (skill, rel): info["sha256"]
        for skill, scripts in load_lock(Path(skills_root)).get("reviewed_scripts", {}).items()
        for rel, info in scripts.items()
        if tier is None or info["tier"] == tier
    }


def require_valid(skills_root: Path, names: list[str], extra_roots=()) -> dict[str, str]:
    """Fail fast if an assigned skill is missing, invalid or (for upstream skills) modified; return package hashes.
    ``extra_roots`` (user plugin dirs) are searched after ``skills_root``; the first folder of a name wins."""
    reports: dict[str, SkillReport] = {}
    for root in (skills_root, *(r for r in extra_roots if Path(r).is_dir())):
        for r in lint_all(root):
            reports.setdefault(r.name, r)
    tampered = verify_lock(skills_root, names)
    if tampered:
        raise ValueError("Vendored Agent Skill modified after review: " + "; ".join(tampered[:5]))
    hashes: dict[str, str] = {}
    for name in names:
        report = reports.get(name)
        if report is None:
            raise ValueError(f"Assigned Agent Skill not found: {name}")
        if not report.ok:
            raise ValueError(
                f"Agent Skill {name} violates the Agent Skills specification: {'; '.join(report.errors)}"
            )
        hashes[name] = report.sha256
    return hashes


# ---- Skill plugins: plugin.yaml next to SKILL.md (docs/SKILL_PLUGINS.md) ------------------------------------------

PLUGIN_FILE = "plugin.yaml"
STAGE_NAMES = (
    "repo_analysis",
    "requirements",
    "test_planning",
    "test_design",
    "test_generation",
    "environment_setup",
    "test_execution",
    "qa",
)
PLUGIN_KEYS = {"name", "stages", "role", "kind", "purpose", "applies_when", "entrypoint", "priority"}
APPLIES_KEYS = {"languages", "frameworks", "levels", "files_glob", "stack_flags", "run_modes"}
ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


@dataclass(frozen=True)
class SkillPlugin:
    name: str
    path: Path
    stages: tuple[str, ...] = ()
    role: str = "companion"
    kind: str = "model"
    purpose: str = ""
    applies_when: dict = field(default_factory=dict)
    entrypoint: str = ""
    priority: int = 0
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def _str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v for v in value)


def load_plugin(skill_dir: Path) -> SkillPlugin:
    """Parse and strictly validate ``plugin.yaml``; problems are returned in ``errors`` (never raised)."""
    skill_dir = Path(skill_dir)
    errors: list[str] = []
    file = skill_dir / PLUGIN_FILE
    meta: dict = {}
    if not file.is_file():
        errors.append(f"{PLUGIN_FILE} is missing")
    else:
        try:
            loaded = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            loaded = None
            errors.append(f"{PLUGIN_FILE} is not valid YAML: {exc}")
        if loaded is not None and not isinstance(loaded, dict):
            errors.append(f"{PLUGIN_FILE} must be a mapping")
        elif isinstance(loaded, dict):
            meta = loaded
    if meta:
        for key in sorted(set(meta) - PLUGIN_KEYS):
            errors.append(f"unknown key {key!r}")
        if meta.get("name") != skill_dir.name:
            errors.append(f"name {meta.get('name')!r} must match folder {skill_dir.name!r}")
        stages = meta.get("stages")
        if not _str_list(stages):
            errors.append("stages must be a list of stage names")
        else:
            errors += [f"unknown stage {s!r}" for s in stages if s not in STAGE_NAMES]
        if meta.get("role") not in ("primary", "companion"):
            errors.append("role must be 'primary' or 'companion'")
        if meta.get("kind") not in ("model", "host"):
            errors.append("kind must be 'model' or 'host'")
        if not isinstance(meta.get("purpose"), str) or not meta["purpose"].strip() or "\n" in meta["purpose"].strip():
            errors.append("purpose must be a non-empty single line")
        if meta.get("role") == "primary" and (not _str_list(stages) or not stages):
            errors.append("a primary skill needs at least one stage")
        entry = meta.get("entrypoint", "")
        if entry and (not isinstance(entry, str) or not ENTRYPOINT_RE.fullmatch(entry)):
            errors.append("entrypoint must look like 'package.module:function'")
        if entry and meta.get("kind") != "host":
            errors.append("entrypoint is only valid for kind 'host'")
        priority = meta.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            errors.append("priority must be an integer")
        applies = meta.get("applies_when", {})
        if not isinstance(applies, dict):
            errors.append("applies_when must be a mapping")
            applies = {}
        else:
            errors += [f"unknown applies_when key {k!r}" for k in sorted(set(applies) - APPLIES_KEYS)]
            errors += [f"applies_when.{k} must be a list of strings" for k, v in applies.items() if not _str_list(v)]
            if applies and meta.get("role") == "primary":
                errors.append("applies_when is only valid for companion skills")
    return SkillPlugin(
        name=skill_dir.name,
        path=skill_dir,
        stages=tuple(meta["stages"]) if _str_list(meta.get("stages")) else (),
        role=str(meta.get("role", "companion")),
        kind=str(meta.get("kind", "model")),
        purpose=str(meta.get("purpose", "")).strip(),
        applies_when=meta["applies_when"] if isinstance(meta.get("applies_when"), dict) else {},
        entrypoint=str(meta.get("entrypoint") or ""),
        priority=meta["priority"] if isinstance(meta.get("priority"), int) else 0,
        errors=tuple(errors),
    )


_DISCOVERY_CACHE: dict[tuple[str, ...], tuple[tuple, list[SkillPlugin]]] = {}


def discover_skills(roots) -> list[SkillPlugin]:
    """Every skill folder under the given roots (repo ``skills/`` plus an optional user plugin dir), cached per
    process and re-read when a folder or its plugin.yaml changes. A folder in an earlier root shadows a later one."""
    roots = tuple(Path(r).resolve() for r in roots if r)
    dirs = [
        p
        for r in roots
        if r.is_dir()
        for p in sorted(r.iterdir())
        if p.is_dir() and not p.name.startswith(".") and p.name != "__pycache__"
    ]
    stamp = tuple(
        (str(p), p.stat().st_mtime_ns, (p / PLUGIN_FILE).stat().st_mtime_ns if (p / PLUGIN_FILE).is_file() else 0)
        for p in dirs
    ) + tuple((str(r), r.stat().st_mtime_ns) for r in roots if r.is_dir())
    key = tuple(map(str, roots))
    cached = _DISCOVERY_CACHE.get(key)
    if cached and cached[0] == stamp:
        return list(cached[1])
    seen: set[str] = set()
    plugins = []
    for p in dirs:
        if p.name not in seen:
            seen.add(p.name)
            plugins.append(load_plugin(p))
    _DISCOVERY_CACHE[key] = (stamp, plugins)
    return list(plugins)


def lint_plugins(plugins: list[SkillPlugin]) -> dict[str, list[str]]:
    """Per-skill plugin problems: schema errors, unimportable host entrypoints, and more than one primary per
    (stage, kind) among the given plugins."""
    import importlib

    problems = {p.name: list(p.errors) for p in plugins}
    for p in plugins:
        if p.ok and p.entrypoint:
            module, _, func = p.entrypoint.partition(":")
            try:
                if not callable(getattr(importlib.import_module(module), func, None)):
                    problems[p.name].append(f"entrypoint {p.entrypoint!r} is not callable")
            except Exception as exc:  # noqa: BLE001 - any import failure is a lint finding
                problems[p.name].append(f"entrypoint {p.entrypoint!r} is not importable: {exc}")
    primaries: dict[tuple[str, str], list[str]] = {}
    for p in plugins:
        if p.ok and p.role == "primary":
            for stage in p.stages:
                primaries.setdefault((stage, p.kind), []).append(p.name)
    for (stage, kind), names in primaries.items():
        if len(names) > 1:
            for n in names:
                problems[n].append(f"more than one {kind} primary for stage {stage}: {', '.join(sorted(names))}")
    return problems

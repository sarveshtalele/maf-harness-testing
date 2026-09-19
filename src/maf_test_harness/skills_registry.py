"""Agent Skills specification lint and provenance for the bundled skill packages.

Rules follow https://agentskills.io/specification and the Microsoft Agent Framework
"Agent Skills" documentation: ``name`` matches the directory, is 1-64 chars of lowercase
letters/digits/single hyphens; ``description`` is 1-1024 chars; ``compatibility`` <= 500;
``SKILL.md`` should stay under 500 lines.
"""
from __future__ import annotations

import argparse
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
    body_lines = len(text[match.end():].splitlines())
    if body_lines > 500:
        report.warnings.append(f"SKILL.md body has {body_lines} lines; keep under 500 and move detail to references/")
    return report


def lint_all(skills_root: Path) -> list[SkillReport]:
    return [lint_skill(p) for p in sorted(Path(skills_root).iterdir()) if p.is_dir() and not p.name.startswith(".")]


def require_valid(skills_root: Path, names: list[str]) -> dict[str, str]:
    """Fail fast if an assigned skill is missing or invalid; return {name: package sha256}."""
    reports = {r.name: r for r in lint_all(skills_root)}
    hashes: dict[str, str] = {}
    for name in names:
        report = reports.get(name)
        if report is None:
            raise ValueError(f"Assigned Agent Skill not found: {name}")
        if not report.ok:
            raise ValueError(f"Agent Skill {name} violates the Agent Skills specification: {'; '.join(report.errors)}")
        hashes[name] = report.sha256
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skills_root", nargs="?", default="skills", type=Path)
    args = parser.parse_args()
    reports = lint_all(args.skills_root)
    print(json.dumps([r.__dict__ | {"ok": r.ok} for r in reports], indent=2))
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Execution of Agent Skill scripts under a reviewed allow-list.

MAF treats skill scripts as third-party code: they need a ``script_runner`` and an approval. The harness's policy:

* only scripts recorded in ``skills/SKILLS.lock.json`` (``reviewed_scripts``) may run, and only if their bytes still
  match the recorded SHA-256 — a modified script is refused, never executed;
* skills outside the lock (unlocked repo folders, user plugins) have no reviewed scripts; once a user approves one
  (``trust.approve``) its ``scripts/`` run as ``model`` tier, only while its whole-folder hash still matches the approval;
* two tiers: ``model`` scripts (pure functions of their arguments) may be called by an agent through MAF's
  ``run_skill_script``; ``host`` scripts are run only by the harness itself, on its own artifacts;
* every run is argv-only, in a fresh temporary directory, with a sanitized environment and a timeout, and is
  recorded (skill, script, hash, argv, exit code) for the audit trail.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_framework import Content, SkillsProvider

from ..envs.execution import run_command
from .registry import discover_skills, reviewed_scripts
from .trust import is_approved, is_verified

SCRIPT_TIMEOUT_SECONDS = 30
MAX_OUTPUT_CHARS = 20_000
INTERPRETERS = {".py": [sys.executable], ".js": ["node"]}


class ScriptRefused(RuntimeError):
    pass


class HarnessScriptRunner:
    """MAF ``SkillScriptRunner`` and the host's own script executor, both gated by the same allow-list."""

    def __init__(
        self, skills_root: Path, tiers: tuple[str, ...] = ("model",), plugin_roots: tuple[Path, ...] = ()
    ):
        self.tiers = tiers
        self.skills_root = Path(skills_root).resolve()
        self.allowed = {
            key: sha for tier in tiers for key, sha in reviewed_scripts(self.skills_root, tier).items()
        }
        self.dirs: dict[str, Path] = {}  # approved unverified skills: name -> folder
        if "model" in tiers:
            for plugin in discover_skills([self.skills_root, *plugin_roots]):
                directory = plugin.path.resolve()
                if is_verified(self.skills_root, directory) or not is_approved(directory):
                    continue
                self.dirs[plugin.name] = directory
                for script in sorted((directory / "scripts").rglob("*")):
                    if script.is_file() and not script.is_symlink() and script.suffix in INTERPRETERS:
                        self.allowed[(plugin.name, script.relative_to(directory).as_posix())] = hashlib.sha256(
                            script.read_bytes()
                        ).hexdigest()
        self.runs: list[dict[str, Any]] = []

    # ---- policy
    def check(self, skill: str, rel: str) -> Path:
        base = self.dirs.get(skill, self.skills_root / skill)
        path = (base / rel).resolve()
        expected = self.allowed.get((skill, rel))
        if expected is None:
            raise ScriptRefused(f"Script {skill}/{rel} is not on the reviewed allow-list for this caller.")
        if skill in self.dirs and not is_approved(base):
            raise ScriptRefused(f"Skill {skill} changed since it was approved; refusing to run its scripts.")
        if not path.is_relative_to(base) or not path.is_file():
            raise ScriptRefused(f"Script {skill}/{rel} does not exist inside its skill.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ScriptRefused(f"Script {skill}/{rel} changed since it was reviewed; refusing to run it.")
        return path

    @staticmethod
    def _clean_args(args: Any) -> list[str]:
        if args is None:
            return []
        if isinstance(args, dict):
            raise ScriptRefused("Pass script arguments as a list of strings.")
        cleaned = []
        for arg in args:
            text = str(arg)
            if len(text) > 300 or "\0" in text or ".." in text or text.startswith(("/", "~")):
                raise ScriptRefused(
                    f"Argument not allowed: {text[:40]!r} (no paths outside the working directory)."
                )
            cleaned.append(text)
        return cleaned

    # ---- execution
    def run(
        self,
        skill: str,
        rel: str,
        args: Any = None,
        *,
        files: dict[str, str] | None = None,
        output_json: str | None = None,
    ) -> dict[str, Any]:
        """Run a reviewed script in a temp dir holding ``files``; returns {exit_code, stdout, stderr, ...}."""
        if output_json and "host" not in self.tiers:
            raise ScriptRefused("JSON artifact output is host-only.")
        path = self.check(skill, rel)
        argv = [*INTERPRETERS.get(path.suffix, [str(path)]), str(path), *self._clean_args(args)]
        work = Path(tempfile.mkdtemp(prefix="skill-script-")).resolve()
        try:
            for name, content in (files or {}).items():
                target = (work / "input" / name).resolve()
                if not target.is_relative_to(work / "input"):
                    raise ScriptRefused("Input file must stay inside the temporary input directory.")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            started = time.time()
            result = run_command(argv, work, work / "logs", timeout=SCRIPT_TIMEOUT_SECONDS)
            stdout = (work / "logs" / "stdout.log").read_text(encoding="utf-8", errors="replace")
            stderr = (work / "logs" / "stderr.log").read_text(encoding="utf-8", errors="replace")
            data = None
            if output_json and result.get("exit_code") == 0:
                output = (work / output_json).resolve()
                if (
                    not output.is_relative_to(work)
                    or output.is_symlink()
                    or output.stat().st_size > 8_000_000
                ):
                    raise ScriptRefused("Invalid or oversized host JSON output.")
                data = json.loads(output.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        record = {
            "skill": skill,
            "script": rel,
            "sha256": self.allowed[(skill, rel)],
            "argv": argv[len(INTERPRETERS.get(path.suffix, [""])) :],
            "exit_code": result.get("exit_code"),
            "status": result["status"],
            "duration_seconds": round(time.time() - started, 3),
        }
        self.runs.append(record)
        return {
            **record,
            "stdout": stdout[:MAX_OUTPUT_CHARS],
            "stderr": stderr[:MAX_OUTPUT_CHARS],
            **({"data": data} if output_json else {}),
        }

    # ---- MAF SkillScriptRunner protocol
    def __call__(self, skill: Any, script: Any, args: Any = None) -> str:
        name = skill.frontmatter.name
        rel = Path(script.full_path).resolve().relative_to(self.dirs.get(name, self.skills_root / name)).as_posix()
        try:
            outcome = self.run(name, rel, args)
        except ScriptRefused as exc:
            return f"Error: {exc}"
        text = outcome["stdout"] or ""
        if outcome["exit_code"] != 0:
            text += f"\n[exit code {outcome['exit_code']}]\n{outcome['stderr']}"
        return text.strip() or "(no output)"


def script_approval_rule(runner: HarnessScriptRunner) -> Callable[[Content], bool]:
    """MAF auto-approval rule: approve ``run_skill_script`` only for reviewed, hash-intact, model-tier scripts."""

    def rule(function_call: Content) -> bool:
        if function_call.name != SkillsProvider.RUN_SKILL_SCRIPT_TOOL_NAME:
            return False
        if (function_call.additional_properties or {}).get("server_label"):
            return False
        arguments = function_call.arguments
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return False
        if not isinstance(arguments, dict):
            return False
        skill, script = str(arguments.get("skill_name", "")), str(arguments.get("script_name", ""))
        try:
            return any(
                s == skill
                and (rel == script or rel.endswith("/" + script.lstrip("/")))
                and runner.check(s, rel) is not None
                for (s, rel) in runner.allowed
            )
        except ScriptRefused:
            return False

    return rule

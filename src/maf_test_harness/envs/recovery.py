"""Bounded automatic recovery: turn a diagnosed setup failure into one allow-listed action, or say why not.

The error playbook (``errors/``) says *what* went wrong and which recipes exist; this module is what may actually be
done about it without a human, and how far:

* only diagnoses whose autonomy is ``auto`` or ``auto_once`` are acted on; ``ask`` becomes ``needs_approval`` (a
  download or a choice), ``manual`` stays an explanation;
* a recipe that downloads software is never applied without ``allow_downloads``;
* each recipe is tried at most once per step, and at most ``code.max_attempts`` recipes per step;
* an action is data (a changed copy of the step, a step to rewind to), never a shell string: it is run through the same
  ``run_command`` as everything else, so timeouts, the sanitized environment and evidence directories apply.

Implemented recipes: ``retry-longer`` (double the timeout), ``retry-update`` (Maven ``-U`` / Gradle
``--refresh-dependencies``), ``retry-restore`` (one more NuGet restore) and ``recreate-venv`` (delete the isolated
venv and restart from ``create-venv``). Every other recipe is reported as not automatable yet.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..errors import CODES, RECIPES
from .runners import VENV_DIR, Step

MAX_TIMEOUT = 3600.0
IMPLEMENTED = ("retry-longer", "retry-update", "retry-restore", "recreate-venv")
_AUTOMATIC = {"auto", "auto_once"}


@dataclass(frozen=True)
class Action:
    recipe: str
    step: Step  # what to run next (the failed step, possibly changed)
    note: str
    rewind_to: str | None = None  # restart from this earlier step (recreate-venv)
    prepare: Callable[[], None] | None = None  # side effect done before the run (inside the workspace only)


def _tool(step: Step) -> str:
    return Path(step.argv[0]).name.lower().split(".")[0] if step.argv else ""


def _retry_longer(step: Step, workspace: Path) -> Action | None:
    if step.timeout >= MAX_TIMEOUT:
        return None
    longer = min(step.timeout * 2, MAX_TIMEOUT)
    return Action("retry-longer", replace(step, timeout=longer), f"timeout {step.timeout:g}s -> {longer:g}s")


def _retry_update(step: Step, workspace: Path) -> Action | None:
    tool = _tool(step)
    if tool in {"mvn", "mvnw"} and "-U" not in step.argv:
        argv = [step.argv[0], "-U", *step.argv[1:]]
    elif tool in {"gradle", "gradlew"} and "--refresh-dependencies" not in step.argv:
        argv = [*step.argv, "--refresh-dependencies"]
    else:
        return None
    return Action("retry-update", replace(step, argv=argv), "force dependency updates")


def _retry_restore(step: Step, workspace: Path) -> Action | None:
    if _tool(step) != "dotnet":
        return None
    return Action("retry-restore", replace(step), "retry the NuGet restore once")


def _recreate_venv(step: Step, workspace: Path) -> Action | None:
    venv = (workspace / VENV_DIR).resolve()
    if not venv.is_relative_to(workspace.resolve()) or venv.name != VENV_DIR:
        return None  # refuse to delete anything that is not the harness venv inside the workspace

    def wipe() -> None:
        if venv.exists():
            shutil.rmtree(venv, ignore_errors=True)

    # An interpreter that cannot be found means the venv (or its base Python) is broken: start again from create-venv.
    target = "create-venv" if step.name != "create-venv" else None
    return Action("recreate-venv", step, "delete the isolated venv and create it again", target, wipe)


_BUILDERS: dict[str, Callable[[Step, Path], Action | None]] = {
    "retry-longer": _retry_longer,
    "retry-update": _retry_update,
    "retry-restore": _retry_restore,
    "recreate-venv": _recreate_venv,
}


def choose(
    step: Step,
    diagnoses: list,
    tried: list[str],
    workspace: Path,
    *,
    allow_downloads: bool = False,
) -> tuple[Action | None, list[dict[str, Any]]]:
    """The next action for a failed step, and a record of every recipe considered and why it was or was not used."""
    considered: list[dict[str, Any]] = []
    if not diagnoses:
        return None, considered
    code = CODES.get(diagnoses[0].code)
    if code is None:
        return None, considered
    if code.autonomy not in _AUTOMATIC:
        status = "needs_approval" if code.autonomy == "ask" else "manual"
        considered = [{"recipe": r, "status": status, "code": code.code} for r in code.recipes] or [
            {"recipe": "", "status": status, "code": code.code}
        ]
        return None, considered
    for recipe in code.recipes:
        entry = {"recipe": recipe, "code": code.code}
        if recipe in tried:
            continue
        if len(tried) >= code.max_attempts:
            considered.append({**entry, "status": "attempts_exhausted"})
            break
        if RECIPES[recipe].downloads and not allow_downloads:
            considered.append({**entry, "status": "needs_approval"})
            continue
        builder = _BUILDERS.get(recipe)
        action = builder(step, workspace) if builder else None
        if action is None:
            considered.append({**entry, "status": "not_automatable" if builder is None else "not_applicable"})
            continue
        considered.append({**entry, "status": "applied", "note": action.note})
        return action, considered
    return None, considered


FALLBACK_CODES = frozenset(
    {"ENV-PY-001", "ENV-PY-002", "ENV-PY-003"}
)  # compile needed / no matching dist / wrong Python
FALLBACK_MINORS = ("3.11", "3.10", "3.12")  # older interpreters first: wheels exist for them longest


def wipe_venv(workspace: Path) -> bool:
    """Delete the harness venv (and only that) so it can be rebuilt with another interpreter."""
    venv = (Path(workspace) / VENV_DIR).resolve()
    if venv.name != VENV_DIR or not venv.is_relative_to(Path(workspace).resolve()) or not venv.exists():
        return False
    shutil.rmtree(venv, ignore_errors=True)
    return not venv.exists()


def python_fallback(
    report: dict[str, Any],
    workspace: Path,
    tried: list[str] | tuple[str, ...] = (),
    *,
    current: str | None = None,
    available: Callable[[str], bool] | None = None,
) -> str | None:
    """Another Python minor to rebuild the environment with, when the install failed for a reason another interpreter fixes.

    Only for a package that must be compiled, has no matching distribution or wants another Python (the codes in
    ``FALLBACK_CODES``); never the interpreter that just failed, never one already tried, and only one that is installed
    or that ``uv`` can install. The caller shows it in an approval: a download is never done silently.
    """
    from . import env_check, toolchains

    codes = {d.get("code") for step in report.get("steps", []) for d in step.get("diagnoses", [])}
    if not codes & FALLBACK_CODES:
        return None
    current = current or env_check.venv_python_version(workspace) or ".".join(map(str, sys.version_info[:2]))
    uv = shutil.which("uv") is not None
    can = available or (lambda v: uv or toolchains.find("python", v, exact=True) is not None)
    for candidate in FALLBACK_MINORS:
        if candidate != current and candidate not in tried and can(candidate):
            return candidate
    return None


def preview(
    argv: list[str], log: str, ecosystem: str | None, workspace: Path | None = None
) -> dict[str, Any]:
    """What recovery would do for this failed command, without doing it (used by the ``plan_env_fix`` tool)."""
    from ..errors import classify

    diagnoses = classify(log, ecosystem)
    step = Step("failed-step", list(argv), "setup")
    action, considered = choose(step, diagnoses, [], workspace or Path.cwd(), allow_downloads=False)
    return {
        "diagnosis": diagnoses[0].to_dict() if diagnoses else None,
        "would_apply": None
        if action is None
        else {
            "recipe": action.recipe,
            "argv": action.step.argv,
            "timeout": action.step.timeout,
            "note": action.note,
        },
        "considered": considered,
    }


__all__ = ["IMPLEMENTED", "Action", "choose", "preview"]

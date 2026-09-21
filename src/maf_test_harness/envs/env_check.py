"""Which declared Python dependencies really are in the isolated environment, and which import errors follow.

An install step can fail (a package that must be compiled, no wheel for this platform) while the environment is still
usable for everything else. Generated tests that import a missing package then fail at collection with
``ModuleNotFoundError``: that is an environment fact, not a test defect, and asking a model to regenerate the test can
never fix it. This module answers "what is missing" from the environment itself so callers can tell the two apart.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

from . import platform_compat
from .runners import VENV_DIR

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_MODULE_NOT_FOUND = re.compile(r"No module named '([\w.]+)'")
# `pip check`: "fastapi 0.110.0 has requirement starlette<0.37.0,>=0.36.3, but you have starlette 1.6.0."
_CONFLICT = re.compile(
    r"^(?P<package>\S+) (?P<version>\S+) has requirement (?P<requirement>.+?), but you have "
    r"(?P<dependency>\S+) (?P<installed>\S+?)\.?$"
)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_requirements(workspace: Path) -> list[str]:
    """Normalized distribution names the repository itself declares (requirements.txt, pyproject dependencies)."""
    workspace = Path(workspace)
    names: dict[str, None] = {}
    req = workspace / "requirements.txt"
    if req.is_file():
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split(" #")[0]
            if line.strip() and not line.lstrip().startswith(("#", "-")) and (m := _NAME.match(line)):
                names[normalize(m.group(1))] = None
    pyproject = workspace / "pyproject.toml"
    if pyproject.is_file():
        try:
            deps = (
                tomllib.loads(pyproject.read_text(encoding="utf-8"))
                .get("project", {})
                .get("dependencies", [])
            )
        except (tomllib.TOMLDecodeError, OSError):
            deps = []
        for spec in deps:
            if isinstance(spec, str) and (m := _NAME.match(spec)):
                names[normalize(m.group(1))] = None
    return list(names)


def installed_distributions(workspace: Path, timeout: float = 60) -> set[str] | None:
    """Normalized names pip lists in the workspace venv, or None when it cannot be asked."""
    python = Path(platform_compat.venv_python(Path(workspace) / VENV_DIR))
    if not python.is_file():
        return None
    try:
        proc = subprocess.run(
            [str(python), "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {normalize(item["name"]) for item in json.loads(proc.stdout)} if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None


def missing_packages(workspace: Path) -> list[str]:
    """Declared dependencies that are not installed in the isolated environment (empty when unknown)."""
    installed = installed_distributions(workspace)
    if installed is None:
        return []
    return [name for name in declared_requirements(workspace) if name not in installed]


def parse_pip_check(output: str) -> list[dict[str, str]]:
    """Version conflicts from ``pip check`` output: which installed package wants which range of which dependency.

    "X requires Y, which is not installed" lines are not conflicts here: a missing declared package is reported by
    :func:`missing_packages`.
    """
    conflicts = []
    for line in (output or "").splitlines():
        match = _CONFLICT.match(line.strip())
        if match:
            conflicts.append(match.groupdict())
    return conflicts


def dependency_conflicts(workspace: Path, timeout: float = 60) -> list[dict[str, str]]:
    """Installed packages whose declared requirements the environment does not satisfy (empty when unknown).

    Installing requirements one at a time, or letting a newer package pull a newer dependency, can leave a set that
    pip itself calls inconsistent (``fastapi 0.110.0`` next to ``starlette 1.6.0``). Code then fails at import or call
    time with ``TypeError: ... unexpected keyword argument``: an environment fact, not a test defect.
    """
    python = Path(platform_compat.venv_python(Path(workspace) / VENV_DIR))
    if not python.is_file():
        return []
    try:
        proc = subprocess.run(
            [str(python), "-m", "pip", "check", "--disable-pip-version-check"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_pip_check(proc.stdout + "\n" + proc.stderr)


def missing_module_names(output: str) -> list[str]:
    """Top-level module names a collection/run output says it could not import."""
    return list(dict.fromkeys(m.split(".")[0] for m in _MODULE_NOT_FOUND.findall(output or "")))


def explained_by_missing(module: str, missing: list[str]) -> str | None:
    """The missing distribution that ``module`` most plausibly belongs to (e.g. dotenv -> python-dotenv)."""
    wanted = normalize(module)
    for dist in missing:
        parts = dist.split("-")
        if wanted == dist or wanted in parts or dist.removeprefix("python-") == wanted:
            return dist
    return None

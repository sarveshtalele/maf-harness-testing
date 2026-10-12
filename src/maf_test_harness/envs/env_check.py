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
_COULD_NOT_IMPORT = re.compile(r"Could not import ([A-Za-z0-9_.-]+) python package")
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
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=platform_compat.probe_env(Path(workspace) / VENV_DIR / "probe-home"),
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


def venv_python_version(workspace: Path) -> str | None:
    """``major.minor`` of the interpreter the workspace venv was built with (from pyvenv.cfg), if it exists."""
    cfg = Path(workspace) / VENV_DIR / "pyvenv.cfg"
    try:
        match = re.search(r"(?m)^version(?:_info)?\s*=\s*(\d+\.\d+)", cfg.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def ui_tooling(workspace: Path) -> dict[str, bool] | None:
    """Which UI test tools the Python environment really has (None when it cannot be asked).

    ``playwright``: a browser can be driven; ``streamlit``: in-process UI tests are possible (``streamlit.testing.v1.AppTest``).
    """
    installed = installed_distributions(workspace)
    if installed is None:
        return None
    from ..repo.stack import detect_stack
    from .runners import repository_components

    node_browser = any(
        (workspace / cwd / "node_modules/@playwright/test/package.json").is_file()
        for kind, cwd in repository_components(detect_stack(workspace), workspace)
        if kind == "node"
    )
    return {
        "playwright": "playwright" in installed or node_browser,
        "node_playwright": node_browser,
        "streamlit": "streamlit" in installed,
    }


_UNMET = re.compile(
    r"^(?P<package>\S+) (?P<version>\S+) requires (?P<requirement>.+?), which is not installed\.?$"
)


def parse_pip_unmet(output: str) -> list[dict[str, str]]:
    """Dependencies of installed packages that are not installed themselves (pip check: "X requires Y, which is not installed")."""
    found = []
    for line in (output or "").splitlines():
        match = _UNMET.match(line.strip())
        if match:
            row = match.groupdict()
            name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", row["requirement"])
            found.append(
                {
                    **row,
                    "dependency": name.group(0) if name else row["requirement"],
                    "installed": "not installed",
                }
            )
    return found


def describe_conflict(item: dict[str, str]) -> str:
    """One readable line for a conflict or an unmet dependency."""
    if item.get("installed") == "not installed":
        return f"{item['package']} needs {item['requirement']}, which is not installed"
    return f"{item['package']} needs {item['requirement']}, has {item['installed']}"


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


def dependency_problems(workspace: Path, timeout: float = 60) -> dict[str, list[dict[str, str]]]:
    """Everything ``pip check`` reports, from one run: version ``conflicts`` and ``unmet`` (not installed) dependencies."""
    python = Path(platform_compat.venv_python(Path(workspace) / VENV_DIR))
    if not python.is_file():
        return {"conflicts": [], "unmet": []}
    try:
        proc = subprocess.run(
            [str(python), "-m", "pip", "check", "--disable-pip-version-check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=platform_compat.probe_env(Path(workspace) / VENV_DIR / "probe-home"),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"conflicts": [], "unmet": []}
    text = proc.stdout + "\n" + proc.stderr
    return {"conflicts": parse_pip_check(text), "unmet": parse_pip_unmet(text)}


def dependency_conflicts(workspace: Path, timeout: float = 60) -> list[dict[str, str]]:
    """Installed packages whose declared version requirements the environment does not satisfy (empty when unknown).

    Installing requirements one at a time, or letting a newer package pull a newer dependency, can leave a set that
    pip itself calls inconsistent (``fastapi 0.110.0`` next to ``starlette 1.6.0``). Code then fails at import or call
    time with ``TypeError: ... unexpected keyword argument``: an environment fact, not a test defect.
    """
    return dependency_problems(workspace, timeout)["conflicts"]


def missing_module_names(output: str) -> list[str]:
    """Top-level module names a collection/run output says it could not import.

    Also the wrapper wording libraries use instead of ``ModuleNotFoundError`` (LangChain: "Could not import chromadb python
    package. Please install it with `pip install chromadb`").
    """
    text = output or ""
    names = [m.split(".")[0] for m in _MODULE_NOT_FOUND.findall(text)]
    names += [m.split(".")[0] for m in _COULD_NOT_IMPORT.findall(text)]
    return list(dict.fromkeys(names))


def explained_by_missing(module: str, missing: list[str]) -> str | None:
    """The missing distribution that ``module`` most plausibly belongs to (e.g. dotenv -> python-dotenv)."""
    wanted = normalize(module)
    for dist in missing:
        parts = dist.split("-")
        if wanted == dist or wanted in parts or dist.removeprefix("python-") == wanted:
            return dist
    return None

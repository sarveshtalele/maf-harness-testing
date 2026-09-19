"""Environment plan: what must be installed to run this repository's tests, and how, in an isolated location.

Everything here is deterministic host logic derived from the stack profile and manifests. Installing happens only in
a separate, explicitly approved tool call. System-level tools (JDK, Node, Python...) are *reported with install hints*,
never installed by the harness; libraries go into an isolated location (a venv, the project's node_modules, or the
harness cache directories), never into the system interpreter.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .runners import Step, resolve_ecosystem, setup_steps
from .stack import REQUIRED_TOOLS, StackProfile

# tool -> (version command, install hint)
TOOLS: dict[str, tuple[list[str], str]] = {
    "python3": (
        ["python3", "--version"],
        "Install Python 3.10+ (macOS: brew install python; Debian/Ubuntu: apt install python3 python3-venv)",
    ),
    "node": (["node", "--version"], "Install Node.js LTS (macOS: brew install node; or https://nodejs.org)"),
    "npm": (["npm", "--version"], "npm ships with Node.js"),
    "mvn": (
        ["mvn", "-v"],
        "Install Maven (macOS: brew install maven; Debian/Ubuntu: apt install maven) or use the repo's mvnw",
    ),
    "java": (
        ["java", "-version"],
        "Install a JDK matching the repo (macOS: brew install openjdk@11; set JAVA_HOME)",
    ),
    "go": (["go", "version"], "Install Go (macOS: brew install go; or https://go.dev/dl)"),
    "dotnet": (["dotnet", "--version"], "Install the .NET SDK (https://dotnet.microsoft.com/download)"),
}

ISOLATION = {
    "python": "Virtual environment at <workspace>/.harness-venv (system Python is never modified)",
    "node": "Project-local <workspace>/node_modules with lifecycle scripts disabled (--ignore-scripts)",
    "maven": "Dedicated Maven repository under the harness cache dir (-Dmaven.repo.local); ~/.m2 is untouched",
    "gradle": "Dedicated GRADLE_USER_HOME under the harness cache dir",
    "go": "Dedicated GOMODCACHE/GOCACHE under the harness cache dir",
    "dotnet": "Project-local obj/ and restore output inside the workspace",
}


def _probe(tool: str) -> dict:
    command, hint = TOOLS.get(tool, ([tool, "--version"], f"Install {tool}"))
    path = shutil.which(tool)
    entry = {"tool": tool, "present": path is not None, "version": "", "install_hint": hint}
    if path:
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
            entry["version"] = ((proc.stdout or proc.stderr).strip().splitlines() or [""])[0][:120]
        except (OSError, subprocess.TimeoutExpired):
            entry["version"] = "unknown"
    return entry


def build_environment_plan(
    stack: StackProfile, repo_or_workspace: Path, cache_dir: Path, timeout: float = 900
) -> dict:
    """Prerequisites, isolation strategy and the exact install steps for the primary ecosystem."""
    eco = resolve_ecosystem(stack)
    root = Path(repo_or_workspace)
    wrapper = eco == "maven" and (root / "mvnw").exists()
    prerequisites = [_probe(t) for t in REQUIRED_TOOLS.get(eco, ()) if not (wrapper and t == "mvn")]
    steps: list[Step] = setup_steps(eco, root, str(Path(cache_dir).resolve()), timeout)
    missing = [p["tool"] for p in prerequisites if not p["present"]]
    return {
        "ecosystem": eco,
        "isolation": ISOLATION.get(eco, "No adapter for this ecosystem yet; nothing will be installed."),
        "prerequisites": prerequisites,
        "missing_prerequisites": missing,
        "steps": [
            {
                "name": s.name,
                "kind": s.kind,
                "argv": s.argv,
                "env": s.env,
                "cwd": s.cwd,
                "timeout": s.timeout,
                "continue_on_failure": s.continue_on_failure,
            }
            for s in steps
        ],
        "notes": ([f"Install first (outside the harness): {', '.join(missing)}"] if missing else [])
        + ([] if steps else ["No dependency installation is required or supported for this ecosystem."]),
    }


def steps_from_plan(plan: dict) -> list[Step]:
    return [
        Step(
            name=s["name"],
            argv=s["argv"],
            kind=s["kind"],
            cwd=s["cwd"],
            timeout=s["timeout"],
            env=s["env"],
            continue_on_failure=s["continue_on_failure"],
        )
        for s in plan["steps"]
    ]

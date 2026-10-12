"""Environment plan: what must be installed to run this repository's tests, and how, in an isolated location.

Everything here is deterministic host logic derived from the stack profile and manifests. Installing happens only in
a separate, explicitly approved tool call. System-level tools (JDK, Node, Python...) are *reported with install hints*,
never installed by the harness; libraries go into an isolated location (a venv, the project's node_modules, or the
harness cache directories), never into the system interpreter.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ..repo.stack import REQUIRED_TOOLS, StackProfile
from . import plan_toolchains, platform_compat
from .runners import Step, repository_components, resolve_ecosystem, setup_steps

# tool -> (version command, install hint)
TOOLS: dict[str, tuple[list[str], str]] = {
    "python3": (
        ["python3", "--version"],
        "Install Python 3.10+ (macOS: brew install python; Debian/Ubuntu: apt install python3 python3-venv; "
        "Windows: winget install Python.Python.3.12 or https://python.org/downloads)",
    ),
    "node": (
        ["node", "--version"],
        "Install Node.js LTS (macOS: brew install node; Windows: winget install OpenJS.NodeJS.LTS; "
        "or https://nodejs.org)",
    ),
    "npm": (["npm", "--version"], "npm ships with Node.js"),
    "mvn": (
        ["mvn", "-v"],
        "Install Maven (macOS: brew install maven; Debian/Ubuntu: apt install maven; "
        "Windows: winget install Apache.Maven) or use the repo's mvnw",
    ),
    "java": (
        ["java", "-version"],
        "Install a JDK matching the repo (macOS: brew install openjdk@11; "
        "Windows: winget install EclipseAdoptium.Temurin.11.JDK; set JAVA_HOME)",
    ),
    "go": (
        ["go", "version"],
        "Install Go (macOS: brew install go; Windows: winget install GoLang.Go; or https://go.dev/dl)",
    ),
    "dotnet": (
        ["dotnet", "--version"],
        "Install the .NET SDK (Windows: winget install Microsoft.DotNet.SDK.8; "
        "or https://dotnet.microsoft.com/download)",
    ),
}

ISOLATION = {
    "python": (
        "Virtual environment at <workspace>/.harness-venv (Scripts/ on Windows, bin/ on macOS/Linux); "
        "system Python is never modified"
    ),
    "node": "Project-local <workspace>/node_modules with lifecycle scripts disabled (--ignore-scripts)",
    "maven": "Dedicated Maven repository under the harness cache dir (-Dmaven.repo.local); ~/.m2 is untouched",
    "gradle": "Dedicated GRADLE_USER_HOME under the harness cache dir",
    "go": "Dedicated GOMODCACHE/GOCACHE under the harness cache dir",
    "dotnet": "Project-local obj/ and restore output inside the workspace",
}


_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    ".harness-venv",
    "__pycache__",
    "tests",
    "test",
    "docs",
}
_PIP_SPEC = re.compile(
    r"[A-Za-z][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,_-]+\])?(?:(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!_-]+)?"
)
_PIP_INSTALL = re.compile(r"^\s*(?:python3?\s+-m\s+)?pip3?\s+install\s+(.+?)\s*$")


def nested_requirements(root: Path, limit: int = 6) -> list[str]:
    """`requirements*.txt` files below the repository root (monorepos keep them next to each package)."""
    root = Path(root)
    found: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        depth = Path(current).relative_to(root).parts
        if not depth or len(depth) > 3:
            continue
        found += [
            (Path(current) / f).relative_to(root).as_posix()
            for f in sorted(files)
            if re.fullmatch(r"requirements[\w.-]*\.txt", f)
        ]
    return found[:limit]


def documented_packages(needs: list[dict], root: Path, limit: int = 8) -> list[str]:
    """Python packages the model listed as dependencies, kept only when the repository itself supports the claim.

    A need survives when its install command is a plain ``pip install <specs>`` (no URLs, no flags) and the cited
    evidence file exists in the repository and mentions the package. Anything else stays a note for a human.
    """
    root = Path(root)
    kept: dict[str, None] = {}
    for need in needs or []:
        if need.get("kind") not in {"dependency", "test_tool"}:
            continue
        match = _PIP_INSTALL.match(str(need.get("how_to_install", "")))
        evidence = root / str(need.get("evidence_file") or "")
        if (
            not match
            or not need.get("evidence_file")
            or not evidence.resolve().is_relative_to(root.resolve())
            or not evidence.is_file()
        ):
            continue
        text = evidence.read_text(encoding="utf-8", errors="replace").lower()
        for spec in match.group(1).split():
            spec = spec.strip("\"'")
            if _PIP_SPEC.fullmatch(spec) and re.split(r"[\[<>=!~]", spec)[0].lower() in text:
                kept[spec] = None
    return list(kept)[:limit]


def _probe(tool: str) -> dict:
    command, hint = TOOLS.get(tool, ([tool, "--version"], f"Install {tool}"))
    # "python3" is a stable logical key (used by REQUIRED_TOOLS/messages/tests), but the executable to
    # actually invoke may be named "python" (stock Windows Python ships no "python3").
    exe = platform_compat.python_launcher() if tool == "python3" else tool
    path = shutil.which(exe)
    entry = {"tool": tool, "present": path is not None, "version": "", "install_hint": hint}
    if path:
        command = [exe, *command[1:]]
        try:
            proc = platform_compat.run_cancellable(command, timeout=20)
            entry["version"] = ((proc.stdout or proc.stderr).strip().splitlines() or [""])[0][:120]
        except InterruptedError:
            raise
        except (OSError, subprocess.TimeoutExpired):
            entry["version"] = "unknown"
    return entry


def build_environment_plan(
    stack: StackProfile,
    repo_or_workspace: Path,
    cache_dir: Path,
    timeout: float = 900,
    needs: list[dict] | None = None,
    python_override: str | None = None,
    _component: bool = False,
    browser_tests: bool = False,
) -> dict:
    """Prerequisites, isolation strategy and the exact install steps for the primary ecosystem.

    ``needs`` are the model's environment needs; only those the repository's own files support are installed.
    """
    eco = resolve_ecosystem(stack)
    root = Path(repo_or_workspace)
    components = repository_components(stack, root)
    if not _component and (len(components) > 1 or any(cwd != "." for _, cwd in components)):
        from dataclasses import replace

        from ..repo.stack import detect_stack

        plans = []
        for kind, cwd in components:
            local_root = (root / cwd).resolve()
            local = replace(detect_stack(local_root), primary=kind, ecosystems=[kind])
            child = build_environment_plan(
                local,
                local_root,
                cache_dir,
                timeout,
                needs if cwd == "." else None,
                python_override if kind == "python" else None,
                _component=True,
                browser_tests=browser_tests and kind == "node",
            )
            for step in child["steps"]:
                step["cwd"] = (Path(cwd) / step["cwd"]).as_posix()
            plans.append({**child, "cwd": cwd})
        return {
            "ecosystem": eco,
            "components": [{"ecosystem": kind, "cwd": cwd} for kind, cwd in components],
            "toolchains": [row for child in plans for row in child["toolchains"]],
            "needs_download": any(child["needs_download"] for child in plans),
            "isolation": "; ".join(f"{child['cwd']}: {child['isolation']}" for child in plans),
            "prerequisites": [row for child in plans for row in child["prerequisites"]],
            "missing_prerequisites": sorted(
                {name for child in plans for name in child["missing_prerequisites"]}
            ),
            "steps": [step for child in plans for step in child["steps"]],
            "notes": [f"Install component {child['ecosystem']} at {child['cwd']}" for child in plans],
        }

    wrapper = eco == "maven" and (root / "mvnw").exists()
    prerequisites = [_probe(t) for t in REQUIRED_TOOLS.get(eco, ()) if not (wrapper and t == "mvn")]
    python = eco == "python"
    steps: list[Step] = setup_steps(
        eco,
        root,
        str(Path(cache_dir).resolve()),
        timeout,
        need_playwright=browser_tests and eco == "node",
        extra_requirements=nested_requirements(root) if python else None,
        extra_packages=documented_packages(needs or [], root) if python else None,
        python_override=python_override if python else None,
    )
    toolchain_rows = plan_toolchains(root, eco, python_override=python_override)
    coverable = {"jdk": "java", "maven": "mvn", "dotnet": "dotnet", "python": "python3"}
    provisionable = {
        coverable[r["kind"]]
        for r in toolchain_rows
        if r["status"] == "download" and r.get("can_provision", True)
    }
    for p in prerequisites:
        p["provisionable"] = not p["present"] and p["tool"] in provisionable
    missing = [p["tool"] for p in prerequisites if not p["present"] and not p["provisionable"]]
    downloads = [r for r in toolchain_rows if r["status"] == "download" and r.get("can_provision", True)]
    return {
        "ecosystem": eco,
        "toolchains": toolchain_rows,
        "needs_download": bool(downloads),
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
        + (
            [
                "Approving downloads into the harness toolchain cache (nothing system-wide is changed): "
                + "; ".join(f"{r['kind']} {r['version']} from {r['source']}" for r in downloads)
            ]
            if downloads
            else []
        )
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

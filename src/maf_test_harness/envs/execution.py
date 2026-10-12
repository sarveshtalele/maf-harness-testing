"""Explicit local export/execution tools. A working copy is not a security sandbox.

Design rules: every run writes to a NEW result directory (immutable evidence); commands are explicit argv
lists (no shell); credentials are not inherited; timeouts kill the whole process group; a run is only
"passed" when fresh test-report evidence shows at least one executed and passing test.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from ..repo.analyzer import SENSITIVE_PATH
from . import platform_compat

EXPORT_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".harness-venv",
    ".harness-reports",
    ".gradle",
    ".idea",
    ".vscode",
    "Python",  # a stray Windows Python Install Manager download; never expected in a workspace
}
# Host variables forwarded to setup/test commands. Model/cloud credentials are deliberately absent.
FORWARDED_ENV = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "PROGRAMDATA",
    "WINDIR",
    "COMSPEC",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PATHEXT",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "JAVA_HOME",
    "MAVEN_HOME",
    "GRADLE_HOME",
    "GOROOT",
    "DOTNET_ROOT",
    "PLAYWRIGHT_BROWSERS_PATH",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
)
BASIC_ENV = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "LANG", "TMPDIR", "TEMP", "TMP")


def _exportable(relative: Path, source: Path) -> bool:
    return (
        not source.is_symlink()
        and not any(part in EXPORT_SKIP_DIRS or part.startswith("%") for part in relative.parts)
        and not SENSITIVE_PATH.search(relative.as_posix())
    )


def copy_repo(repo: Path, destination: Path) -> Path:
    """Copy the repository into a NEW workspace (no VCS metadata, dependency dirs or secret files)."""
    repo, destination = repo.resolve(), destination.resolve()
    if destination.exists() or destination.is_relative_to(repo) or repo.is_relative_to(destination):
        raise ValueError("Workspace needs a new directory outside the source repository.")
    destination.mkdir(parents=True)
    for folder, dirs, names in os.walk(repo):
        folder_path = Path(folder)
        dirs[:] = [
            d for d in dirs if d not in EXPORT_SKIP_DIRS and not d.startswith("%")
        ]  # prune, do not descend
        for name in names:
            source = folder_path / name
            relative = source.relative_to(repo)
            if source.is_file() and _exportable(relative, source):
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    return destination


def write_generated(
    workspace: Path, files: list[dict], repo: Path | None = None, previous: list[str] | None = None
) -> list[str]:
    """Write generated files into a workspace (replacing files from an earlier attempt, never repository files)."""
    workspace = workspace.resolve()
    for old in previous or []:
        (workspace / old).unlink(missing_ok=True)
    written = []
    seen: dict[str, str] = {}
    for item in files:
        target = workspace / item["path"]
        if not target.resolve().is_relative_to(workspace):
            raise ValueError(f"Generated path escapes the workspace: {item['path']}")
        clash = seen.setdefault(item["path"].casefold(), item["path"])
        if clash != item["path"]:
            raise ValueError(
                f"Generated paths differ only by case and would overwrite each other: {clash}, {item['path']}"
            )
        if repo is not None and (repo / item["path"]).exists():
            raise ValueError(f"Generated file would overwrite repository file: {item['path']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"], encoding="utf-8")
        written.append(item["path"])
    return written


def read_junit_cases(paths: list[Path]) -> list[dict]:
    """Per-testcase outcomes (never nested aggregate attributes, which double-count)."""
    cases = []
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            failure = case.find("failure") if case.find("failure") is not None else case.find("error")
            status = (
                "error"
                if case.find("error") is not None
                else "failed"
                if case.find("failure") is not None
                else "skipped"
                if case.find("skipped") is not None
                else "passed"
            )
            message = ""
            if failure is not None:
                message = ((failure.get("message") or "") + "\n" + (failure.text or "")).strip()[:1500]
            cases.append(
                {
                    "name": case.get("name", ""),
                    "classname": case.get("classname", ""),
                    "file": case.get("file", ""),
                    "status": status,
                    "message": message,
                }
            )
    return cases


def read_junit(paths: list[Path]) -> dict:
    counts = dict(tests=0, passed=0, failed=0, errors=0, skipped=0)
    for case in read_junit_cases(paths):
        counts["tests"] += 1
        counts[{"error": "errors"}.get(case["status"], case["status"])] += 1
    return counts


def _child_env(output_dir: Path, inherit_env: bool, extra: dict[str, str] | None) -> dict[str, str]:
    names = FORWARDED_ENV if inherit_env else BASIC_ENV
    env = {key: os.environ[key] for key in names if key in os.environ}
    # No bytecode: a regenerated test with the same size and mtime second must never run from a stale .pyc.
    env.update(platform_compat.fake_home_env(output_dir / "home"))
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "CI": "1",
        }
    )
    env.update(extra or {})
    return env


def run_command(
    argv: list[str],
    cwd: Path,
    output_dir: Path,
    *,
    timeout: float = 300,
    junit_glob: str | None = None,
    env: dict[str, str] | None = None,
    inherit_env: bool = False,
) -> dict:
    if not argv or not all(isinstance(arg, str) and "\0" not in arg for arg in argv) or timeout <= 0:
        raise ValueError("Provide explicit argv and a positive timeout.")
    cwd, output_dir = cwd.resolve(), output_dir.resolve()
    cancel = platform_compat.RUN_CANCEL.get()
    if cancel is not None and cancel.is_set():
        raise InterruptedError("Run stopped by user; no command was started.")
    if not cwd.is_dir():
        raise ValueError("Execution working directory does not exist.")
    if output_dir.exists():
        raise ValueError("Use a new result directory for each execution; old evidence is immutable.")
    output_dir.mkdir(parents=True)
    child_env = _child_env(output_dir, inherit_env, env)
    started = time.time()
    result = {
        "argv": argv,
        "cwd": str(cwd),
        "started_at": started,
        "status": "running",
        "exit_code": None,
        "isolation": "local-process-not-security-sandbox",
    }
    with (
        (output_dir / "stdout.log").open("w", encoding="utf-8") as stdout,
        (output_dir / "stderr.log").open("w", encoding="utf-8") as stderr,
    ):
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=child_env,
                stdout=stdout,
                stderr=stderr,
                **platform_compat.new_process_group_kwargs(),
            )
            try:
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if cancel is not None and cancel.is_set():
                        platform_compat.kill_process_tree(process)
                        process.wait(timeout=10)
                        result["status"] = "cancelled"
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    try:
                        process.wait(timeout=min(0.25, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                result["exit_code"] = process.returncode
                if result["status"] != "cancelled":
                    result["status"] = "command_succeeded" if process.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                platform_compat.kill_process_tree(process)
                process.wait()
                result["status"] = "timeout"
        except OSError as exc:
            result.update(status="blocked", error=str(exc))
    if junit_glob and result["status"] not in {"timeout", "blocked", "cancelled"}:
        try:
            reports = sorted(p for p in cwd.glob(junit_glob) if p.is_file() and p.stat().st_mtime >= started)
            summary = read_junit(reports)
            result.update(test_results=summary, junit_files=[str(p) for p in reports])
            if result["exit_code"] == 0:
                result["status"] = (
                    "failed"
                    if summary["failed"] or summary["errors"]
                    else "passed_with_skips"
                    if summary["passed"] and summary["skipped"]
                    else "passed"
                    if summary["passed"]
                    else "no_test_evidence"
                )
        except (ET.ParseError, OSError, ValueError) as exc:
            result.update(status="invalid_test_report", error=str(exc))
    result["duration_seconds"] = round(time.time() - started, 3)
    result["log_sha256"] = {
        name: hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
        for name in ("stdout.log", "stderr.log")
    }
    (output_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

"""Compile / collect generated tests in the prepared workspace, before a model round is spent on guessing.

Runs only after the isolated environment was installed with the human's approval. The check uses the same runner, the
same isolated interpreter/Maven repository and the same sanitized environment as an approved test run, but never runs a
test body: ``pytest --collect-only`` for Python (imports the modules, catches ImportError/NameError/SyntaxError at
import time) and ``mvn test-compile`` for Maven. The candidate files are written into the workspace for the check and
removed afterwards, so a rejected candidate can never be compiled into a later run.

Diagnostics come back per file with line numbers, in the same shape the triage report uses.
"""

from __future__ import annotations

import re
import time
from pathlib import Path, PurePosixPath
from typing import Any

from . import platform_compat
from .execution import run_command, write_generated
from .runners import VENV_DIR, Step, _expand_env, build_plan, resolve_ecosystem
from .stack import StackProfile

_MAVEN_ERROR = re.compile(
    r"\[ERROR\]\s+(?P<path>\S+\.(?:java|kt)):\[(?P<line>\d+)(?:,\d+)?\]\s*(?P<message>.*)"
)
_PYTEST_ERROR_LINE = re.compile(r"^(?:E\s+|ERROR |.*(?:Error|Exception)\b)")
BUILD_TIMEOUT_SECONDS = 300.0


def _tail(path: Path, limit: int = 60_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _python_errors(output: str, path: str) -> list[dict[str, Any]]:
    decisive = [line.strip() for line in output.splitlines() if _PYTEST_ERROR_LINE.match(line)]
    line_number = next(
        (int(m.group(1)) for m in re.finditer(rf"{re.escape(PurePosixPath(path).name)}:(\d+)", output)), 0
    )
    return [{"file": path, "line": line_number, "message": "; ".join(decisive[:4])[:400] or output[-400:]}]


def _maven_errors(output: str, generated: list[str]) -> list[dict[str, Any]]:
    by_name = {PurePosixPath(p).name: p for p in generated}
    lines = output.splitlines()
    errors: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        match = _MAVEN_ERROR.search(line)
        if not match:
            continue
        name = PurePosixPath(match["path"]).name
        if name not in by_name:
            continue
        detail = [
            re.sub(r"^\[ERROR\]\s*", "", nxt).strip()
            for nxt in lines[index + 1 : index + 4]
            if re.match(r"\[ERROR\]\s+(symbol|location|required|found|reason)", nxt.strip())
        ]
        errors.append(
            {
                "file": by_name[name],
                "line": int(match["line"]),
                "message": " ".join([match["message"].strip(), *detail])[:400],
            }
        )
    best: dict[
        tuple[str, int], dict[str, Any]
    ] = {}  # Maven prints each error twice; keep the one with detail
    for error in errors:
        key = (error["file"], error["line"])
        if key not in best or len(error["message"]) > len(best[key]["message"]):
            best[key] = error
    return list(best.values())


def try_build(
    stack: StackProfile,
    workspace: Path,
    files: list[dict],
    *,
    cache_dir: Path,
    out_root: Path,
    timeout: float = BUILD_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """``{"status": "ok" | "failed" | "skipped", "tool", "errors": [{file, line, message}], "seconds", "reason"}``."""
    started = time.time()
    workspace = Path(workspace)
    generated = [f["path"] for f in files]
    eco = resolve_ecosystem(stack, files)
    result: dict[str, Any] = {"status": "skipped", "tool": None, "errors": [], "reason": ""}
    if eco == "python" and not Path(platform_compat.venv_python(workspace / VENV_DIR)).exists():
        return {**result, "reason": "isolated environment not installed"}
    if eco not in {"python", "maven"}:
        return {**result, "reason": f"no build check for ecosystem '{eco}'"}
    plan = build_plan(stack, workspace, files, cache_dir=cache_dir, timeout=timeout, env_ready=True)
    if plan.blocked_reason or not plan.steps:
        return {**result, "reason": plan.blocked_reason or "no test step"}
    test_step = plan.steps[-1]
    backup = {p: (workspace / p).read_bytes() for p in generated if (workspace / p).is_file()}
    out_root.mkdir(parents=True, exist_ok=True)
    try:
        write_generated(workspace, files)
        if eco == "python":
            result.update(tool="pytest --collect-only", status="ok")
            for index, item in enumerate(files):
                if not item["path"].endswith(".py"):
                    continue
                argv = [
                    a
                    for a in test_step.argv
                    if not a.startswith(("--junitxml", "--continue-on-collection-errors"))
                    and a not in generated
                ]
                step = Step("collect", [*argv, "--collect-only", item["path"]], "test", env=test_step.env)
                run = run_command(
                    step.argv,
                    workspace,
                    out_root / f"collect-{index:02d}",
                    timeout=timeout,
                    env=_expand_env(step.env, workspace),
                    inherit_env=True,
                )
                output = _tail(out_root / f"collect-{index:02d}" / "stdout.log") + _tail(
                    out_root / f"collect-{index:02d}" / "stderr.log"
                )
                if run["status"] == "timeout":
                    return {**result, "status": "skipped", "reason": "collection timed out"}
                if run["exit_code"] != 0:
                    result["status"] = "failed"
                    result["errors"] += _python_errors(output, item["path"])
        else:
            argv = [*test_step.argv[: test_step.argv.index("test")], "-q", "test-compile"]
            run = run_command(
                argv,
                workspace,
                out_root / "test-compile",
                timeout=timeout,
                env=_expand_env(test_step.env, workspace),
                inherit_env=True,
            )
            result["tool"] = "mvn test-compile"
            if run["status"] == "timeout":
                return {**result, "reason": "compilation timed out"}
            output = _tail(out_root / "test-compile" / "stdout.log") + _tail(
                out_root / "test-compile" / "stderr.log"
            )
            errors = _maven_errors(output, generated)
            if errors:
                result.update(status="failed", errors=errors)
            elif run["exit_code"] == 0:
                result["status"] = "ok"
            else:  # the build broke outside the candidate files (repository itself, offline repo...): not their fault
                result["reason"] = (
                    "build failed outside the generated files: "
                    + (next((ln for ln in output.splitlines() if "[ERROR]" in ln), "")[:200])
                )
    finally:
        for path in generated:
            (workspace / path).unlink(missing_ok=True)
        for path, data in backup.items():
            (workspace / path).parent.mkdir(parents=True, exist_ok=True)
            (workspace / path).write_bytes(data)
        for path in generated:  # compiled classes of a rejected candidate must not survive into a later run
            for cls in (workspace / "target" / "test-classes").rglob(f"{PurePosixPath(path).stem}*.class"):
                cls.unlink(missing_ok=True)
    result["seconds"] = round(time.time() - started, 1)
    return result

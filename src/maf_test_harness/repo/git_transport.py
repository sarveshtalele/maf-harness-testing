"""File-backed Git output, bounded process-tree lifetime and explicit cancellation."""

import subprocess
import time
from pathlib import Path
from uuid import uuid4

from ..envs import platform_compat
from ..storage import atomic_json, read_json


def read_operation(run_dir: Path):
    try:
        operation = read_json(run_dir / "clone_operation.json")
        if not isinstance(operation, dict):
            return {}
        relative = operation.get("stderr_path", "")
        path = (run_dir / relative).resolve()
        if relative and path.is_relative_to(run_dir.resolve()) and path.is_file():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 4096))
                lines = stream.read().decode("utf-8", "replace").splitlines()
            operation["latest_output"] = lines[-1][:600] if lines else "Waiting for Git network output"
        operation["elapsed_seconds"] = max(0, int(time.time() - operation["started_at"]))
        return operation
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def run_git(argv, run_dir: Path, timeout, env, attempt=1):
    operation_id = uuid4().hex
    output = run_dir / "clone-operations" / operation_id
    output.mkdir(parents=True)
    operation = {
        "id": operation_id,
        "stage": "repo_analysis",
        "kind": "clone",
        "status": "running",
        "attempt": attempt,
        "started_at": time.time(),
        "timeout_seconds": round(timeout, 1),
        "message": "Cloning repository from GitHub",
        "stderr_path": (output / "stderr.log").relative_to(run_dir).as_posix(),
    }
    atomic_json(run_dir / "clone_operation.json", operation)
    process = None
    try:
        # Files avoid an unbounded post-timeout communicate() when a descendant inherits a pipe.
        with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(
                argv,
                cwd=run_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                **platform_compat.new_process_group_kwargs(),
            )
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                try:
                    cancel = read_json(run_dir / "clone_cancel.json")
                except (OSError, ValueError):
                    cancel = {}
                stopped = platform_compat.RUN_CANCEL.get()
                if (isinstance(cancel, dict) and cancel.get("operation_id") == operation_id) or (
                    stopped is not None and stopped.is_set()
                ):
                    operation.update(status="cancelled", message="Repository clone cancelled by user")
                    raise InterruptedError(operation["message"])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    operation.update(status="timeout", message="Git clone reached its timeout")
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    process.wait(timeout=min(0.25, remaining))
                except subprocess.TimeoutExpired:
                    pass
        operation.update(status="succeeded" if process.returncode == 0 else "failed")
        return subprocess.CompletedProcess(
            argv,
            process.returncode,
            (output / "stdout.log").read_text(encoding="utf-8", errors="replace"),
            (output / "stderr.log").read_text(encoding="utf-8", errors="replace"),
        )
    except OSError as exc:
        if operation["status"] == "running":
            operation.update(status="blocked", message=str(exc))
        raise
    finally:
        if process is not None and process.poll() is None:
            platform_compat.kill_process_tree(process)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        operation["finished_at"] = time.time()
        atomic_json(run_dir / "clone_operation.json", operation)

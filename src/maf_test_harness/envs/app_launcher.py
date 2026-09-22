"""Start the application under test for the duration of an approved run, so UI/E2E (Playwright) cases can execute.

The command is chosen by the host from repository facts (never by a model), is shown to the human inside the
"run the tests" approval, and is part of the approved plan hash. The application runs as a local child process
inside the isolated environment, bound to 127.0.0.1 on a free port, with the same sanitized environment as every
other executed command, and is always stopped afterwards (whole process tree).
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import platform_compat
from .execution import EXPORT_SKIP_DIRS, _child_env
from .runners import VENV_DIR

READY_TIMEOUT_SECONDS = 90.0
_ENTRY_PREFERENCE = ("app.py", "streamlit_app.py", "main.py", "Home.py", "server.py", "api.py")
_FASTAPI = re.compile(r"^(\w+)\s*=\s*FastAPI\(", re.M)
_FLASK = re.compile(r"^(\w+)\s*=\s*Flask\(", re.M)
_STREAMLIT = re.compile(r"^\s*(?:import streamlit|from streamlit\b)", re.M)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _python_files(workspace: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(workspace.rglob("*.py"), key=lambda p: (len(p.relative_to(workspace).parts), str(p))):
        rel = path.relative_to(workspace)
        if any(part in EXPORT_SKIP_DIRS or part in {"tests", "test"} for part in rel.parts[:-1]):
            continue
        found.append(path)
        if len(found) >= 400:
            break
    return found


def _module(rel: Path) -> str | None:
    parts = [*rel.parts[:-1], rel.stem]
    return ".".join(parts) if all(p.isidentifier() for p in parts) else None


def detect_launch(workspace: Path, port: int) -> dict[str, Any] | None:
    """How to start the application, or None when the repository gives no unambiguous way to."""
    workspace = Path(workspace)
    python = platform_compat.venv_python(workspace / VENV_DIR)
    files = _python_files(workspace)
    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in files}

    streamlit = [p for p, t in texts.items() if _STREAMLIT.search(t)]
    if streamlit:
        entry = min(
            streamlit, key=lambda p: (p.name not in _ENTRY_PREFERENCE, len(p.relative_to(workspace).parts))
        )
        return {
            "kind": "streamlit",
            "argv": [
                python,
                "-m",
                "streamlit",
                "run",
                entry.relative_to(workspace).as_posix(),
                "--server.port",
                str(port),
                "--server.address",
                "127.0.0.1",
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
            ],
            "ready_path": "/_stcore/health",
            "env": {},
        }
    for kind, pattern in (("fastapi", _FASTAPI), ("flask", _FLASK)):
        for path, text in texts.items():
            match = pattern.search(text)
            module = _module(path.relative_to(workspace)) if match else None
            if not module:
                continue
            target = f"{module}:{match.group(1)}"
            argv = (
                [python, "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", str(port)]
                if kind == "fastapi"
                else [
                    python,
                    "-m",
                    "flask",
                    "--app",
                    target,
                    "run",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ]
            )
            return {"kind": kind, "argv": argv, "ready_path": "/", "env": {"PYTHONPATH": str(workspace)}}
    package = workspace / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        except (json.JSONDecodeError, OSError):
            scripts = {}
        name = next((n for n in ("start", "dev") if isinstance(scripts, dict) and n in scripts), None)
        if name:
            return {
                "kind": "node",
                "argv": [*platform_compat.resolve_tool("npm"), "run", name],
                "ready_path": "/",
                "env": {"PORT": str(port)},
            }
    return None


def _ready(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except urllib.error.HTTPError:
        return True  # any HTTP answer means the server is up (404 on "/" is fine)
    except (urllib.error.URLError, OSError, ValueError):
        return False


@contextmanager
def running(spec: dict[str, Any], workspace: Path, base_url: str, log_dir: Path) -> Iterator[None]:
    """Start the app, wait until it answers HTTP, yield, and always stop the whole process tree."""
    workspace, log_dir = Path(workspace).resolve(), Path(log_dir).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    env = _child_env(log_dir, True, spec.get("env"))
    with (log_dir / "app.stdout.log").open("w") as out, (log_dir / "app.stderr.log").open("w") as err:
        process = subprocess.Popen(
            spec["argv"],
            cwd=workspace,
            env=env,
            stdout=out,
            stderr=err,
            **platform_compat.new_process_group_kwargs(),
        )
        try:
            deadline = time.time() + READY_TIMEOUT_SECONDS
            url = base_url.rstrip("/") + spec.get("ready_path", "/")
            while not _ready(url):
                if process.poll() is not None or time.time() > deadline:
                    err.flush()
                    tail = (log_dir / "app.stderr.log").read_text(encoding="utf-8", errors="replace")[-600:]
                    why = "exited" if process.poll() is not None else "did not answer in time"
                    raise RuntimeError(
                        f"The application {why} ({' '.join(spec['argv'][:4])} ...): {tail.strip()}"
                    )
                time.sleep(1)
            yield
        finally:
            if process.poll() is None:
                platform_compat.kill_process_tree(process)
            process.wait()

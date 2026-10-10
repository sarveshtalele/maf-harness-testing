"""Start the application under test for the duration of an approved run, so UI/E2E (Playwright) cases can execute.

The command is chosen by the host from repository facts (never by a model), is shown to the human inside the
"run the tests" approval, and is part of the approved plan hash. The application runs as a local child process
inside the isolated environment, bound to 127.0.0.1 on a free port, with the same sanitized environment as every
other executed command, and is always stopped afterwards (whole process tree).
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
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
        if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()):
            continue
        found.append(path)
        if len(found) >= 400:
            break
    return found


def _module(rel: Path) -> str | None:
    parts = [*rel.parts[:-1], rel.stem]
    return ".".join(parts) if all(p.isidentifier() for p in parts) else None


def detect_launch(workspace: Path, port: int, component: str | None = None) -> dict[str, Any] | None:
    """How to start the application, or None when the repository gives no unambiguous way to."""
    workspace = Path(workspace)
    python = platform_compat.venv_python(workspace / VENV_DIR)
    files = _python_files(workspace)
    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in files}

    if component:
        selected = (workspace / component).resolve()
        if not selected.is_relative_to(workspace.resolve()) or not selected.is_dir():
            raise ValueError("Application component must be an existing contained directory")
        package = selected / "package.json"
        if package.is_file():
            data = json.loads(package.read_text(encoding="utf-8", errors="replace"))
            scripts = data.get("scripts", {})
            name = next((name for name in ("dev", "start") if name in scripts), None)
            if name:
                command = scripts[name]
                argv = [*platform_compat.resolve_tool("npm"), "run", name]
                vite = "vite" in command
                if vite:
                    argv += ["--", "--host", "127.0.0.1", "--port", str(port), "--strictPort"]
                node = {
                    "kind": "node",
                    "argv": argv,
                    "cwd": component,
                    "ready_path": "/",
                    "env": {"PORT": str(port)},
                }
                config = "\n".join(
                    p.read_text(encoding="utf-8", errors="replace")
                    for p in selected.glob("vite.config.*")
                    if not p.is_symlink() and p.resolve().is_relative_to(selected)
                )
                proxy_ports = set(re.findall(r"https?://(?:localhost|127\.0\.0\.1):(\d+)", config))
                if proxy_ports:
                    if len(proxy_ports) != 1:
                        return {
                            **node,
                            "blocked_reason": "Multiple Vite proxy services require an explicit launch plan",
                        }
                    backend_port = int(next(iter(proxy_ports)))
                    candidates = [
                        (path, _FASTAPI.search(text)) for path, text in texts.items() if _FASTAPI.search(text)
                    ]
                    if len(candidates) != 1:
                        return {
                            **node,
                            "blocked_reason": "Vite proxy target needs an unambiguous backend entry point",
                        }
                    path, match = candidates[0]
                    module = _module(path.relative_to(workspace))
                    if not module:
                        return {**node, "blocked_reason": "Backend module cannot be imported"}
                    state_env = {}
                    for text in texts.values():
                        for key in re.findall(r"os\.environ\.get\([\"']([A-Z_]+)[\"']", text):
                            if key.endswith(("_DB", "_CONFIG_DIR", "_DATA_DIR")):
                                state_env[key] = str(
                                    workspace
                                    / ".harness-app-state"
                                    / (key + ".db" if key.endswith("_DB") else key)
                                )
                    backend = {
                        "kind": "fastapi",
                        "argv": [
                            python,
                            "-m",
                            "uvicorn",
                            f"{module}:{match.group(1)}",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(backend_port),
                        ],
                        "cwd": ".",
                        "ready_path": "/health" if '"/health"' in texts[path] else "/openapi.json",
                        "env": {"PYTHONPATH": str(workspace), **state_env},
                        "url": f"http://127.0.0.1:{backend_port}",
                    }
                    return {
                        **node,
                        "kind": "multi-service",
                        "services": [backend, {**node, "url": f"http://127.0.0.1:{port}"}],
                    }
                return node
        spec = detect_launch(selected, port)
        if spec:
            spec["cwd"] = component
            if spec.get("argv") and ".harness-venv" in spec["argv"][0]:
                spec["argv"][0] = python
            spec.setdefault("env", {})["PYTHONPATH"] = str(workspace) + os.pathsep + str(selected)
        return spec
    streamlit = [p for p, t in texts.items() if _STREAMLIT.search(t)]
    http_entries = [p for p, text in texts.items() if _FASTAPI.search(text) or _FLASK.search(text)]
    node_entries = [
        p
        for p in workspace.rglob("package.json")
        if not any(part in EXPORT_SKIP_DIRS for part in p.relative_to(workspace).parts)
    ]
    if (streamlit and (http_entries or node_entries)) or len(http_entries) > 1 or len(node_entries) > 1:
        return {
            "kind": "ambiguous",
            "argv": [],
            "blocked_reason": "Multiple application interfaces found. Select an application component or provide its running URL.",
        }

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
            return {
                "kind": kind,
                "argv": argv,
                "ready_path": "/health"
                if kind == "fastapi" and '"/health"' in text
                else "/openapi.json"
                if kind == "fastapi"
                else "/",
                "env": {"PYTHONPATH": str(workspace)},
            }
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
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError:
        return False
    except (urllib.error.URLError, OSError, ValueError):
        return False


@contextmanager
def running(spec: dict[str, Any], workspace: Path, base_url: str, log_dir: Path) -> Iterator[None]:
    """Start the app, wait until it answers HTTP, yield, and always stop the whole process tree."""
    workspace, log_dir = Path(workspace).resolve(), Path(log_dir).resolve()
    if spec.get("blocked_reason"):
        raise RuntimeError(spec["blocked_reason"])
    if spec.get("services"):
        with ExitStack() as stack:
            for index, service in enumerate(spec["services"]):
                stack.enter_context(running(service, workspace, service["url"], log_dir / f"service-{index}"))
            yield
        return
    root = (workspace / spec.get("cwd", ".")).resolve()
    if not root.is_relative_to(workspace):
        raise ValueError("Application working directory escapes workspace")
    log_dir.mkdir(parents=True, exist_ok=True)
    for value in spec.get("env", {}).values():
        path = Path(value)
        if path.is_absolute() and path.is_relative_to(workspace / ".harness-app-state"):
            (path.parent if path.suffix == ".db" else path).mkdir(parents=True, exist_ok=True)
    env = _child_env(log_dir, True, spec.get("env"))
    with (log_dir / "app.stdout.log").open("w") as out, (log_dir / "app.stderr.log").open("w") as err:
        process = subprocess.Popen(
            spec["argv"],
            cwd=root,
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

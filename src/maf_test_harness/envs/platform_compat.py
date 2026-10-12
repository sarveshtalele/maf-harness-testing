"""OS-specific decisions for the isolated-environment pipeline, centralized so call sites stay uniform.

Everything here is a pure function of sys.platform / shutil.which / os.environ. No POSIX (or Windows) assumption
should leak into runners.py / execution.py / build_gate.py / code_intel.py / environment.py / stack.py outside of
calls into this module.
"""

from __future__ import annotations

import os
import shutil
import sys
from contextvars import ContextVar
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
RUN_CANCEL = ContextVar("harness_run_cancel", default=None)


def is_cancelled() -> bool:
    """True when the current run's Stop (or Ctrl-C) event is set."""
    event = RUN_CANCEL.get()
    return event is not None and event.is_set()


def raise_if_cancelled() -> None:
    """Explicit cancellation point for long loops between batches/model calls."""
    if is_cancelled():
        import asyncio

        raise asyncio.CancelledError("run stopped by user")


def venv_python(venv_dir: str | Path) -> str:
    """Path to the interpreter inside a venv created at ``venv_dir``, as a string suitable for argv[0].

    POSIX: <venv_dir>/bin/python. Windows: <venv_dir>\\Scripts\\python.exe.
    """
    venv_dir = Path(venv_dir)
    return str(venv_dir / "Scripts" / "python.exe") if IS_WINDOWS else str(venv_dir / "bin" / "python")


def venv_site_packages_glob(venv_dir: Path) -> list[Path]:
    """Existing site-packages dir(s) inside a venv, across platforms (empty list if the venv is absent)."""
    if IS_WINDOWS:
        candidate = venv_dir / "Lib" / "site-packages"
        return [candidate] if candidate.is_dir() else []
    return sorted(venv_dir.glob("lib/python*/site-packages"))


def tool_present(name: str) -> bool:
    """True if the named tool is available. Python means a real detected interpreter (the same detection that runs
    it), never a bare ``python``/``python3`` on PATH, which on Windows can be the Store alias."""
    if name == "python3":
        from . import toolchains  # late import: toolchains imports this module

        return bool(toolchains.detect_pythons())
    return shutil.which(name) is not None


def resolve_tool(name: str) -> list[str]:
    """Argv prefix to invoke a project tool (npm/npx/mvn/gradle/...) safely with shell=False.

    shutil.which() consults PATHEXT on Windows and correctly resolves '.cmd'/'.bat' shims to their real
    path; a bare literal like "npm" in Popen's argv does NOT get that treatment because CreateProcess is
    called directly, not through a shell. Resolving via shutil.which keeps shell=False everywhere (no
    shell-injection surface) while fixing the Windows FileNotFoundError.
    """
    resolved = shutil.which(name)
    return [resolved] if resolved else [name]


def resolve_wrapper(workspace: Path, unix_name: str, windows_name: str, fallback: str) -> list[str]:
    """Argv prefix for a project wrapper script (mvnw/mvnw.cmd, gradlew/gradlew.bat), falling back to the
    system tool (resolved via resolve_tool) if no wrapper is checked into the repo."""
    if IS_WINDOWS and (workspace / windows_name).exists():
        return [str(workspace / windows_name)]
    if not IS_WINDOWS and (workspace / unix_name).exists():
        return [f"./{unix_name}"]
    return resolve_tool(fallback)


def playwright_cache_dir() -> Path | None:
    """Default Playwright browser cache location per OS (None if not found)."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        candidate = Path(base) / "ms-playwright" if base else None
    elif sys.platform == "darwin":
        candidate = Path.home() / "Library/Caches/ms-playwright"
    else:
        candidate = Path.home() / ".cache/ms-playwright"
    return candidate if candidate and candidate.is_dir() else None


def pythonpath_join(paths: list[str]) -> str:
    """PYTHONPATH value joined with the platform separator (':' POSIX, ';' Windows). A literal ':' on
    Windows collides with drive letters (C:\\...), so os.pathsep must always be used."""
    return os.pathsep.join(paths)


def cache_path(cache_root: str, *parts: str) -> str:
    """Build a cache sub-path with pathlib instead of an f-string '/'-join."""
    return str(Path(cache_root, *parts))


def new_process_group_kwargs() -> dict:
    """Popen kwargs to start the child as the root of its own process group/job, so the whole tree can be
    killed on timeout. POSIX: start_new_session=True (setsid). Windows: CREATE_NEW_PROCESS_GROUP."""
    if IS_WINDOWS:
        import subprocess

        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_process_tree(process) -> None:
    """Forcefully kill a process and any children it spawned, after subprocess.TimeoutExpired.

    POSIX: os.killpg(pid, SIGKILL) -- the process group created by start_new_session=True.
    Windows: os.killpg/SIGKILL don't exist; use taskkill /T /F /PID <pid> to kill the whole tree
    (CREATE_NEW_PROCESS_GROUP alone does not kill descendants). Swallow errors from a process that
    already exited between the timeout firing and the kill attempt.
    """
    if IS_WINDOWS:
        import subprocess

        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
    else:
        import signal

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_cancellable(argv, *, input=None, timeout: float, env=None):
    """subprocess.run(capture_output, text) that Stop can interrupt: own process group, polled RUN_CANCEL, whole
    tree killed on cancel or timeout. Raises subprocess.TimeoutExpired on timeout, InterruptedError on cancel."""
    import subprocess
    import time

    if is_cancelled():
        raise InterruptedError("Run stopped by user; no command was started.")
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",  # locale code pages (cp1252) garble non-ASCII hook payloads on Windows
        errors="replace",
        env=env,
        **new_process_group_kwargs(),
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            out, err = process.communicate(input, timeout=min(0.25, max(deadline - time.monotonic(), 0.01)))
            return subprocess.CompletedProcess(argv, process.returncode, out, err)
        except subprocess.TimeoutExpired:
            expired = time.monotonic() >= deadline
            if expired or is_cancelled():
                kill_process_tree(process)
                process.communicate()
                if expired:
                    raise
                raise InterruptedError("Run stopped by user.") from None
        input = None  # already written; communicate() resumes the pending write on retry


PROBE_ENV_KEYS = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "LANG", "TMPDIR", "TEMP", "TMP")


def probe_env(fake_home: Path) -> dict[str, str]:
    """Environment for short inspection commands run by a workspace venv (pip list/check, import probes).

    A dependency of the repository under test can ship code that runs whenever the venv interpreter starts (a .pth
    file). Such code sees only these variables: no model keys, tokens, proxy credentials or real home directory.
    """
    env = {key: os.environ[key] for key in PROBE_ENV_KEYS if key in os.environ}
    env.update(fake_home_env(fake_home))
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return env


def fake_home_env(fake_home: Path) -> dict[str, str]:
    """Env overrides that redirect every OS's notion of 'home' to ``fake_home``, so the real user home
    directory (and any secrets under it) stays hidden from child processes on every platform.

    POSIX tools read HOME. Windows tools read USERPROFILE (and often APPDATA/LOCALAPPDATA for caches and
    config) -- setting only HOME on Windows (as before) silently fails to hide the real profile.
    """
    fake_home.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        appdata = fake_home / "AppData" / "Roaming"
        local = fake_home / "AppData" / "Local"
        appdata.mkdir(parents=True, exist_ok=True)
        local.mkdir(parents=True, exist_ok=True)
        return {
            "USERPROFILE": str(fake_home),
            "HOME": str(fake_home),  # some cross-platform tools (git, node) still check HOME on Windows
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(local),
        }
    return {"HOME": str(fake_home)}

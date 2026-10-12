"""Start the local Python API and Next.js together on Windows, macOS or Linux."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


def port_available(port):
    with socket.socket() as probe:
        if os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def build_id():
    """Fingerprint of the code a launch would serve; a running UI from other code is never reused."""
    digest = hashlib.sha256()
    roots = [
        ROOT / "src",
        ROOT / "frontend" / "app",
        ROOT / "frontend" / "components",
        ROOT / "frontend" / "lib",
    ]
    for path in sorted(p for root in roots if root.is_dir() for p in root.rglob("*") if p.is_file()):
        if "__pycache__" not in path.parts:
            stat = path.stat()
            digest.update(f"{path.relative_to(ROOT).as_posix()}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()[:16]


# One state file per launcher (launcher-<pid>.json), so a second instance never overwrites or unlinks another's record.
STATE_DIR = ROOT / ".maf-harness"
LEGACY_STATE_FILE = "launcher.json"  # written by launchers before per-pid files; still cleaned up once
API_MODULE = "maf_test_harness.ui.api"
# Next.js retitles its server process, so the recorded entry path alone cannot verify an orphaned frontend.
NEXT_TITLE = "next-server"


def _probe(argv):
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=10, errors="replace", check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def pid_alive(pid):
    if os.name == "nt":
        out = _probe(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
        return bool(out) and f'"{pid}"' in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # PermissionError: exists, owned by someone else
        return True
    return True


def command_line(pid):
    """Full command line of a pid, or None when it cannot be verified (callers then skip cleanup)."""
    if os.name == "nt":
        out = _probe(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ]
        )
    else:
        out = _probe(["ps", "-o", "command=", "-p", str(pid)])
    return (out.strip() or None) if out is not None else None


def tree_commands(pid):
    """(pid, command line) of a recorded child's tree, also after the child itself exited: its POSIX process group
    (start_new_session made it the leader) or, on Windows, the child and its direct children."""
    if os.name == "nt":
        out = _probe(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid} or ParentProcessId={pid}' | "
                'ForEach-Object { "$($_.ProcessId) $($_.CommandLine)" }',
            ]
        )
        rows = [line.split(None, 1) for line in (out or "").splitlines()]
        return [(int(r[0]), r[1]) for r in rows if len(r) == 2 and r[0].isdigit()]
    out = _probe(["ps", "-A", "-o", "pid=,pgid=,command="])
    rows = [line.split(None, 2) for line in (out or "").splitlines()]
    return [(int(r[0]), r[2]) for r in rows if len(r) == 3 and r[1] == str(pid)]


def state_file():
    return STATE_DIR / f"launcher-{os.getpid()}.json"


def _taskkill(pid):
    # Forced at once: a console-less service cannot act on a graceful /T request, so that stage would only stall.
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True, timeout=10)


def terminate_tree(pid):
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True, timeout=10
        )
        return
    # Children were started with start_new_session, so the pid is also the process-group id; the group can
    # outlive its leader, so wait on the group rather than the pid.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except OSError:
            return
        for _ in range(30):
            try:
                os.killpg(pid, 0)
            except OSError:
                return
            time.sleep(0.1)


def stop_children(processes, grace=5):
    """Stop every child tree. Windows: `taskkill /T /F` at once. POSIX: SIGTERM each process group, then SIGKILL what is
    left after `grace` seconds. Groups are killed even when their leader already exited, so a grandchild such as
    next-server cannot outlive the launcher."""
    from maf_test_harness.envs.platform_compat import kill_process_tree

    if os.name == "nt":
        for process in reversed(processes):
            if process.poll() is None:
                _taskkill(process.pid)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        return
    for process in reversed(processes):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + grace
    for process in processes:
        try:
            process.wait(timeout=max(deadline - time.monotonic(), 0.1))
        except subprocess.TimeoutExpired:
            pass
    for process in processes:
        try:
            kill_process_tree(process)
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def install_stop_handlers(on_close, windows=os.name == "nt"):
    """Make SIGTERM, SIGHUP and Ctrl+Break take the Ctrl+C path (KeyboardInterrupt, then the cleanup in main).
    Windows ends the process as soon as a console close/logoff/shutdown handler returns, so those events stop the
    children synchronously in on_close. Returns (previous signal handlers, console handler or None)."""

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = {}
    for name in ("SIGTERM", "SIGBREAK", "SIGHUP"):
        if hasattr(signal, name):
            signum = getattr(signal, name)
            previous[signum] = signal.signal(signum, interrupt)
    console = None
    if windows:
        import ctypes

        def on_console(event):
            if event in (2, 5, 6):  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
                on_close()
                return True
            return False  # Ctrl+C / Ctrl+Break reach the Python signal handlers

        console = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)(on_console)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(console, True)
    return previous, console


def write_state(children):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = state_file()
    tmp = target.with_suffix(".tmp")
    payload = {
        "launcher_pid": os.getpid(),
        "children": children,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "build_id": build_id(),
    }
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def _orphan_state_files():
    files = sorted(STATE_DIR.glob("launcher-*.json")) if STATE_DIR.is_dir() else []
    legacy = STATE_DIR / LEGACY_STATE_FILE
    return files + ([legacy] if legacy.is_file() else [])


def _drop_unreadable_state(path):
    """A state file that cannot be parsed has no children we can verify. Remove it only when its launcher is gone:
    the pid in `launcher-<pid>.json`, or the legacy file (no pid), so a live launcher's file is never touched."""
    match = re.fullmatch(r"launcher-(\d+)\.json", path.name)
    if match is None or not pid_alive(int(match.group(1))):
        path.unlink(missing_ok=True)


def clean_orphans():
    """Stop children a hard-killed launcher left behind. Only a process that is alive, still holds its
    recorded port and whose command line carries our marker is touched; unverifiable ones are skipped.
    Each dead launcher's own state file is removed; a live launcher's file (and our own) is never touched."""
    for path in _orphan_state_files():
        if path == state_file():
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            launcher = int(state["launcher_pid"])
            children = list(state["children"])
        except (OSError, ValueError, KeyError, TypeError):
            _drop_unreadable_state(path)
            continue
        if launcher != os.getpid() and pid_alive(launcher):
            owner = command_line(launcher)
            if owner is None or "start_ui" in owner:
                # A live launcher owns these. An unreadable command line (a transient lookup failure on Windows) is
                # unverifiable, so its children are never treated as orphans.
                continue
        for child in children:
            try:
                pid, port, markers = int(child["pid"]), int(child["port"]), child["marker"]
                markers = [markers] if isinstance(markers, str) else [str(m) for m in markers]
            except (KeyError, ValueError, TypeError):
                continue
            members = tree_commands(pid)
            if any(m in cmd for _, cmd in members for m in markers) and not port_available(port):
                for target in [m for m, _ in members] if os.name == "nt" else [pid]:
                    terminate_tree(target)
                print(
                    f"Stopped orphaned {child.get('name', 'service')} (pid {pid}, port {port}) from a previous launch.",
                    flush=True,
                )
        path.unlink(missing_ok=True)


class _NoRedirect(HTTPRedirectHandler):
    """Return 3xx replies as HTTPError so the session cookie of /api/session can be read."""

    def redirect_request(self, *args, **kwargs):
        return None


def http_get(url, headers=None, timeout=5):
    """(status, headers, body) for any HTTP reply, including 3xx and 4xx. Connection errors still raise OSError."""
    try:
        with build_opener(_NoRedirect).open(Request(url, headers=headers or {}), timeout=timeout) as response:
            return response.status, response.headers, response.read()
    except HTTPError as error:
        return error.code, error.headers, error.read()


def open_session(port, launch_secret):
    """Exchanges the one-time launch secret for the session cookie, as the browser does. This spends the secret."""
    status, headers, _ = http_get(f"http://127.0.0.1:{port}/api/session?launch={launch_secret}")
    match = re.search(rf"aqe_session_{port}=([A-Za-z0-9_.-]+)", headers.get("Set-Cookie", ""))
    if status != 303 or not match:
        raise RuntimeError("The UI did not accept the launch link.")
    return f"aqe_session_{port}={match.group(1)}"


def open_app(port, launch_secret, opener=webbrowser.open, cleanup_after=60):
    """Opens the UI through a local page that redirects to the launch link. The secret is then never in the opener's
    command line (visible to other local users). The page is 0600, in a private folder, and is deleted after
    `cleanup_after` seconds; the printed link remains the fallback. Returns the page path."""
    target = f"http://127.0.0.1:{port}/api/session?launch={launch_secret}"
    folder = Path(tempfile.mkdtemp(prefix="maf-open-"))
    page = folder / "open.html"
    body = (
        '<!doctype html><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0;url={html.escape(target, quote=True)}">'
        f"<title>Agentic QE</title><script>location.replace({json.dumps(target)});</script>"
        f'<p>Opening Agentic QE. If nothing happens, <a href="{html.escape(target, quote=True)}">continue</a>.</p>'
    )
    fd = os.open(page, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body)

    def cleanup():
        time.sleep(cleanup_after)
        shutil.rmtree(folder, ignore_errors=True)

    Thread(target=cleanup, daemon=True).start()
    try:
        opener(page.as_uri())
    except webbrowser.Error:
        pass
    return page


def nonce_matches(body, nonce):
    try:
        return secrets.compare_digest(str(json.loads(body).get("nonce", "")).encode(), nonce.encode())
    except (ValueError, AttributeError):
        return False


def verify_backend(port, token, nonce):
    """Authenticated readiness: the API answers with this launch's nonce, so the token goes to no other process."""
    status, _, body = http_get(f"http://127.0.0.1:{port}/health", {"Authorization": f"Bearer {token}"})
    if status != 200 or not nonce_matches(body, nonce):
        raise RuntimeError("Another process answered on the API port. The launch was stopped.")


def running_application(port, expected_build=None):
    """Reuse only a responding platform UI built from this checkout. Its 401 page carries the build id."""
    if port_available(port):
        return False
    try:
        _, _, body = http_get(f"http://127.0.0.1:{port}/", timeout=2)
        if b"Agentic QE Platform" not in body:
            return False
        build = re.search(rb'name="agentic-qe-build" content="([^"]*)"', body)
        if expected_build and (build is None or build.group(1).decode() != expected_build):
            print(
                f"An older Agentic QE Platform build is running on port {port}; it is left running and a new "
                "instance starts on another port. Stop the old one with Ctrl+C in its terminal.",
                flush=True,
            )
            return False
        return True
    except (OSError, ValueError):
        return False


def choose_port(port, explicit, excluded=()):
    if explicit:
        if port in excluded or not port_available(port):
            raise RuntimeError(f"Port {port} is already in use. Choose another port.")
        return port
    for candidate in range(port, min(port + 100, 65536)):
        if candidate not in excluded and port_available(candidate):
            if candidate != port:
                print(
                    f"Port {port} is occupied; using {candidate}. Existing processes are left running.",
                    flush=True,
                )
            return candidate
    raise RuntimeError(f"No free local port found near {port}.")


def node_runtime():
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Install Node.js 24 LTS, open a new terminal, then retry. https://nodejs.org/")
    result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10, check=False)
    version = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"v24\.\d+\.\d+", version):
        raise RuntimeError(
            f"Node.js 24 LTS is required. Found {version or 'unknown'} at {node}. Check where node (Windows) or which node (macOS) and remove older PATH entries."
        )
    print(f"Node.js {version} · {node}", flush=True)
    return node


def npm_command(node):
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    roots = [Path(node).resolve().parent, Path(npm).resolve().parent if npm else Path(node).parent]
    candidates = [root / "node_modules/npm/bin/npm-cli.js" for root in roots]
    candidates += [root / "../lib/node_modules/npm/bin/npm-cli.js" for root in roots]
    if npm and Path(npm).resolve().name == "npm-cli.js":
        candidates.append(Path(npm).resolve())
    cli = next((p.resolve() for p in candidates if p.is_file()), None)
    if not cli:
        raise RuntimeError(
            "Cannot find npm-cli.js for Node 24. Repair the official Node.js 24 installation and reopen the terminal."
        )
    return [node, str(cli)]


def frontend_command(node, mode, port=None):
    entry = ROOT / "frontend/node_modules/next/dist/bin/next"
    if not entry.is_file():
        raise RuntimeError(
            "Next.js is missing. Run start-windows.cmd or start-macos.command to reinstall frontend dependencies."
        )
    return [
        node,
        str(entry),
        mode,
        *(["--webpack"] if mode == "build" else ["--hostname", "127.0.0.1", "--port", str(port)]),
    ]


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--production", action="store_true", help="Use the built frontend (npm run build first)"
    )
    parser.add_argument(
        "--check-only", action="store_true", help="Check ports before rebuilding a stopped UI"
    )
    parser.add_argument(
        "--prepare", action="store_true", help="Install frontend dependencies and build with Node 24"
    )
    parser.add_argument("--smoke-test", action="store_true", help="Verify both servers, then stop")
    parser.add_argument("--port", type=int, help="UI port; default 3000, with automatic conflict recovery")
    parser.add_argument(
        "--api-port", type=int, help="API port; default 8765, with automatic conflict recovery"
    )
    args = parser.parse_args()
    explicit_port, explicit_api_port = args.port is not None, args.api_port is not None
    args.port = args.port if explicit_port else 3000
    args.api_port = args.api_port if explicit_api_port else 8765
    try:
        node = node_runtime()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        parser.error(str(exc))
    if args.port == args.api_port or not all(0 < p < 65536 for p in (args.port, args.api_port)):
        parser.error("Choose two different ports between 1 and 65535.")
    clean_orphans()
    current_build = build_id()
    for candidate in range(args.port, args.port + (1 if explicit_port else 10)):
        if running_application(candidate, current_build):
            print(
                f"Agentic QE Platform is already running on port {candidate}. To use it:\n"
                f"  1. Open http://127.0.0.1:{candidate} in the browser that already has it: the session lasts 7 days.\n"
                "  2. For a new link, stop the running launcher (Ctrl+C in its terminal) and start it again. The link printed\n"
                "     by a launcher is single-use.\nNo rebuild or duplicate servers started.",
                flush=True,
            )
            # Platform wrappers distinguish reuse from preflight success and skip installation/build.
            return 10 if args.check_only else 0
    try:
        args.port = choose_port(args.port, explicit_port, (args.api_port,))
        args.api_port = choose_port(args.api_port, explicit_api_port, (args.port,))
    except RuntimeError as exc:
        parser.error(str(exc))
    if args.check_only:
        return None
    # Preflight runs before dependencies are installed; normal launch follows uv sync.
    # Load .env before choosing storage so launcher and API honor the same overrides.
    sys.path.insert(0, str(ROOT / "src"))
    from maf_test_harness.settings import load_dotenv

    load_dotenv(ROOT / ".env")
    env = {
        **os.environ,
        "HARNESS_API_TOKEN": secrets.token_urlsafe(48),
        "HARNESS_API_PORT": str(args.api_port),
        "HARNESS_BUILD_ID": current_build,
        "NEXT_TELEMETRY_DISABLED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PATH": str(Path(node).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    # New runs belong to this checkout; an explicit directory still takes precedence.
    env.setdefault("HARNESS_RUNS_DIR", str(ROOT / ".maf-harness" / "runs"))
    runs = Path(env["HARNESS_RUNS_DIR"]).expanduser()
    runs = (runs if runs.is_absolute() else ROOT / runs).resolve()
    runs.mkdir(parents=True, exist_ok=True, mode=0o700)
    env["HARNESS_RUNS_DIR"] = str(runs)
    log_dir = Path(env.get("HARNESS_LOG_DIR") or Path(env["HARNESS_RUNS_DIR"]).parent / "logs")
    log_dir = log_dir.expanduser()
    log_dir = (log_dir if log_dir.is_absolute() else ROOT / log_dir).resolve()
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    env["HARNESS_LOG_DIR"] = str(log_dir)
    # Per launch. The UI's secret is single use (exchanged at /api/session); the API's nonce proves the port is ours.
    launch_secret = secrets.token_urlsafe(32)
    backend_nonce = secrets.token_urlsafe(24)
    backend_env = {**env, "HARNESS_LAUNCH_NONCE": backend_nonce}
    # The state directory keeps the session signing key and the spent launch secret across a Next restart (0600 file).
    frontend_env = {**env, "HARNESS_LAUNCH_SECRET": launch_secret, "HARNESS_UI_STATE_DIR": str(STATE_DIR)}
    log_path = log_dir / ("launch-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".log")
    log = log_path.open("w", encoding="utf-8")
    print(
        f"Service/setup log: {log_path}\nRun logs: {Path(env['HARNESS_RUNS_DIR']) / '<run-id>'} (Logs, Audit and Observability in the UI)",
        flush=True,
    )
    lock = Lock()
    # Keys may be configured in .env rather than inherited. Never copy them into saved service output.
    secret_values = [
        v
        for k, v in env.items()
        if any(t in k.upper() for t in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and len(v) > 8
    ] + [launch_secret, backend_nonce]
    if (ROOT / ".env").is_file():
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if (
                sep
                and any(t in key.upper() for t in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
                and len(value.strip().strip("\"'")) > 8
            ):
                secret_values.append(value.strip().strip("\"'"))
    threads = []

    def launch(argv, cwd, name, port=None, marker=None, child_env=None, ready=None, ready_line=None):
        child = subprocess.Popen(
            argv,
            cwd=cwd,
            env=child_env or env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
        processes.append(child)
        if port:
            tracked.append({"name": name, "pid": child.pid, "port": port, "marker": marker})
            write_state(tracked)

        def pump():
            for line in child.stdout:
                if ready is not None and ready_line and ready_line in line:
                    ready.set()  # printed only after the service bound its port, so that port is ours
                for secret in secret_values:
                    line = line.replace(secret, "[REDACTED]")
                with lock:
                    log.write(f"{datetime.now().isoformat(timespec='seconds')} [{name}] {line}")
                    log.flush()
                    print(line, end="", flush=True)
            child.stdout.close()

        worker = Thread(target=pump, daemon=True)
        threads.append(worker)
        worker.start()
        return child

    processes, tracked = [], []
    kwargs = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )

    previous_handlers, console_handler = install_stop_handlers(lambda: stop_children(processes))
    try:
        if args.prepare:
            install = launch(
                [*npm_command(node), "ci", "--include=dev", "--bin-links=true"],
                ROOT / "frontend",
                "npm-install",
            )
            if install.wait() != 0:
                raise RuntimeError(f"npm install failed. See {log_path}")
            build = launch(frontend_command(node, "build"), ROOT / "frontend", "next-build")
            if build.wait() != 0:
                raise RuntimeError(f"Next.js build failed. See {log_path}")
            return None
        api_ready = Event()
        backend = launch(
            [sys.executable, "-m", API_MODULE, "--port", str(args.api_port)],
            ROOT,
            "backend",
            args.api_port,
            [API_MODULE],
            child_env=backend_env,
            ready=api_ready,
            ready_line=f"Local harness API: http://127.0.0.1:{args.api_port} nonce={backend_nonce}",
        )
        # Cold MAF imports on hosted runners can exceed the old ten-second startup budget.
        deadline = time.monotonic() + 90
        while not api_ready.wait(0.1):
            if backend.poll() is not None:
                raise RuntimeError("The local API failed to start. See its error above.")
            if time.monotonic() > deadline:
                raise RuntimeError("The local API did not become ready.")
        verify_backend(args.api_port, env["HARNESS_API_TOKEN"], backend_nonce)
        command = frontend_command(node, "start" if args.production else "dev", args.port)
        frontend = launch(
            command,
            ROOT / "frontend",
            "frontend",
            args.port,
            [command[1], NEXT_TITLE],
            child_env=frontend_env,
        )
        if args.smoke_test:
            for _ in range(120):
                if any(process.poll() is not None for process in processes):
                    raise RuntimeError("A local service exited before startup verification.")
                try:
                    status, _, body = http_get(f"http://127.0.0.1:{args.port}/")
                    if status != 401 or b"Agentic QE Platform" not in body:
                        raise RuntimeError("The frontend returned an unexpected page.")
                    session = open_session(args.port, launch_secret)
                    status, _, body = http_get(
                        f"http://127.0.0.1:{args.port}/api/harness/config", {"Cookie": session}
                    )
                    config = json.loads(body) if status == 200 else {}
                    if not {"model", "key_configured", "stages"}.issubset(config):
                        raise RuntimeError("The frontend could not verify the backend configuration.")
                    status, _, body = http_get(
                        f"http://127.0.0.1:{args.port}/api/harness/health", {"Cookie": session}
                    )
                    if status != 200 or not nonce_matches(body, backend_nonce):
                        raise RuntimeError("The UI is not connected to this launch's API.")
                    print("Startup verified: UI and authenticated backend proxy work.", flush=True)
                    return None
                except (URLError, TimeoutError):
                    time.sleep(0.5)
            raise RuntimeError("The UI/backend connection did not become ready.")
        # The browser opens the link. Its launch secret works once; a manual visit to the bare address gets a 401 page.
        launch_url = f"http://127.0.0.1:{args.port}/api/session?launch={launch_secret}"
        for _ in range(180):
            if frontend.poll() is not None:
                raise RuntimeError("The UI server exited before it was ready. See its error above.")
            try:
                http_get(f"http://127.0.0.1:{args.port}/", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        if os.environ.get("AGENTIC_QE_OPEN_BROWSER", "1") != "0":
            open_app(args.port, launch_secret)
        print(
            f"Open the app: {launch_url}\n(The browser opens the app automatically. This link works once; if the browser "
            "did not open, use it. Afterwards the browser keeps the session for 7 days.) Ctrl+C stops both services.",
            flush=True,
        )
        while all(process.poll() is None for process in processes):
            time.sleep(0.3)
        if any(process.poll() not in (None, 0) for process in processes):
            raise RuntimeError("A local service exited with an error.")
    except KeyboardInterrupt:
        pass
    finally:
        # A second Ctrl+C must not interrupt taskkill and leave the application children running.
        for name in ("SIGINT", "SIGBREAK", "SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                signum = getattr(signal, name)
                previous_handlers.setdefault(signum, signal.getsignal(signum))
                signal.signal(signum, signal.SIG_IGN)
        stop_children(processes)
        for worker in threads:
            worker.join(timeout=5)
        log.close()
        if tracked:
            state_file().unlink(missing_ok=True)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if console_handler:
            import ctypes

            ctypes.windll.kernel32.SetConsoleCtrlHandler(console_handler, False)


if __name__ == "__main__":
    sys.exit(main())

"""Start the local Python API and Next.js together on Windows, macOS or Linux."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from urllib.error import URLError
from urllib.request import urlopen

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


def running_application(port):
    """Reuse only a responding platform UI with a working authenticated API proxy."""
    if port_available(port):
        return False
    try:
        with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
            if b"Agentic QE Platform" not in response.read():
                return False
        with urlopen(f"http://127.0.0.1:{port}/api/harness/config", timeout=2) as response:
            config = json.load(response)
        return isinstance(config, dict) and {"model", "key_configured", "stages"}.issubset(config)
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
    result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10)
    version = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"v24\.\d+\.\d+", version):
        raise RuntimeError(
            f"Node.js 24 LTS is required. Found {version or 'unknown'} at {node}. Check where node (Windows) or which node (macOS) and remove older PATH entries."
        )
    print(f"Node.js {version} · {node} (Next.js is a separate framework version)", flush=True)
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
    for candidate in range(args.port, args.port + (1 if explicit_port else 10)):
        if running_application(candidate):
            print(
                f"Agentic QE Platform is already running. Open http://127.0.0.1:{candidate}\nNo rebuild or duplicate servers started. Stop it with Ctrl+C in its original terminal before updating.",
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
        return
    # Preflight runs before dependencies are installed; normal launch follows uv sync.
    # Load .env before choosing storage so launcher and API honor the same overrides.
    sys.path.insert(0, str(ROOT / "src"))
    from maf_test_harness.settings import load_dotenv

    load_dotenv(ROOT / ".env")
    env = {
        **os.environ,
        "HARNESS_API_TOKEN": secrets.token_urlsafe(48),
        "HARNESS_API_PORT": str(args.api_port),
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
    ]
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

    def launch(argv, cwd, name):
        child = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
        processes.append(child)

        def pump():
            for line in child.stdout:
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

    processes = []
    kwargs = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous_handlers = {}
    for name in ("SIGTERM", "SIGBREAK", "SIGHUP"):
        if hasattr(signal, name):
            signum = getattr(signal, name)
            previous_handlers[signum] = signal.signal(signum, interrupt)
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
            return
        backend = launch(
            [sys.executable, "-m", "maf_test_harness.ui.api", "--port", str(args.api_port)],
            ROOT,
            "backend",
        )
        # Cold MAF imports on hosted runners can exceed the old ten-second startup budget.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                raise RuntimeError("The local API failed to start. See its error above.")
            try:
                with socket.create_connection(("127.0.0.1", args.api_port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("The local API did not become ready.")
        command = frontend_command(node, "start" if args.production else "dev", args.port)
        launch(command, ROOT / "frontend", "frontend")
        if args.smoke_test:
            for _ in range(120):
                if any(process.poll() is not None for process in processes):
                    raise RuntimeError("A local service exited before startup verification.")
                try:
                    with urlopen(f"http://127.0.0.1:{args.port}/", timeout=2) as response:
                        if b"Agentic QE Platform" not in response.read():
                            raise RuntimeError("The frontend returned an unexpected page.")
                    with urlopen(f"http://127.0.0.1:{args.port}/api/harness/config", timeout=2) as response:
                        config = json.load(response)
                    if not {"model", "key_configured", "stages"}.issubset(config):
                        raise RuntimeError("The frontend could not verify the backend configuration.")
                    print("Startup verified: UI and authenticated backend proxy work.", flush=True)
                    return
                except (URLError, TimeoutError):
                    time.sleep(0.5)
            raise RuntimeError("The UI/backend connection did not become ready.")
        print(f"Open http://127.0.0.1:{args.port} — Ctrl+C stops both services.", flush=True)
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
        for process in reversed(processes):
            if process.poll() is None:
                if os.name == "nt":
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=10,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        if process.poll() is None:
                            process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=5)
        for worker in threads:
            worker.join(timeout=5)
        log.close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(main())

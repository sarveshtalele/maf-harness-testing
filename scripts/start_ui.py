"""Start the local Python API and Next.js together on Windows, macOS or Linux."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--production", action="store_true", help="Use the built frontend (npm run build first)"
    )
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--api-port", type=int, default=8765)
    args = parser.parse_args()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm or not (ROOT / "frontend" / "node_modules").is_dir():
        parser.error("Install Node.js and run npm ci in frontend first.")
    if args.port == args.api_port or not all(0 < p < 65536 for p in (args.port, args.api_port)):
        parser.error("Choose two different ports between 1 and 65535.")
    for port in (args.port, args.api_port):
        with socket.socket() as probe:
            if os.name != "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                parser.error(f"Port {port} is already in use.")
    env = {
        **os.environ,
        "HARNESS_API_TOKEN": secrets.token_urlsafe(48),
        "HARNESS_API_PORT": str(args.api_port),
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    # Keep each operating-system user's data separate; an explicit runs directory still takes precedence.
    env.setdefault("HARNESS_RUNS_DIR", str(Path.home() / ".maf-harness" / "runs"))
    Path(env["HARNESS_RUNS_DIR"]).mkdir(parents=True, exist_ok=True, mode=0o700)
    processes = []
    kwargs = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    try:
        backend = subprocess.Popen(
            [sys.executable, "-m", "maf_test_harness.ui.api", "--port", str(args.api_port)],
            cwd=ROOT,
            env=env,
            **kwargs,
        )
        processes.append(backend)
        for _ in range(100):
            if backend.poll() is not None:
                raise RuntimeError("The local API failed to start. See its error above.")
            try:
                with socket.create_connection(("127.0.0.1", args.api_port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("The local API did not become ready.")
        command = [npm, "run", "start" if args.production else "dev", "--", "--port", str(args.port)]
        processes.append(subprocess.Popen(command, cwd=ROOT / "frontend", env=env, **kwargs))
        print(f"Open http://127.0.0.1:{args.port} — Ctrl+C stops both services.", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.3)
        if any(process.poll() not in (None, 0) for process in processes):
            raise RuntimeError("A local service exited with an error.")
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                    )
                else:
                    os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()


if __name__ == "__main__":
    main()

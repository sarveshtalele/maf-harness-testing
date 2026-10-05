"""The launcher must reject occupied ports without touching another local service."""

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("preflight", [False, True])
def test_occupied_port_is_rejected_without_stopping_its_owner(preflight):
    root = Path(__file__).resolve().parents[1]
    if (
        not shutil.which("npm.cmd" if os.name == "nt" else "npm")
        or not (root / "frontend/node_modules").is_dir()
    ):
        pytest.skip("Install frontend dependencies to check the UI launcher")
    with socket.socket() as owner:
        owner.bind(("127.0.0.1", 0))
        owner.listen()
        port = owner.getsockname()[1]
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts/start_ui.py"),
                *(["--check-only"] if preflight else []),
                "--port",
                str(port),
                "--api-port",
                str(1 if port != 1 else 2),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2 and f"Port {port} is already in use" in result.stderr
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass


def test_production_startup_verifies_proxy_and_stops_both_servers(tmp_path):
    root = Path(__file__).resolve().parents[1]
    if not (root / "frontend/.next/BUILD_ID").is_file():
        pytest.skip("Build the frontend before the startup integration check")
    ports = []
    for _ in range(2):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            ports.append(probe.getsockname()[1])
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/start_ui.py"),
            "--production",
            "--smoke-test",
            "--port",
            str(ports[0]),
            "--api-port",
            str(ports[1]),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HARNESS_RUNS_DIR": str(tmp_path / "runs"),
            "HARNESS_USAGE_DIR": str(tmp_path / "usage"),
        },
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Startup verified: UI and authenticated backend proxy work." in result.stdout
    for port in ports:
        with socket.socket() as probe:
            if os.name != "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))

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

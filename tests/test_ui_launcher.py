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
    saved_logs = list((tmp_path / "logs").glob("launch-*.log"))
    assert saved_logs
    saved_output = saved_logs[0].read_text(encoding="utf-8")
    assert "[backend]" in saved_output and "[frontend]" in saved_output
    assert "Local harness API" in saved_output
    for port in ports:
        with socket.socket() as probe:
            if os.name != "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))


def launcher_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "qe_launcher", Path(__file__).resolve().parents[1] / "scripts/start_ui.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ["v16.20.2", "v20.19.0", "v26.0.0"])
def test_launcher_rejects_wrong_node_major(monkeypatch, version):
    launcher = launcher_module()
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "node")
    monkeypatch.setattr(
        launcher.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, version, "")
    )
    with pytest.raises(RuntimeError, match="Node.js 24 LTS is required"):
        launcher.node_runtime()


def test_frontend_launch_does_not_require_npm_next_shim(monkeypatch, tmp_path):
    launcher = launcher_module()
    root = tmp_path / "repo with spaces"
    entry = root / "frontend/node_modules/next/dist/bin/next"
    entry.parent.mkdir(parents=True)
    entry.write_text("// fixture")
    monkeypatch.setattr(launcher, "ROOT", root)
    argv = launcher.frontend_command("C:/Program Files/nodejs/node.exe", "start", 3000)
    assert argv == [
        "C:/Program Files/nodejs/node.exe",
        str(entry),
        "start",
        "--hostname",
        "127.0.0.1",
        "--port",
        "3000",
    ]
    assert not (root / "frontend/node_modules/.bin/next.cmd").exists()


def test_npm_uses_the_selected_node_executable(monkeypatch, tmp_path):
    launcher = launcher_module()
    root = tmp_path / "Node 24 with spaces"
    cli = root / "node_modules/npm/bin/npm-cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// fixture")
    node = root / "node.exe"
    node.touch()
    monkeypatch.setattr(launcher.shutil, "which", lambda _: str(root / "npm.cmd"))
    assert launcher.npm_command(str(node)) == [str(node), str(cli)]

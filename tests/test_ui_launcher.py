"""The launcher must reject occupied ports without touching another local service."""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
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


def test_default_port_conflict_recovers_without_stopping_owner():
    launcher = launcher_module()
    with socket.socket() as owner:
        owner.bind(("127.0.0.1", 0))
        owner.listen()
        port = owner.getsockname()[1]
        chosen = launcher.choose_port(port, False)
        assert chosen != port and launcher.port_available(chosen)
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass


def test_repeated_start_reuses_healthy_app_and_signal_shutdown_frees_ports(tmp_path):
    root = Path(__file__).resolve().parents[1]
    if not (root / "frontend/.next/BUILD_ID").is_file():
        pytest.skip("Build the frontend before the startup integration check")
    launcher = launcher_module()
    ports = []
    for _ in range(2):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            ports.append(probe.getsockname()[1])
    command = [
        sys.executable,
        str(root / "scripts/start_ui.py"),
        "--production",
        "--port",
        str(ports[0]),
        "--api-port",
        str(ports[1]),
    ]
    env = {
        **os.environ,
        "HARNESS_RUNS_DIR": str(tmp_path / "runs"),
        "HARNESS_USAGE_DIR": str(tmp_path / "usage"),
    }
    with (tmp_path / "console.log").open("w", encoding="utf-8") as console:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=env,
            stdout=console,
            stderr=subprocess.STDOUT,
            **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}),
        )
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and process.poll() is None:
                if launcher.running_application(ports[0]):
                    break
                time.sleep(0.5)
            else:
                pytest.fail((tmp_path / "console.log").read_text(encoding="utf-8"))
            for preflight in (True, False):
                result = subprocess.run(
                    [*command, *(["--check-only"] if preflight else [])],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                assert result.returncode == (10 if preflight else 0), result.stderr
                assert "already running" in result.stdout
                assert process.poll() is None
        finally:
            if process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
                process.wait(timeout=30)
    assert process.returncode == 0
    for port in ports:
        assert launcher.port_available(port), f"Shutdown left port {port} occupied"


def test_a_running_ui_from_other_code_is_not_reused(monkeypatch, capsys):
    import io
    import json as _json

    launcher = launcher_module()
    pages = {
        "/": b"<title>Agentic QE Platform</title>",
        "/api/harness/config": _json.dumps(
            {"model": "m", "key_configured": True, "stages": [], "build_id": "old"}
        ).encode(),
    }

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(launcher, "port_available", lambda port: False)
    monkeypatch.setattr(launcher, "urlopen", lambda url, timeout: Reply(pages[url.split(":3000", 1)[1]]))
    assert launcher.running_application(3000, "old") is True
    assert launcher.running_application(3000, "new") is False
    assert "older Agentic QE Platform build" in capsys.readouterr().out
    assert launcher.build_id() == launcher.build_id()


def _launcher():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "start_ui_under_test", Path(__file__).resolve().parents[1] / "scripts" / "start_ui.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state(module, tmp_path, monkeypatch, launcher_pid=111, cmd="node /x/next/dist/bin/next start"):
    import json

    monkeypatch.setattr(module, "STATE_DIR", tmp_path)
    child = {"name": "frontend", "pid": 222, "port": 3000, "marker": "next/dist/bin/next"}
    (tmp_path / "launcher-111.json").write_text(
        json.dumps({"launcher_pid": launcher_pid, "children": [child]})
    )
    killed = []
    monkeypatch.setattr(module, "pid_alive", lambda pid: pid == 222)
    monkeypatch.setattr(module, "command_line", lambda pid: cmd if pid == 222 else None)
    monkeypatch.setattr(module, "tree_commands", lambda pid: [(222, cmd)] if pid == 222 and cmd else [])
    monkeypatch.setattr(module, "port_available", lambda port: False)
    monkeypatch.setattr(module, "terminate_tree", killed.append)
    return killed


@pytest.mark.parametrize("cmd", ["node /x/next/dist/bin/next start", "next-server (v16.4.0)"])
def test_orphaned_child_of_dead_launcher_is_terminated(tmp_path, monkeypatch, capsys, cmd):
    module = _launcher()
    killed = _state(module, tmp_path, monkeypatch, cmd=cmd)
    child = {"name": "frontend", "pid": 222, "port": 3000, "marker": ["next/dist/bin/next", "next-server"]}
    state = module.STATE_DIR / "launcher-111.json"
    state.write_text(__import__("json").dumps({"launcher_pid": 111, "children": [child]}))
    module.clean_orphans()
    assert killed == [222] and not state.exists()
    assert "orphaned frontend" in capsys.readouterr().out


def test_unreadable_state_file_is_removed_only_when_its_launcher_is_gone(tmp_path, monkeypatch):
    module = _launcher()
    monkeypatch.setattr(module, "STATE_DIR", tmp_path)
    dead = tmp_path / "launcher-424242.json"
    live = tmp_path / f"launcher-{os.getpid() + 1}.json"
    legacy = tmp_path / "launcher.json"
    for path in (dead, live, legacy):
        path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(module, "pid_alive", lambda pid: pid == os.getpid() + 1)
    module.clean_orphans()
    assert not dead.exists() and not legacy.exists()
    assert live.exists()  # a live launcher's file is never touched, even when unreadable


@pytest.mark.parametrize(
    "case",
    ["foreign command", "port free", "unverifiable", "launcher alive", "launcher alive, command unreadable"],
)
def test_unverified_processes_are_never_killed(tmp_path, monkeypatch, case):
    module = _launcher()
    killed = _state(module, tmp_path, monkeypatch, cmd="vim notes.txt" if case == "foreign command" else None)
    if case == "unverifiable":
        monkeypatch.setattr(module, "tree_commands", lambda pid: [])
    if case == "port free":
        monkeypatch.setattr(module, "port_available", lambda port: True)
    if case == "launcher alive":
        monkeypatch.setattr(module, "pid_alive", lambda pid: True)
        monkeypatch.setattr(module, "command_line", lambda pid: "python scripts/start_ui.py")
    if case == "launcher alive, command unreadable":  # Windows CI: the PowerShell lookup timed out (None)
        monkeypatch.setattr(module, "pid_alive", lambda pid: True)
        monkeypatch.setattr(
            module, "command_line", lambda pid: None if pid == 111 else "node /x/next/dist/bin/next"
        )
    module.clean_orphans()
    assert killed == []


FAKE_SERVICE = """
import os, signal, socket, subprocess, sys, time
from pathlib import Path

port = int(sys.argv[sys.argv.index("--port") + 1])
# A grandchild that ignores SIGTERM, like a Next.js server outliving its CLI parent.
grandchild = subprocess.Popen(
    [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"]
)
Path(os.environ["FAKE_PIDS"], f"{port}.pid").write_text(f"{os.getpid()} {grandchild.pid}")
server = socket.socket()
server.bind(("127.0.0.1", port))
server.listen()
time.sleep(600)
"""

DRIVER = """
import importlib.util, sys
from pathlib import Path

root, state, fakes = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
spec = importlib.util.spec_from_file_location("start_ui", root / "scripts" / "start_ui.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
launcher.node_runtime = lambda: sys.executable
launcher.frontend_command = lambda node, mode, port=None: [node, str(fakes / "fake_ui.py"), "--port", str(port)]
launcher.API_MODULE = "fake_api"
launcher.STATE_DIR = state
sys.argv = ["start_ui.py", "--production", "--port", sys.argv[4], "--api-port", sys.argv[5]]
sys.exit(launcher.main())
"""


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX signals; Windows console events are covered by the unit test below"
)
@pytest.mark.parametrize("signum", ["SIGTERM", "SIGHUP"])
def test_signal_to_launcher_stops_both_child_trees(tmp_path, signum):
    launcher = launcher_module()
    root = Path(__file__).resolve().parents[1]
    for name in ("fake_api.py", "fake_ui.py"):
        (tmp_path / name).write_text(FAKE_SERVICE, encoding="utf-8")
    (tmp_path / "pids").mkdir()
    ports = []
    for _ in range(2):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            ports.append(probe.getsockname()[1])
    state = tmp_path / "state"
    env = {
        **os.environ,
        "PYTHONPATH": str(tmp_path),
        "FAKE_PIDS": str(tmp_path / "pids"),
        "HARNESS_RUNS_DIR": str(tmp_path / "runs"),
    }
    with (tmp_path / "console.log").open("w", encoding="utf-8") as console:
        process = subprocess.Popen(
            [sys.executable, "-c", DRIVER, str(root), str(state), str(tmp_path), *map(str, ports)],
            cwd=root,
            env=env,
            stdout=console,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 60
            while len(list((tmp_path / "pids").glob("*.pid"))) < 2 or "Ctrl+C stops" not in (
                tmp_path / "console.log"
            ).read_text(encoding="utf-8"):
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail((tmp_path / "console.log").read_text(encoding="utf-8"))
                time.sleep(0.2)
            pids = [int(pid) for f in (tmp_path / "pids").glob("*.pid") for pid in f.read_text().split()]
            process.send_signal(getattr(signal, signum))
            process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
    assert process.returncode == 0, (tmp_path / "console.log").read_text(encoding="utf-8")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(launcher.pid_alive(pid) for pid in pids):
        time.sleep(0.1)
    assert not [pid for pid in pids if launcher.pid_alive(pid)], "launcher left child processes running"
    assert all(launcher.port_available(port) for port in ports) and not list(state.glob("launcher-*.json"))


def test_windows_console_close_stops_children_before_the_process_ends(monkeypatch):
    import ctypes

    launcher = launcher_module()
    registered, closed = [], []

    class Kernel32:
        def SetConsoleCtrlHandler(self, handler, add):
            registered.append((handler, add))
            return True

    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    monkeypatch.setattr(ctypes, "windll", type("WinDLL", (), {"kernel32": Kernel32()})(), raising=False)
    previous, console = launcher.install_stop_handlers(lambda: closed.append(True), windows=True)
    try:
        assert registered == [(console, True)]
        assert console(0) is False and closed == []  # Ctrl+C stays with the Python signal handler
        assert console(2) is True and closed == [True]  # CTRL_CLOSE_EVENT stops the children synchronously
        with pytest.raises(KeyboardInterrupt):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)

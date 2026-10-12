"""Parent-death and argv handling of the desktop backend entry point (src/maf_test_harness/desktop_backend.py)."""

import os
import subprocess
import sys
import time
from pathlib import Path

from maf_test_harness import desktop_backend

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

WATCH_SNIPPET = """
import sys, time
from maf_test_harness import desktop_backend
desktop_backend._exit_when_stdin_closes()
print("watching", flush=True)
time.sleep(60)  # would outlive the test if the watcher did not exit on EOF
"""


def test_backend_exits_when_the_shell_closes_its_stdin_pipe():
    proc = subprocess.Popen(
        [sys.executable, "-c", WATCH_SNIPPET],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=ENV,
    )
    assert proc.stdout.readline().strip() == b"watching"
    time.sleep(0.5)
    assert proc.poll() is None, "must keep running while the pipe is open"
    proc.stdin.close()  # what the Electron shell does when it dies
    assert proc.wait(timeout=15) == 0


def test_watcher_is_opt_in_by_environment(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop_backend, "_exit_when_stdin_closes", lambda: calls.append(True))
    from maf_test_harness.ui import api

    monkeypatch.setattr(api, "main", lambda: None)
    monkeypatch.delenv("HARNESS_EXIT_ON_STDIN_EOF", raising=False)
    desktop_backend.main(["--port", "9001"])
    assert calls == []  # a terminal or CI launch keeps running on an EOF of its stdin
    monkeypatch.setenv("HARNESS_EXIT_ON_STDIN_EOF", "1")
    desktop_backend.main(["--port", "9001"])
    assert calls == [True]


def test_instance_id_is_not_passed_to_the_api_parser(monkeypatch):
    seen = []
    from maf_test_harness.ui import api

    monkeypatch.setattr(api, "main", lambda: seen.append(sys.argv[1:]))
    assert desktop_backend.main(["--port", "9001", "--instance", "abc123"]) == 0
    assert seen == [["--port", "9001"]]
    assert desktop_backend._strip_instance(["--instance", "x"]) == []
    assert desktop_backend._strip_instance(["--port", "1"]) == ["--port", "1"]

"""app_launcher: choosing a start command from repository facts and running the app for the length of a run."""

import sys
import textwrap

import pytest

from maf_test_harness.envs import app_launcher
from maf_test_harness.qa import triage


def write(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def test_detects_streamlit_entry_and_ignores_tests_and_venv(tmp_path):
    write(
        tmp_path,
        {
            "tests/test_ui.py": "import streamlit\n",
            ".harness-venv/Lib/x.py": "import streamlit\n",
            "pages/other.py": "import streamlit as st\n",
            "app.py": "import streamlit as st\nst.title('x')\n",
        },
    )
    spec = app_launcher.detect_launch(tmp_path, 8765)
    assert spec["kind"] == "streamlit" and spec["ready_path"] == "/_stcore/health"
    argv = spec["argv"]
    assert argv[argv.index("run") + 1] == "app.py" and argv[argv.index("--server.port") + 1] == "8765"
    assert "127.0.0.1" in argv and "--server.headless" in argv


def test_detects_fastapi_flask_and_node_scripts(tmp_path):
    write(tmp_path, {"api/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"})
    spec = app_launcher.detect_launch(tmp_path, 9000)
    assert spec["kind"] == "fastapi" and "api.main:app" in spec["argv"] and "9000" in spec["argv"]
    flask = tmp_path / "f"
    write(flask, {"server.py": "from flask import Flask\napp = Flask(__name__)\n"})
    assert app_launcher.detect_launch(flask, 9001)["kind"] == "flask"
    node = tmp_path / "n"
    write(node, {"package.json": '{"scripts": {"start": "node s.js"}}'})
    spec = app_launcher.detect_launch(node, 9002)
    assert spec["kind"] == "node" and spec["env"] == {"PORT": "9002"}
    assert app_launcher.detect_launch(tmp_path / "empty", 1) is None


def test_running_waits_for_the_server_and_always_stops_it(tmp_path):
    port = app_launcher.free_port()
    spec = {
        "kind": "x",
        "argv": [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        "ready_path": "/",
    }
    with app_launcher.running(spec, tmp_path, f"http://127.0.0.1:{port}", tmp_path / "log"):
        assert app_launcher._ready(f"http://127.0.0.1:{port}/")
    assert not app_launcher._ready(f"http://127.0.0.1:{port}/")


def test_running_reports_an_app_that_exits_immediately(tmp_path):
    spec = {"kind": "x", "argv": [sys.executable, "-c", "raise SystemExit('boom')"], "ready_path": "/"}
    with (
        pytest.raises(RuntimeError, match="exited"),
        app_launcher.running(
            spec, tmp_path, f"http://127.0.0.1:{app_launcher.free_port()}", tmp_path / "log"
        ),
    ):
        pass


@pytest.mark.parametrize(
    "message",
    [
        "browserType.launch: Executable doesn't exist at C:/x/chrome.exe",
        "page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:1",
        "AssertionError: SRE module mismatch",
        "ModuleNotFoundError: No module named 'fastapi'",
    ],
)
def test_environment_errors_have_specific_remedies(message):
    assert textwrap.shorten(triage.remedy_for(message), 200) and "Import only names" not in triage.remedy_for(
        message
    )

"""Launch identity: the API echoes the per-launch nonce only to its authenticated caller (F13), and the launcher
refuses to trust an API that does not echo it (no bearer token goes to a process that merely holds the port)."""

import importlib.util
import json
import os
import stat
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from maf_test_harness.ui.api import LocalAPI, make_server

TOKEN = "t" * 40
NONCE = "nonce-for-this-launch-0123456789"


@pytest.fixture
def api_server(tmp_path):
    server = make_server(LocalAPI(tmp_path / "runs", requirements_mode="deterministic"), TOKEN, 0, NONCE)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


def _get(port, headers=None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/health", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_health_echoes_the_nonce_only_to_the_authenticated_caller(api_server):
    status, body = _get(api_server)
    assert status == 403 and NONCE.encode() not in body
    status, body = _get(api_server, {"Authorization": "Bearer wrong-token-of-the-same-length-000"})
    assert status == 403 and NONCE.encode() not in body
    status, body = _get(api_server, {"Authorization": f"Bearer {TOKEN}"})
    assert status == 200 and json.loads(body) == {"ok": True, "nonce": NONCE}


def test_the_launcher_rejects_an_api_that_does_not_echo_the_nonce(api_server):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("qe_launcher", root / "scripts/start_ui.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    launcher.verify_backend(api_server, TOKEN, NONCE)  # the real API passes
    with pytest.raises(RuntimeError, match="Another process answered"):
        launcher.verify_backend(api_server, TOKEN, "a-different-nonce")
    with pytest.raises(RuntimeError, match="Another process answered"):
        launcher.verify_backend(api_server, "wrong-token", NONCE)


def _launcher_module():
    spec = importlib.util.spec_from_file_location(
        "qe_launcher_identity", Path(__file__).resolve().parents[1] / "scripts/start_ui.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_opener_argv_never_contains_the_launch_secret(tmp_path, monkeypatch):
    """The browser opener's command line is visible to other local users, so it gets a local page path, not the link."""
    launcher = _launcher_module()
    secret = "launch-secret-must-not-appear-in-argv-0123456789"
    opened = []
    page = launcher.open_app(4321, secret, opener=opened.append, cleanup_after=0.2)
    assert len(opened) == 1 and opened[0].startswith("file://")
    assert secret not in " ".join(opened)
    assert secret in page.read_text(encoding="utf-8")  # the page redirects to the launch link
    assert "/api/session?launch=" in page.read_text(encoding="utf-8")
    if os.name != "nt":
        assert stat.S_IMODE(page.stat().st_mode) == 0o600
    deadline = time.monotonic() + 5
    while page.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not page.exists()  # deleted after the cleanup delay


def test_session_cookie_is_named_per_port(monkeypatch):
    launcher = _launcher_module()
    reply = {"status": 303, "Set-Cookie": "aqe_session_4321=abc.def-1; Path=/; Max-Age=604800; HttpOnly"}

    class Headers(dict):
        def get(self, key, default=""):
            return dict.get(self, key, default)

    monkeypatch.setattr(launcher, "http_get", lambda url, headers=None, timeout=5: (303, Headers(reply), b""))
    assert launcher.open_session(4321, "secret") == "aqe_session_4321=abc.def-1"
    # A cookie for another port (another local app) is not taken as this UI's session.
    reply["Set-Cookie"] = "aqe_session_9999=abc.def-1; Path=/"
    with pytest.raises(RuntimeError, match="did not accept"):
        launcher.open_session(4321, "secret")

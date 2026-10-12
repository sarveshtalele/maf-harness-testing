"""Launch identity: the API echoes the per-launch nonce only to its authenticated caller (F13), and the launcher
refuses to trust an API that does not echo it (no bearer token goes to a process that merely holds the port)."""

import importlib.util
import json
import threading
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

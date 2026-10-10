"""Runtime model/API-key settings: precedence, validation, secrecy, busy lock."""

import json

import pytest

from maf_test_harness import paths, settings
from maf_test_harness.hooks.builtin import redact
from maf_test_harness.ui.api import LocalAPI

KEY = "fake-test-0123456789abcdefWXYZ"


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "LLM_MODEL", "HARNESS_STAGE_MODELS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "_from_dotenv", {})
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(paths, "env_file", lambda: tmp_path / ".env")
    settings.clear_runtime()
    yield
    settings.clear_runtime()


@pytest.fixture
def api(tmp_path):
    a = LocalAPI(tmp_path / "runs")
    yield a
    a.close()


def test_precedence_runtime_over_env_over_dotenv(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("OPENAI_MODEL=from-dotenv\nOPENAI_API_KEY=dotenv-key-12345678\n")
    settings.load_dotenv(tmp_path / ".env")
    cur = settings.current()
    assert (cur["model"], cur["source"]) == ("from-dotenv", ".env")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key-12345678")
    assert settings.current()["source"] == "environment"
    settings.set_runtime(api_key=KEY, model="rt-model")
    cur = settings.current()
    assert (cur["api_key"], cur["model"], cur["source"]) == (KEY, "rt-model", "runtime")
    settings.clear_runtime()
    assert settings.current()["api_key"] == "env-key-12345678"


@pytest.mark.parametrize(
    "bad",
    [
        {"base_url": "http://example.com/v1"},
        {"base_url": "ftp://x"},
        {"base_url": "https://u:p@example.com"},
        {"model": ""},
        {"model": "x" * 101},
        {"stage_models": "[1]"},
        {"api_key": "has space"},
        {"surprise": 1},
    ],
)
def test_validation_rejects(api, bad):
    with pytest.raises(ValueError):
        api.save_model_settings(bad)


def test_http_allowed_for_localhost(api):
    assert api.save_model_settings({"base_url": "http://localhost:4000/v1"})["base_url"].startswith(
        "http://localhost"
    )


def test_key_configured_without_restart_and_never_echoed(api):
    assert api.config()["key_configured"] is False
    out = api.save_model_settings({"api_key": KEY, "model": "m1", "stage_models": {"test_design": "m2"}})
    assert out["key_configured"] and out["key_hint"] == KEY[-4:] and out["source"] == "runtime"
    assert out["stage_models"] == {"test_design": "m2"}
    assert api.config()["key_configured"] is True
    for payload in (out, api.model_settings(), api.config()):
        assert KEY not in json.dumps(payload)
    assert settings.model_for("test_design") == "m2" and settings.model_for("other") == "m1"
    # empty api_key keeps the stored key
    api.save_model_settings({"api_key": "", "model": "m3"})
    assert settings.current()["api_key"] == KEY


def test_busy_returns_conflict(api, monkeypatch):
    monkeypatch.setattr(api, "busy", lambda: True)
    with pytest.raises(RuntimeError):
        api.save_model_settings({"model": "m"})


def test_test_endpoint_never_leaks_key(api):
    assert api.test_model_settings()["ok"] is False
    api.save_model_settings({"api_key": KEY})

    def boom(cur):
        raise RuntimeError(f"401 for key {cur['api_key']}")

    api.model_probe = boom
    result = api.test_model_settings()
    assert result["ok"] is False and KEY not in json.dumps(result)
    api.model_probe = lambda cur: None
    assert api.test_model_settings() == {"ok": True, "message": "Connection works."}


def test_redaction_covers_runtime_key():
    settings.set_runtime(api_key=KEY)
    text, n = redact(f"call failed with {KEY} inside", keyed=False)
    assert KEY not in text and n == 1


def test_save_to_env_preserves_lines_and_mode(api, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# keep\nOTHER=1\nOPENAI_MODEL=old\n")
    api.save_model_settings({"api_key": KEY, "model": "new", "stage_models": {"a": "b"}, "save_to_env": True})
    text = env.read_text()
    assert "# keep" in text and "OTHER=1" in text and "OPENAI_MODEL=new" in text and "old" not in text
    assert f"OPENAI_API_KEY={KEY}" in text
    assert (env.stat().st_mode & 0o777) in {0o600, 0o666}  # Windows has no modes
    # round-trips through the .env reader
    monkey = {}
    import os

    saved = dict(os.environ)
    try:
        settings.load_dotenv(env)
        monkey = settings.parse_stage_models(os.environ["HARNESS_STAGE_MODELS"])
    finally:
        os.environ.clear()
        os.environ.update(saved)
    assert monkey == {"a": "b"}


def test_not_saved_to_env_by_default(api, tmp_path):
    api.save_model_settings({"api_key": KEY})
    assert not (tmp_path / ".env").exists()

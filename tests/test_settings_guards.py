"""Runtime settings guards: the key follows only its host, a bad STAGE_MODELS cannot stop the import, and only the
install-root .env is read (never one from the working directory)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from maf_test_harness import settings

SRC = str(Path(settings.__file__).resolve().parents[1])


@pytest.fixture(autouse=True)
def clean_runtime(monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "HARNESS_STAGE_MODELS"):
        monkeypatch.delenv(name, raising=False)
    settings.clear_runtime()
    yield
    settings.clear_runtime()


def _import_in_subprocess(code, env_extra=None, cwd=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENAI_", "HARNESS_", "LLM_"))}
    env.update(PYTHONPATH=SRC, **(env_extra or {}))
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=cwd, timeout=60
    )


def test_changing_the_host_with_a_saved_key_requires_a_new_key_in_the_same_request():
    settings.set_runtime(api_key="sk-test-1", base_url="https://api.example.test/v1")
    with pytest.raises(ValueError, match="requires a new API key"):
        settings.set_runtime(base_url="https://attacker.example.test/v1")
    assert settings.current()["base_url"] == "https://api.example.test/v1"  # nothing changed
    settings.set_runtime(base_url="https://attacker.example.test/v1", api_key="sk-test-2")
    assert settings.current()["base_url"] == "https://attacker.example.test/v1"


def test_same_host_or_no_saved_key_can_change_the_base_url():
    settings.set_runtime(api_key="sk-test-1", base_url="https://api.example.test/v1")
    settings.set_runtime(base_url="https://api.example.test/v2")  # same host
    settings.clear_runtime()
    settings.set_runtime(base_url="https://other.example.test/v1")  # no key saved yet


def test_invalid_stage_models_json_falls_back_instead_of_failing_the_import():
    proc = _import_in_subprocess(
        "from maf_test_harness import settings as s; print(s.STAGE_MODELS)",
        {"HARNESS_STAGE_MODELS": "{not json"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "{}"


def test_env_file_in_the_working_directory_is_not_read(tmp_path):
    (tmp_path / ".env").write_text("HARNESS_PLAN_REVIEW_MIN_CONFIDENCE=70\n", encoding="utf-8")
    proc = _import_in_subprocess(
        "from maf_test_harness import settings as s; print(s.review_threshold('plan'))", cwd=tmp_path
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "95"


def test_current_rejects_an_insecure_base_url_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://attacker.example.test/v1")
    with pytest.raises(ValueError, match="https"):
        settings.current()


def test_invalid_stage_models_in_the_environment_do_not_break_current(monkeypatch):
    monkeypatch.setenv("HARNESS_STAGE_MODELS", "[1, 2]")
    assert settings.current()["stage_models"] == {}


def test_stage_models_json_round_trips_through_set_runtime():
    settings.set_runtime(stage_models=json.dumps({"test_design": "m1"}))
    assert settings.current()["stage_models"] == {"test_design": "m1"}

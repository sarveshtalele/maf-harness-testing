import sys
from pathlib import Path

import pytest

from maf_test_harness import paths

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "maf_test_harness"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in (
        "HARNESS_RESOURCE_ROOT",
        "HARNESS_DATA_DIR",
        "HARNESS_RUNS_DIR",
        "HARNESS_LOG_DIR",
        "HARNESS_PLUGIN_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def test_source_defaults():
    assert paths.resource_root() == REPO
    assert paths.user_data_root() == REPO / ".maf-harness"
    assert paths.skills_dir() == REPO / "skills" and paths.prompts_dir() == REPO / "prompts"
    assert paths.runs_dir() == REPO / ".maf-harness" / "runs"
    assert paths.cache_dir() == REPO / ".maf-harness" / "cache"
    assert paths.plugin_dir() == REPO / ".maf-harness" / "plugins"
    assert paths.env_file() == REPO / ".env"


def test_env_overrides(monkeypatch, tmp_path):
    for name, fn in (
        ("HARNESS_RESOURCE_ROOT", paths.resource_root),
        ("HARNESS_DATA_DIR", paths.user_data_root),
        ("HARNESS_RUNS_DIR", paths.runs_dir),
        ("HARNESS_PLUGIN_DIR", paths.plugin_dir),
    ):
        monkeypatch.setenv(name, str(tmp_path / name))
        assert fn() == tmp_path / name
    assert paths.skills_dir() == tmp_path / "HARNESS_RESOURCE_ROOT" / "skills"


def _freeze(monkeypatch, platform, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))


def test_frozen_resources(monkeypatch, tmp_path):
    _freeze(monkeypatch, "linux", tmp_path)
    assert paths.resource_root() == tmp_path / "bundle"
    assert paths.skills_dir() == tmp_path / "bundle" / "skills"


def test_frozen_windows(monkeypatch, tmp_path):
    _freeze(monkeypatch, "win32", tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert paths.user_data_root() == tmp_path / "roaming" / "AgenticQE"
    assert paths.env_file() == tmp_path / "roaming" / "AgenticQE" / ".env"


def test_frozen_macos(monkeypatch, tmp_path):
    _freeze(monkeypatch, "darwin", tmp_path)
    assert paths.user_data_root() == tmp_path / "home" / "Library" / "Application Support" / "AgenticQE"


def test_frozen_linux_xdg(monkeypatch, tmp_path):
    _freeze(monkeypatch, "linux", tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert paths.user_data_root() == tmp_path / "xdg" / "agentic-qe"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert paths.plugin_dir() == tmp_path / "home" / ".local" / "share" / "agentic-qe" / "plugins"


def test_data_dir_overrides_frozen(monkeypatch, tmp_path):
    _freeze(monkeypatch, "linux", tmp_path)
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "d"))
    assert paths.runs_dir() == tmp_path / "d" / "runs"


def test_no_parents_root_computation_outside_paths():
    bad = [
        str(p.relative_to(REPO))
        for p in SRC.rglob("*.py")
        if p.name != "paths.py" and "parents[" in p.read_text(encoding="utf-8")
    ]
    assert bad == []

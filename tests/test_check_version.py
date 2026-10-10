import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "check_version", Path(__file__).resolve().parent.parent / "scripts" / "check_version.py"
)
cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cv)


def _root(tmp_path, py="1.2.0", js="1.2.0", desk="1.2.0"):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "desktop").mkdir()
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{py}"\n')
    (tmp_path / "frontend" / "package.json").write_text(json.dumps({"version": js}))
    (tmp_path / "desktop" / "package.json").write_text(json.dumps({"version": desk}))
    return tmp_path


@pytest.fixture(autouse=True)
def _no_tag(monkeypatch):
    monkeypatch.delenv("GITHUB_REF_TYPE", raising=False)
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)


def test_match(tmp_path):
    assert cv.check(_root(tmp_path)) is None


def test_mismatch(tmp_path):
    assert "mismatch" in cv.check(_root(tmp_path, js="1.3.0"))


def test_desktop_mismatch(tmp_path):
    err = cv.check(_root(tmp_path, desk="1.3.0"))
    assert err and "desktop/package.json=1.3.0" in err


@pytest.mark.parametrize("tag", ["v1.2.0", "v1.2.0-rc1"])
def test_tag_match(tmp_path, monkeypatch, tag):
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", tag)
    assert cv.check(_root(tmp_path)) is None


def test_tag_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "v9.9.9")
    assert "does not match" in cv.check(_root(tmp_path))


def test_main_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["x", "--root", str(_root(tmp_path, js="0.0.1"))])
    assert cv.main() == 1

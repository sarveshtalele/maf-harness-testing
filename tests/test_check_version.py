import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_version", ROOT / "scripts" / "check_version.py")
cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cv)


def _root(
    tmp_path,
    py="1.2.0",
    uv="1.2.0",
    js="1.2.0",
    js_lock="1.2.0",
    js_lock_root="1.2.0",
    desk="1.2.0",
    desk_lock="1.2.0",
    desk_lock_root="1.2.0",
):
    for folder in ("frontend", "desktop"):
        (tmp_path / folder).mkdir(exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "maf-test-harness"\nversion = "{py}"\n', "utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "other"\nversion = "9.9.9"\n\n'
        f'[[package]]\nname = "maf-test-harness"\nversion = "{uv}"\nsource = {{ editable = "." }}\n',
        "utf-8",
    )
    for folder, version, lock, lock_root in (
        ("frontend", js, js_lock, js_lock_root),
        ("desktop", desk, desk_lock, desk_lock_root),
    ):
        (tmp_path / folder / "package.json").write_text(json.dumps({"version": version}), "utf-8")
        (tmp_path / folder / "package-lock.json").write_text(
            json.dumps({"version": lock, "packages": {"": {"version": lock_root}}}), "utf-8"
        )
    return tmp_path


@pytest.fixture(autouse=True)
def _no_tag(monkeypatch):
    monkeypatch.delenv("GITHUB_REF_TYPE", raising=False)
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)


def test_match(tmp_path):
    assert cv.check(_root(tmp_path)) is None


@pytest.mark.parametrize(
    "field",
    ["uv", "js", "js_lock", "js_lock_root", "desk", "desk_lock", "desk_lock_root"],
)
def test_each_of_the_six_files_is_checked(tmp_path, field):
    err = cv.check(_root(tmp_path, **{field: "1.3.0"}))
    assert err and "mismatch" in err and "1.3.0" in err


def test_desktop_mismatch_names_the_file(tmp_path):
    err = cv.check(_root(tmp_path, desk="1.3.0"))
    assert err and "desktop/package.json=1.3.0" in err


def test_missing_uv_lock_entry_is_reported(tmp_path):
    root = _root(tmp_path)
    (root / "uv.lock").write_text('[[package]]\nname = "other"\nversion = "1.2.0"\n', "utf-8")
    assert "cannot read" in cv.check(root)


@pytest.mark.parametrize("tag", ["v1.2.0", "v1.2.0-rc1", "v1.2.0-rc12"])
def test_tag_match(tmp_path, monkeypatch, tag):
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", tag)
    assert cv.check(_root(tmp_path)) is None


@pytest.mark.parametrize("tag", ["v9.9.9", "v1.2.0-rcjunk", "v1.2.0-rc", "1.2.0", "v1.2.0-beta1", "v1.2.0 "])
def test_tag_must_match_the_version_strictly(tmp_path, monkeypatch, tag):
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", tag)
    assert cv.check(_root(tmp_path)) is not None


def test_branch_refs_are_not_tags(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REF_TYPE", "branch")
    monkeypatch.setenv("GITHUB_REF_NAME", "develop")
    assert cv.check(_root(tmp_path)) is None


def test_main_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["x", "--root", str(_root(tmp_path, js="0.0.1"))])
    assert cv.main() == 1


def test_repository_versions_agree():
    assert cv.check(ROOT) is None, "the six version files of this checkout disagree"

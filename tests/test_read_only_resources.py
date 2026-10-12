"""The installed app keeps its bundled resources read-only (Program Files, /Applications). Nothing may write there.

The self-check runs the host skill scripts (reverse-engineering, validators) and the lint against a read-only copy of
the skills and prompts, with HARNESS_RESOURCE_ROOT pointing at it. Python must neither fail on nor leave behind any
bytecode inside the resource tree. Skipped where file modes do not restrict writes (Windows, root).
"""

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from maf_test_harness import desktop_backend
from maf_test_harness.skills import scripts

REPO = Path(__file__).resolve().parents[1]
POSIX = os.name != "nt" and hasattr(os, "geteuid")
pytestmark = [
    pytest.mark.skipif(not POSIX, reason="chmod read-only semantics are POSIX-only"),
    pytest.mark.skipif(POSIX and os.geteuid() == 0, reason="root ignores file modes"),
]


def _set_tree_writable(root: Path, writable: bool) -> None:
    for path in [root, *root.rglob("*")]:
        mode = path.stat().st_mode
        if writable:
            os.chmod(path, mode | stat.S_IWUSR)
        else:
            os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


@pytest.fixture
def read_only_resources(tmp_path):
    resources = tmp_path / "install" / "backend"
    for name in ("skills", "prompts"):
        shutil.copytree(REPO / name, resources / name, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    _set_tree_writable(resources, writable=False)
    try:
        yield resources
    finally:
        _set_tree_writable(resources, writable=True)


def test_self_check_passes_with_read_only_resources_and_writes_no_bytecode(
    read_only_resources, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("HARNESS_RESOURCE_ROOT", str(read_only_resources))
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    # Skill scripts import maf_test_harness; the child inherits no PYTHONPATH, so point it at this checkout (as the
    # other smoke tests do) instead of an editable install that may be a different checkout.
    real_run_command = scripts.run_command

    def run_this_checkout(argv, cwd, output_dir, **kwargs):
        kwargs["env"] = {**(kwargs.get("env") or {}), "PYTHONPATH": str(REPO / "src")}
        return real_run_command(argv, cwd, output_dir, **kwargs)

    monkeypatch.setattr(scripts, "run_command", run_this_checkout)
    monkeypatch.chdir(tmp_path)

    assert desktop_backend.self_check() == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "reverse-engineering [python repo]" in out  # the host skill script really ran
    assert "self-check OK" in out
    assert not list(read_only_resources.rglob("__pycache__"))


def test_frozen_shim_runs_a_read_only_bundled_script_without_writing_bytecode(
    read_only_resources, tmp_path, monkeypatch, capsys
):
    package = read_only_resources / "skills" / "reverse-engineering" / "scripts"
    helper = package / "_read_only_probe_helper.py"
    script = package / "_read_only_probe.py"
    _set_tree_writable(read_only_resources, writable=True)
    helper.write_text("VALUE = 7\n", encoding="utf-8")
    script.write_text("import _read_only_probe_helper as h\nprint('VALUE', h.VALUE)\n", encoding="utf-8")
    _set_tree_writable(read_only_resources, writable=False)
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.chdir(tmp_path)

    assert desktop_backend.main(["--run-python", str(script)]) == 0
    assert "VALUE 7" in capsys.readouterr().out
    assert list(read_only_resources.rglob("__pycache__")) == []

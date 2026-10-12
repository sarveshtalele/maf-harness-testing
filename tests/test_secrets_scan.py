import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("secrets_scan", ROOT / "scripts" / "secrets_scan.py")
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)

# Split in source so this file itself passes the scan; the joined values are what the pattern must catch.
CREDENTIAL_SHAPES = [
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "xox" + "b-1234567890-abcdefghij",
    "xox" + "p-1234567890-abcdefghij",
    "sk" + "-" + "a" * 20,
    "gh" + "p_" + "a" * 20,
    "-----BEGIN " + "RSA PRIVATE KEY-----",
    "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    "-----BEGIN " + "PRIVATE KEY-----",
]
NOT_CREDENTIALS = [
    "AKIA_SHORT",
    "xoxb-short",
    "-----BEGIN PUBLIC KEY-----",
    "sk-short",
    "ghp_short",
    "Bearer abcdefghijklmnopqrstuvwxyz0123",  # a generic bearer token has no provider key shape
]


@pytest.mark.parametrize("text", CREDENTIAL_SHAPES)
def test_credential_shapes_match(text):
    assert scan.PATTERN.search(text), text


@pytest.mark.parametrize("text", NOT_CREDENTIALS)
def test_ordinary_text_does_not_match(text):
    assert not scan.PATTERN.search(text), text


def test_find_reports_file_and_line(tmp_path):
    (tmp_path / "ok.txt").write_text("nothing here\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "leak.py").write_text(
        "x = 1\nkey = '" + CREDENTIAL_SHAPES[0] + "'\n", encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(CREDENTIAL_SHAPES[0], encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text(CREDENTIAL_SHAPES[0], encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\0" + CREDENTIAL_SHAPES[0].encode())
    assert scan.find(tmp_path) == ["sub/leak.py:2"]


def test_git_work_tree_scans_tracked_files_only(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tracked.txt").write_text(CREDENTIAL_SHAPES[0], encoding="utf-8")
    (tmp_path / "ignored.txt").write_text(
        CREDENTIAL_SHAPES[0], encoding="utf-8"
    )  # untracked, so not in git ls-files
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    assert scan.find(tmp_path) == ["tracked.txt:1"]


def test_main_exit_codes(tmp_path, capsys):
    (tmp_path / "clean.txt").write_text("ok\n", encoding="utf-8")
    assert scan.main([str(tmp_path)]) == 0
    (tmp_path / "leak.txt").write_text(CREDENTIAL_SHAPES[1], encoding="utf-8")
    assert scan.main([str(tmp_path)]) == 1
    assert "leak.txt:1" in capsys.readouterr().out

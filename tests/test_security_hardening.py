"""Regression tests for the security review (F-series): reads that refuse links, bounded probes, and so on."""

import os
import sys

import pytest

from maf_test_harness import storage
from maf_test_harness.ui import evidence


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_evidence_read_refuses_a_symlink_at_the_final_component(tmp_path):
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("private key material", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    link = run / "report.txt"
    try:
        os.symlink(secret, link)
    except OSError:
        pytest.skip("symlinks are not permitted on this host")
    with pytest.raises(OSError):
        evidence.clean_content(link)
    (run / "plain.txt").write_text("ok", encoding="utf-8")
    assert evidence.clean_content(run / "plain.txt") == b"ok"


def test_read_regular_file_returns_the_bytes_of_a_regular_file(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"\x00payload")
    assert storage.read_regular_file(path) == b"\x00payload"


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW") or sys.platform == "win32", reason="POSIX no-follow only")
def test_read_regular_file_detects_a_swap_between_check_and_read(tmp_path, monkeypatch):
    target = tmp_path / "target.txt"
    target.write_text("inside", encoding="utf-8")
    other = tmp_path / "other.txt"
    other.write_text("outside", encoding="utf-8")
    real_fstat = os.fstat

    def swap_then_fstat(fd):
        # Simulates a concurrent replacement after the open: the path now names a different object.
        target.unlink()
        target.write_text("replaced", encoding="utf-8")
        return real_fstat(fd)

    monkeypatch.setattr(storage.os, "fstat", swap_then_fstat)
    with pytest.raises(FileNotFoundError, match="changed while it was being read"):
        storage.read_regular_file(target)

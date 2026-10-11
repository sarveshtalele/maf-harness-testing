"""Rerun moves run folders into the attempt archive, tolerating short Windows sharing locks."""

import pytest

from maf_test_harness.workflow_steps import rerun


class _Folder:
    def __init__(self, name, failures):
        self.name = name
        self.failures = failures
        self.calls = 0

    def rename(self, target):
        self.calls += 1
        if self.calls <= self.failures:
            error = PermissionError(13, "The process cannot access the file because it is being used")
            error.winerror = 32  # ERROR_SHARING_VIOLATION
            raise error


def test_move_retries_sharing_violation_then_succeeds(monkeypatch):
    monkeypatch.setattr("maf_test_harness.storage.time.sleep", lambda _s: None)
    folder = _Folder("repo", failures=2)
    rerun._move_into_archive(folder, "target")
    assert folder.calls == 3


def test_move_reports_clear_error_after_final_failure(monkeypatch):
    monkeypatch.setattr("maf_test_harness.storage.time.sleep", lambda _s: None)
    folder = _Folder("workspace", failures=10_000)
    with pytest.raises(RuntimeError) as info:
        rerun._move_into_archive(folder, "target")
    assert str(info.value) == (
        "Could not move workspace into the attempt archive; close programs using files in the run folder and retry."
    )
    assert isinstance(info.value.__cause__, PermissionError)

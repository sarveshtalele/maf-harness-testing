"""Rerun moves run folders into the attempt archive, tolerating short Windows sharing locks and rolling back on failure."""

import logging
from pathlib import Path

import pytest

from maf_test_harness.workflow_steps import rerun

SHARING = "The process cannot access the file because it is being used"


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """Real run folders plus a Path.rename that fails for named folders, with time.sleep stubbed out."""
    monkeypatch.setattr("maf_test_harness.storage.time.sleep", lambda _s: None)
    run = tmp_path / "run"
    (run / "repo").mkdir(parents=True)
    (run / "workspace").mkdir()
    (run / "repo" / "a.py").write_text("x", encoding="utf-8")
    archive = run / "attempts" / "1"
    archive.mkdir(parents=True)
    failures: dict[str, tuple[int, BaseException]] = {}
    original = Path.rename

    def rename(self, target):
        key = f"{self.parent.name}/{self.name}"  # e.g. "run/repo" when moving out of the run folder
        if key in failures:
            count, error = failures[key]
            if count:
                failures[key] = (count - 1, error)
                raise error
        return original(self, target)

    monkeypatch.setattr(Path, "rename", rename)

    def fail(key, times, winerror=32):
        # EACCES (13) is retried on Windows as a sharing lock; a non-sharing error uses EPERM (1) on every OS.
        error = PermissionError(13 if winerror is not None else 1, SHARING)
        if winerror is not None:
            error.winerror = winerror
        failures[key] = (times, error)
        return error

    return run, archive, fail


def test_move_retries_sharing_violation_then_succeeds(folders):
    run, archive, fail = folders
    fail("run/repo", times=2)
    rerun._move_into_archive(run / "repo", archive / "repo")
    assert (archive / "repo" / "a.py").exists() and not (run / "repo").exists()


def test_final_sharing_violation_is_rewrapped_with_clear_message(folders):
    run, archive, fail = folders
    fail("run/workspace", times=10_000)
    with pytest.raises(RuntimeError) as info:
        rerun._move_into_archive(run / "workspace", archive / "workspace")
    assert str(info.value) == (
        "Could not move workspace into the attempt archive; close programs using files in the run folder and retry."
    )
    assert isinstance(info.value.__cause__, PermissionError)
    assert (run / "workspace").exists()


def test_non_sharing_permission_error_is_reraised_unchanged(folders):
    run, archive, fail = folders
    error = fail("run/workspace", times=1, winerror=None)
    with pytest.raises(PermissionError) as info:
        rerun._move_into_archive(run / "workspace", archive / "workspace")
    assert info.value is error


def test_failed_second_move_restores_the_first_folder(folders):
    run, archive, fail = folders
    fail("run/workspace", times=10_000)
    moves = [(run / "repo", archive / "repo"), (run / "workspace", archive / "workspace")]
    with pytest.raises(RuntimeError, match="Could not move workspace"):
        rerun._archive_run_folders(moves)
    assert (run / "repo" / "a.py").exists() and not (archive / "repo").exists()
    assert (run / "workspace").exists()


def test_non_sharing_error_on_second_move_is_reraised_and_rolled_back(folders):
    run, archive, fail = folders
    error = fail("run/workspace", times=1, winerror=None)
    moves = [(run / "repo", archive / "repo"), (run / "workspace", archive / "workspace")]
    with pytest.raises(PermissionError) as info:
        rerun._archive_run_folders(moves)
    assert info.value is error
    assert (run / "repo").exists() and not (archive / "repo").exists()


def test_rollback_failure_is_logged_and_original_error_kept(folders, caplog):
    run, archive, fail = folders
    fail("run/workspace", times=10_000)
    fail("1/repo", times=10_000)  # moving repo back out of the archive fails too
    moves = [(run / "repo", archive / "repo"), (run / "workspace", archive / "workspace")]
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="Could not move workspace"):
            rerun._archive_run_folders(moves)
    assert "Could not restore" in caplog.text
    assert (archive / "repo" / "a.py").exists()

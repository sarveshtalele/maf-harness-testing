"""Git transport recovery preserves certificate verification and failed evidence."""

import subprocess

import pytest

from maf_test_harness.repo import analyzer
from maf_test_harness.workflow_support import atomic_json


def test_schannel_retries_use_http1_then_verified_openssl_without_global_changes(tmp_path, monkeypatch):
    calls = []
    destination = tmp_path / "repo"
    destination.mkdir()
    (destination / "retained.txt").write_text("existing evidence")
    monkeypatch.setattr(analyzer.sys, "platform", "win32")
    monkeypatch.setenv("GIT_SSL_NO_VERIFY", "1")

    def git(command, **kwargs):
        calls.append((command, kwargs))
        destination.mkdir()
        (destination / "partial.txt").write_text(f"attempt {len(calls)}")
        return subprocess.CompletedProcess(
            command,
            0 if len(calls) == 3 else 128,
            "",
            "fatal: schannel: server closed abruptly (missing close_notify)",
        )

    monkeypatch.setattr(analyzer.subprocess, "run", git)
    assert analyzer.clone_repository("https://github.com/acme/demo", destination)[1] == "demo"
    assert len(calls) == 3
    assert "http.version=HTTP/1.1" in calls[1][0]
    assert "http.sslBackend=openssl" in calls[2][0]
    assert "http.sslBackend=openssl" not in calls[0][0]
    for command, kwargs in calls:
        assert "http.sslVerify=true" in command and "config" not in command
        assert "GIT_SSL_NO_VERIFY" not in kwargs["env"]
        assert kwargs["encoding"] == "utf-8"
    assert len(list((tmp_path / "clone-attempts").glob("*/repo"))) == 3
    assert list((tmp_path / "clone-attempts").glob("*/repo/retained.txt"))
    assert "succeeded on attempt 3" in (tmp_path / "clone.log").read_text()


@pytest.mark.parametrize(
    "error",
    ["fatal: repository not found", "SSL certificate problem: unable to get local issuer certificate"],
)
def test_clone_does_not_retry_authentication_or_certificate_failures(tmp_path, monkeypatch, error):
    calls = []

    def git(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 128, "", error)

    monkeypatch.setattr(analyzer.subprocess, "run", git)
    with pytest.raises(analyzer.RepoCloneError, match=error):
        analyzer.clone_repository("https://github.com/acme/demo", tmp_path / "repo")
    assert len(calls) == 1


def test_transient_windows_json_replace_keeps_previous_state_until_success(tmp_path, monkeypatch):
    path = tmp_path / "run.json"
    path.write_text('{"status":"waiting"}')
    replace = type(path).replace
    calls = []

    def locked_once(source, target):
        calls.append(source)
        if len(calls) < 3:
            assert path.read_text() == '{"status":"waiting"}'
            error = PermissionError("Windows sharing violation")
            error.winerror = 5
            raise error
        return replace(source, target)

    monkeypatch.setattr(type(path), "replace", locked_once)
    atomic_json(path, {"status": "stopped"})
    assert len(calls) == 3
    assert '"stopped"' in path.read_text()


def test_permanent_permissions_error_is_not_hidden(tmp_path, monkeypatch):
    def forbidden(source, target):
        raise PermissionError("permission denied")

    monkeypatch.setattr(type(tmp_path), "replace", forbidden)
    with pytest.raises(PermissionError):
        atomic_json(tmp_path / "run.json", {"status": "stopped"})

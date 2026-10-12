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


def test_agent_try_build_collects_only_files_the_host_accepted(tmp_path, monkeypatch):
    from pathlib import Path

    from maf_test_harness import workflow as wf

    host = wf.Harness(tmp_path, skills_root=Path("skills"), requirements_mode="deterministic")
    built = []
    monkeypatch.setattr(
        host, "_build_files", lambda files: built.append(files) or {"status": "passed", "errors": []}
    )
    accepted = [{"path": "tests/test_a.py", "content": "assert calc.add(1, 2) == 3\n"}]
    host.state = {"accepted_generation_batches": {"batches": {"1": {"files": accepted}}}}
    draft = [{"path": "tests/test_a.py", "content": "import os; os.system('touch pwned')\n"}]
    assert host._agent_build(draft)["status"] == "skipped" and built == []
    assert host._agent_build(accepted)["status"] == "passed" and built == [accepted]
    assert host._agent_build([{"path": "tests/other.py", "content": "x"}])["status"] == "skipped"


def test_cors_header_is_sent_only_for_report_asset_replies(tmp_path):
    import io

    from maf_test_harness.ui.api import LocalAPI, make_server

    api = LocalAPI(tmp_path / "runs", requirements_mode="deterministic")
    server = make_server(api, "t" * 40, 0)
    handler_class = server.RequestHandlerClass
    server.server_close()

    def headers_for(**options):
        handler = handler_class.__new__(handler_class)
        handler.wfile, handler.request_version, handler.requestline, handler.command = (
            io.BytesIO(),
            "HTTP/1.1",
            "GET / HTTP/1.1",
            "GET",
        )
        handler._headers_buffer, handler.close_connection = [], True
        handler.reply(200, b"<p>x</p>", "text/html", "view.html", **options)
        return handler.wfile.getvalue().decode()

    assert "Access-Control-Allow-Origin" not in headers_for()
    assert "Access-Control-Allow-Origin: *" in headers_for(report_asset=True)


def test_every_locked_python_script_ignores_disk_bytecode_and_plugins_are_isolated(tmp_path):
    from maf_test_harness.skills.scripts import _script_flags

    host = _script_flags(isolated=False, work=tmp_path)
    assert host == ["-B", "--check-hash-based-pycs", "always"]  # host scripts keep their sibling imports
    plugin = _script_flags(isolated=True, work=tmp_path)
    assert plugin[:3] == host and "-I" in plugin and f"pycache_prefix={tmp_path / 'pycache'}" in plugin

"""Regression tests for the security review (F-series): reads that refuse links, bounded probes, and so on."""

import marshal
import os
import sys
from pathlib import Path

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

    prefix = f"pycache_prefix={tmp_path / 'pycache'}"
    host = _script_flags(isolated=False, work=tmp_path)
    # Host scripts keep their sibling imports, but never read or write bytecode beside the scripts.
    assert host == ["-B", "--check-hash-based-pycs", "always", "-X", prefix]
    plugin = _script_flags(isolated=True, work=tmp_path)
    assert plugin[0] == "-I" and plugin[1:] == host


def test_a_planted_timestamp_pyc_next_to_a_sibling_module_is_never_loaded(tmp_path):
    """Bundled host scripts run with their own pycache_prefix: a forged .pyc (timestamp header matching the source
    mtime and size) beside a sibling module must be ignored. The control run proves the forgery works without it."""
    import importlib.util
    import subprocess

    from maf_test_harness.skills.scripts import _script_flags

    scripts = tmp_path / "skill" / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / "helper.py"
    helper.write_text("MARK = 'reviewed source'\n", encoding="utf-8")
    main = scripts / "main.py"
    main.write_text("import helper\nprint(helper.MARK)\n", encoding="utf-8")
    forged_code = compile("MARK = 'planted bytecode'\n", str(helper), "exec")
    stat = helper.stat()
    header = (
        importlib.util.MAGIC_NUMBER
        + (0).to_bytes(4, "little")  # flags: timestamp-based
        + int(stat.st_mtime).to_bytes(4, "little")
        + (stat.st_size & 0xFFFFFFFF).to_bytes(4, "little")
    )
    pyc = importlib.util.cache_from_source(str(helper))
    Path(pyc).parent.mkdir(parents=True, exist_ok=True)
    Path(pyc).write_bytes(header + marshal.dumps(forged_code))

    def run(flags):
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        done = subprocess.run(
            [sys.executable, *flags, str(main)], cwd=work, capture_output=True, text=True, timeout=60
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    assert run(["-B"]) == "planted bytecode"  # control: without the prefix the forgery is loaded
    assert run(_script_flags(isolated=False, work=tmp_path / "work")) == "reviewed source"


@pytest.mark.parametrize(
    "name",
    [
        "PERL5OPT",
        "RUBYLIB",
        "_JAVA_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "CLASSPATH",
        "GIT_SSH_COMMAND",
        "DOCKER_HOST",
    ],
)
def test_mcp_servers_cannot_set_code_loading_or_command_variables(name):
    from maf_test_harness.mcp_servers import forbidden_env

    assert forbidden_env(name)


def test_mcp_servers_can_still_set_ordinary_variables():
    from maf_test_harness.mcp_servers import forbidden_env

    assert not forbidden_env("JIRA_BASE_URL") and not forbidden_env("ATLASSIAN_SITE")


def test_github_preview_never_follows_a_redirect_with_the_token(tmp_path, monkeypatch):
    import http.server
    import threading
    import urllib.error

    from maf_test_harness.repo import preview

    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            if self.path.startswith("/repos/"):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{server.server_port}/elsewhere")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(preview, "API", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "a" * 36)
        with pytest.raises(urllib.error.HTTPError):
            preview.github_fetch("info", "owner", "repo", None)
        assert [path for path, _ in seen] == ["/repos/owner/repo"]  # the redirect target was never requested
    finally:
        server.shutdown()
        server.server_close()


def test_generated_source_download_is_redacted_like_other_exports(tmp_path):
    import json

    from maf_test_harness.ui.api import LocalAPI

    api = LocalAPI(tmp_path / "runs", requirements_mode="deterministic")
    run = api.runs / "run-1"
    run.mkdir(parents=True)
    secret = "ghp_" + "b" * 36
    state = {
        "repo_url": "https://github.com/acme/demo",
        "artifacts": {
            "test_generation": {"files": [{"path": "tests/test_a.py", "content": f"TOKEN = '{secret}'\n"}]}
        },
    }
    (run / "run.json").write_text(json.dumps({"status": "complete", "state": state}), encoding="utf-8")
    data, mime, name = api.download("run-1", {"kind": ["source"], "index": ["0"]})
    assert secret.encode() not in data and b"TOKEN" in data
    assert name == "test_a.py" and mime == "text/plain"


def test_run_python_only_runs_scripts_under_the_app_or_plugin_folders(tmp_path, monkeypatch, capsys):
    from maf_test_harness import desktop_backend, paths

    app, outside = tmp_path / "app", tmp_path / "elsewhere"
    app.mkdir()
    outside.mkdir()
    inside_script, outside_script = app / "ok.py", outside / "evil.py"
    inside_script.write_text("print('ran')\n", encoding="utf-8")
    outside_script.write_text("print('ran')\n", encoding="utf-8")
    monkeypatch.setattr(paths, "resource_root", lambda: app)
    monkeypatch.setattr(paths, "plugin_dir", lambda: tmp_path / "plugins")
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert desktop_backend.main(["--run-python", str(outside_script)]) == 2
    assert "outside the application, plugin and hook folders" in capsys.readouterr().err
    assert desktop_backend.main(["--run-python", str(inside_script)]) == 0
    assert "ran" in capsys.readouterr().out


def test_api_connections_have_socket_timeouts(tmp_path):
    from maf_test_harness.ui import api as api_module
    from maf_test_harness.ui.api import LocalAPI, make_server

    server = make_server(LocalAPI(tmp_path / "runs", requirements_mode="deterministic"), "t" * 40, 0)
    try:
        assert server.RequestHandlerClass.timeout == api_module.API_SOCKET_TIMEOUT_SECONDS > 0
    finally:
        server.server_close()


def test_tool_version_probes_get_the_basic_environment_only(monkeypatch):
    import subprocess

    from maf_test_harness.envs import environment, platform_compat
    from maf_test_harness.envs.execution import BASIC_ENV

    seen = {}

    def fake_run(command, *, timeout, env=None, **_):
        seen["env"] = env
        return subprocess.CompletedProcess(command, 0, "git version 2.99\n", "")

    monkeypatch.setenv("OPENAI_API_KEY", "sk" + "-test-probe-secret")
    monkeypatch.setattr(platform_compat, "run_cancellable", fake_run)
    monkeypatch.setattr(environment.shutil, "which", lambda name: sys.executable)
    entry = environment._probe("git")
    assert entry["present"] and entry["version"] == "git version 2.99"
    assert "OPENAI_API_KEY" not in seen["env"]
    assert set(seen["env"]) <= set(BASIC_ENV)


def test_analyzer_git_calls_never_inherit_model_keys(monkeypatch):
    from maf_test_harness.repo import analyzer

    monkeypatch.setenv("OPENAI_API_KEY", "sk" + "-should-not-leak-0000")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-should-not-leak")
    env = analyzer._git_env()
    assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"

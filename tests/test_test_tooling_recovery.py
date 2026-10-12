"""ENV-PY-010: a test client names a package the static tooling map missed (real run 1a9bbae78ff0, 7 setup errors)."""

from __future__ import annotations

from types import SimpleNamespace

from maf_test_harness.envs import recovery, runners
from maf_test_harness.envs.runners import Step

REAL = (
    "E               RuntimeError: The starlette.testclient module requires the httpx2 package to be installed.\n"
    "E               You can install this with:\n"
    "E                   $ pip install httpx2\n"
    "\n.harness-venv/lib/python3.12/site-packages/starlette/testclient.py:41: RuntimeError\n"
)
NO_MODULE = (
    ".harness-venv/lib/python3.12/site-packages/some_pkg/testclient.py:5: in <module>\n"
    "    import fancyhttp\n"
    "E   ModuleNotFoundError: No module named 'fancyhttp'\n"
)


def test_packages_from_real_excerpt_and_variants(tmp_path):
    assert recovery.test_client_packages(REAL, tmp_path) == ["httpx2"]
    assert recovery.test_client_packages(NO_MODULE, tmp_path) == ["fancyhttp"]
    # an application import error is not a test-client problem
    assert (
        recovery.test_client_packages("E   ModuleNotFoundError: No module named 'app_dep'\n", tmp_path) == []
    )
    # the repository's own declared dependencies are never touched
    (tmp_path / "requirements.txt").write_text("httpx2==1.0\n")
    assert recovery.test_client_packages(REAL, tmp_path) == []


def test_recovery_installs_once_and_reruns(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        runners, "run_command", lambda argv, *a, **k: calls.append(argv) or {"status": "command_succeeded"}
    )
    log = tmp_path / "stdout.log"
    log.write_text(REAL)
    monkeypatch.setattr(
        runners, "_run_test_step", lambda *a: ({"name": "pytest", "status": "passed"}, [{"status": "passed"}])
    )
    step = Step("pytest", ["py", "-m", "pytest"], "test")
    report: dict = {"steps": []}
    entry, cases = runners._recover_test_tooling(
        step,
        tmp_path,
        tmp_path,
        "03-pytest",
        {"stdout": str(log)},
        [{"status": "error", "message": REAL}],
        report,
    )
    assert calls[0][-1] == "httpx2" and cases == [{"status": "passed"}] and entry["status"] == "passed"
    assert report["recoveries"][0]["recipe"] == "add-test-dependency" and len(report["steps"]) == 1


def test_probe_picks_httpx2_from_testclient_source(tmp_path, monkeypatch):
    src = tmp_path / "testclient.py"
    src.write_text("try:\n    import httpx2 as httpx\nexcept ModuleNotFoundError:\n    import httpx\n")
    monkeypatch.setattr(recovery.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=str(src)))
    python = tmp_path / ".harness-venv" / "bin" / "python"  # absolute: the probe home lives beside this venv
    assert recovery.testclient_http_package(python) == "httpx2"
    tooling = Step("install-test-tooling", ["py", "pip", "install", "httpx"], "setup")
    assert runners._tune_test_tooling(tooling, tmp_path).argv[-1] == "httpx2"
    src.write_text("import httpx\n")
    assert recovery.testclient_http_package(python) == "httpx"
    assert runners._tune_test_tooling(tooling, tmp_path).argv[-1] == "httpx"

"""Opt-in browser smoke test for the actual Streamlit application."""

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("HARNESS_BROWSER") != "1", reason="Opt-in local UI browser check"
)


@pytest.fixture
def harness_url(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parents[2]
    url = f"http://127.0.0.1:{port}"
    with (tmp_path / "streamlit.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(root / "app.py"),
                "--server.address=127.0.0.1",
                f"--server.port={port}",
                "--server.headless=true",
                "--browser.gatherUsageStats=false",
            ],
            stdout=log,
            stderr=log,
            cwd=root,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(url + "/_stcore/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                pytest.fail("Streamlit did not become ready")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def test_harness_rejects_non_github_url_before_provider(page, harness_url):
    from playwright.sync_api import expect

    page.goto(harness_url)
    expect(page.get_by_role("heading", name="MAF Test Harness", exact=True)).to_be_visible()
    page.get_by_role("textbox", name="Public GitHub repository URL").fill("https://example.com/not-github")
    page.get_by_role("button", name="Start autonomous review").click()
    expect(page.get_by_text("Use a public HTTPS GitHub URL", exact=False)).to_be_visible()


# ---- real-browser check of the execution/QA gates on a run produced by the real workflow (model scripted) ----
def _seed_run_at_qa_review(runs_dir: Path, monkeypatch, body: str) -> str:
    """Drive the real workflow (scripted model, real pytest execution) to the QA review gate."""
    import sys as _sys

    sys_path = str(Path(__file__).resolve().parents[1])
    if sys_path not in _sys.path:
        _sys.path.insert(0, sys_path)
    from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
    from test_execution_stage import local_pytest_plan

    import maf_test_harness.workflow as wf
    from maf_test_harness.runtime_service import RuntimeService

    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    monkeypatch.setattr(wf, "build_plan", local_pytest_plan)
    monkeypatch.setattr(wf, "build_environment_plan", local_env_plan)
    root = Path(__file__).resolve().parents[2]
    host = wf.Harness(
        runs_dir / "seeded-run",
        skills_root=root / "skills",
        runtime=ScriptedStageRuntime(test_body=body),
        cache_dir=runs_dir / "cache",
    )
    service = RuntimeService()
    try:
        service.submit(host.start("https://github.com/acme/demo")).result(15)
        for _ in range(9):  # clone, repo, req, env install+review, plan, design, generation, run tests
            service.submit(host.respond(host.view["pending"]["id"], {"decision": "approve"})).result(60)
        assert host.view["pending"]["stage"] == "test_execution" and host.view["pending"]["kind"] == "review"
    finally:
        service.close()
    return "seeded-run"


@pytest.fixture
def qa_ui(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    _seed_run_at_qa_review(runs, monkeypatch, "assert add(1, 2) == 3")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "HARNESS_RUNS_DIR": str(runs),
        "OPENAI_API_KEY": "dummy-not-used",
        "OPENAI_BASE_URL": "http://127.0.0.1:9",
    }
    with (tmp_path / "ui.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(root / "app.py"),
                "--server.address=127.0.0.1",
                f"--server.port={port}",
                "--server.headless=true",
                "--browser.gatherUsageStats=false",
            ],
            stdout=log,
            stderr=log,
            cwd=root,
            env=env,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1):
                        break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            yield f"http://127.0.0.1:{port}"
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_ui_shows_qa_report_and_completes_on_approval(page, qa_ui):
    from playwright.sync_api import expect

    page.goto(qa_ui)
    expect(page.get_by_text("Running application URL (optional)")).to_be_visible()
    expect(page.get_by_text("Test Execution & QA: QA Execution Engineer (host, no model)")).to_be_visible()
    box = page.locator("section[data-testid=stSidebar]").get_by_role("combobox").first
    box.click()
    box.fill("seeded")  # Streamlit's dropdown renders options only while filtering
    page.get_by_role("option", name="seeded-run").click()
    page.get_by_role("button", name="Resume selected run").click()
    values = page.get_by_test_id("stMetricValue")
    expect(values.filter(has_text="PASS").first).to_be_visible()  # QA verdict metric
    expect(page.get_by_text("QA verdict", exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Regenerate failing tests")).to_be_visible()
    page.get_by_role("button", name="Approve and proceed").click()
    expect(page.get_by_text("QA verdict PASS", exact=False).first).to_be_visible()

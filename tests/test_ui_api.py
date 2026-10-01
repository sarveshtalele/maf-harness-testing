"""Adapter proof: real workflow/subprocesses, scripted model and local clone fixture."""

import base64
import io
import json
import re
import threading
import time
import zipfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
from test_execution_stage import local_pytest_plan

import maf_test_harness.workflow as wf
from maf_test_harness.ui.api import LocalAPI, make_server


@pytest.fixture
def local_api(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    monkeypatch.setattr(wf, "build_plan", local_pytest_plan)
    monkeypatch.setattr(wf, "build_environment_plan", local_env_plan)
    api = LocalAPI(tmp_path / "runs", runtime_factory=ScriptedStageRuntime)
    yield api
    wait(api)
    api.close()


def wait(api):
    deadline = time.monotonic() + 120
    while api.busy():
        assert time.monotonic() < deadline, "Operation did not complete"
        time.sleep(0.01)


def approve(api, key):
    run = api.read(key)
    api.respond(key, {"request_id": run["pending"]["id"], "decision": "approve"})
    wait(api)
    assert not api.errors


def test_api_complete_workflow_and_downloads(local_api):
    api = local_api
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    assert len(api.list_runs()["runs"]) == 1
    original = api.read(key)["pending"]["id"]
    approve(api, key)
    with pytest.raises(RuntimeError, match="Stale"):
        api.respond(key, {"request_id": original, "decision": "approve"})
    current = api.read(key)["pending"]
    with pytest.raises(ValueError, match="instructions"):
        api.respond(key, {"request_id": current["id"], "decision": "revise"})
    assert api.read(key)["pending"]["id"] == current["id"]
    for _ in range(9):
        approve(api, key)
    run = api.read(key)
    assert run["status"] == "complete" and run["has_zip"]
    assert run["state"]["artifacts"]["test_execution"]["qa_report"]["verdict"] == "PASS"
    assert "sessions" not in run["state"] and "state" not in (run["pending"] or {})
    assert api.download(key, {"kind": ["zip"]})[0].startswith(b"PK")
    assert "def test_" in api.download(key, {"kind": ["source"], "index": ["0"]})[0].decode()
    assert json.loads(api.download(key, {"kind": ["qa"]})[0])["verdict"] == "PASS"
    assert "# Observability" in api.download(key, {"kind": ["observability"], "format": ["md"]})[0].decode()
    assert any(e["event"] == "human_response" for e in api.audit(key)["events"])


def test_project_skill_catalog(local_api):
    catalog = local_api.skills()
    cards = {card["name"]: card for card in catalog["skills"]}
    assert len(cards) == 10 and all(card["valid"] for card in cards.values())
    assert cards["repository-requirements"]["primary_stages"] == ["requirements"]
    assert cards["playwright-test-generator"]["role"] == "Companion"
    assert cards["coverage-analyzer"]["role"] == "Host report validator"
    assert all(card["instructions"] and "SKILL.md" in card["resources"] for card in cards.values())
    assert any(card["reviewed_scripts"] for card in cards.values())


def test_evidence_is_contained_redacted_and_preserves_reports(local_api, tmp_path, monkeypatch):
    api = local_api
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    folder = api.run_path(key) / "executions" / "attempt-1" / "playwright-report"
    folder.mkdir(parents=True)
    monkeypatch.setenv("OPENAI_API_KEY", "private-evidence-value")
    (folder / "index.html").write_text("<h1>Playwright report</h1><p>private-evidence-value</p>")
    (folder / "stdout.log").write_text("AssertionError: private-evidence-value")
    (folder / "data").mkdir()
    (folder / "data" / "result.json").write_text('{"status":"failed"}')
    (api.run_path(key) / "workspace").mkdir()
    (api.run_path(key) / "workspace" / ".env").write_text("not-public")
    files = api.evidence(key)["files"]
    assert len(files) == 2 and not any(".env" in f["path"] for f in files)
    report = next(f for f in files if f["kind"] == "report")
    body, mime, _ = api.evidence_file(key, report["id"], "view.html")
    assert mime == "text/html" and b"private-evidence-value" not in body
    for theme in ("light", "dark"):
        themed = api.evidence_file(key, report["id"], "view.html", theme=theme)[0]
        assert f"localStorage.theme='{theme}-mode'".encode() in themed
        assert b"background:var(--color-canvas-default)" in themed
    assert (
        b"localStorage.theme=" not in api.evidence_file(key, report["id"], "view.html", theme="<script>")[0]
    )
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("report.json", '{"message":"private-evidence-value"}')
    encoded = base64.b64encode(payload.getvalue()).decode()
    (folder / "index.html").write_text(
        "<head></head><script>const enums={token:`passed`};</script>"
        f'<template id="playwrightReportBase64">data:application/zip;base64,{encoded}</template>'
    )
    html = api.evidence_file(key, report["id"], "view.html")[0].decode()
    assert "token:`passed`" in html and "Object.defineProperty(window" in html
    clean = re.search(r"data:application/zip;base64,([^<]+)", html)[1]
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(clean))) as archive:
        assert "private-evidence-value" not in archive.read("report.json").decode()
    log = next(f for f in files if f["kind"] == "log")
    assert "private-evidence-value" not in api.evidence_file(key, log["id"])[0]["text"]
    assert api.evidence_file(key, report["id"], "data/result.json")[0] == b'{"status":"failed"}'
    for asset in ("../run.json", "../../.env", "data/../index.html", "data\\result.json"):
        with pytest.raises(ValueError):
            api.evidence_file(key, report["id"], asset)
    with pytest.raises(FileNotFoundError):
        api.evidence_file(key, "not-a-file-id")
    try:
        (folder / "outside.log").symlink_to(tmp_path / "outside.log")
    except OSError:
        pass
    assert len(api.evidence(key)["files"]) == 2


def test_boundaries_and_interruption(local_api, tmp_path, monkeypatch):
    api = local_api
    with pytest.raises(ValueError):
        api.start({"repo_url": "https://github.com/acme/demo", "app_url": "https://example.com"})
    with pytest.raises(ValueError):
        api.run_path("../escape")
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    pending = api.read(key)["pending"]
    with pytest.raises(RuntimeError):
        api.respond(key, {"request_id": pending["id"], "decision": "skip"})
    path = api.run_path(key) / "run.json"
    view = json.loads(path.read_text())
    view["consumed"].append(pending["id"])
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"secret")
    view["state"]["deliverable_zip"] = str(outside)
    view["state"]["sessions"] = {"hidden": "prompt-secret"}
    monkeypatch.setenv("OPENAI_API_KEY", "private-test-value")
    view["state"]["artifacts"]["sample"] = {"token": "private-test-value"}
    path.write_text(json.dumps(view))
    run = api.read(key)
    assert run["status"] == "interrupted" and not run["actions"]
    assert "private-test-value" not in json.dumps(run) and "prompt-secret" not in json.dumps(run)
    with pytest.raises(ValueError, match="inside"):
        api.download(key, {"kind": ["zip"]})
    with pytest.raises(FileNotFoundError):
        api.download(key, {"kind": ["source"], "index": ["-1"]})
    target = tmp_path / "outside"
    target.mkdir()
    (target / "run.json").write_text("{}")
    try:
        (api.runs / "symlink").symlink_to(target, target_is_directory=True)
    except OSError:
        return  # Windows may require elevated privileges to create symlinks.
    with pytest.raises(FileNotFoundError):
        api.run_path("symlink")


def test_http_requires_token_and_rejects_browser_origin(local_api):
    server = make_server(local_api, "test-authorization-token-32-characters", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(f"{base}/runs")
        assert denied.value.code == 403
        request = Request(
            f"{base}/runs", headers={"Authorization": "Bearer test-authorization-token-32-characters"}
        )
        assert json.load(urlopen(request)) == {"runs": [], "busy": False}
        request.add_header("Origin", "https://evil.example")
        with pytest.raises(HTTPError) as denied:
            urlopen(request)
        assert denied.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_skip_reconnect_and_new_runtime_resume(local_api):
    api = local_api
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    for _ in range(3):
        approve(api, key)
    gate = api.read(key)["pending"]
    assert gate["stage"] == "environment_setup" and gate["kind"] == "tool"
    api.respond(key, {"request_id": gate["id"], "decision": "skip"})
    wait(api)
    assert api.read(key)["pending"]["details"]["artifact"]["status"] == "skipped"
    before = api.read(key)["pending"]["id"]
    api.reconnect(key)
    wait(api)
    assert api.read(key)["pending"]["id"] == before
    assert before not in json.loads((api.run_path(key) / "run.json").read_text())["consumed"]
    # A fresh adapter restores exactly the saved gate rather than replaying an approved install.
    resumed = LocalAPI(api.runs, runtime_factory=ScriptedStageRuntime)
    try:
        assert resumed.read(key)["pending"]["id"] == before
        resumed.respond(key, {"request_id": before, "decision": "stop"})
        wait(resumed)
        assert resumed.read(key)["status"] == "stopped"
        assert not resumed.read(key)["actions"]
    finally:
        resumed.close()


def test_failed_reconnect_discards_old_runtime(local_api):
    api = local_api
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    original = api.read(key)["pending"]["id"]

    def unavailable():
        raise ValueError("Gateway unavailable")

    api.runtime_factory = unavailable
    api.reconnect(key)
    wait(api)
    assert api.host is None
    assert api.read(key)["operation_error"] == "Gateway unavailable"
    api.runtime_factory = ScriptedStageRuntime
    api.reconnect(key)
    wait(api)
    assert not api.read(key)["operation_error"]
    assert api.read(key)["pending"]["id"] == original


def test_prompts_are_saved_snapshotted_and_historical_requests_remain_immutable(local_api):
    api = local_api
    settings = api.prompts()["settings"]
    settings["personas"]["requirements"] = "CUSTOM PERSONA: derive requirements only from evidence."
    settings["repository_prompt"] = "USER REVIEW: test real backend and frontend with disposable data."
    api.save_prompts(settings)
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    api.save_prompts({**settings, "repository_prompt": "New runs only."})
    assert api.prompts(key)["settings"]["repository_prompt"] == settings["repository_prompt"]
    approve(api, key)
    approve(api, key)
    records = api.prompts(key)["records"]
    original = api.prompts(key, records[-1]["id"])
    assert original["persona"].startswith("CUSTOM PERSONA:")
    run_settings = api.prompts(key)["settings"]
    run_settings["overrides"]["requirements"] = original["user_prompt"] + "\nFuture revision guidance."
    api.save_prompts(run_settings, key)
    assert api.prompts(key, records[-1]["id"]) == original
    assert api.prompts(key)["settings"]["overrides"]["requirements"].endswith("Future revision guidance.")
    with pytest.raises(ValueError, match="Invalid prompt record"):
        api.prompts(key, "../private")
    with pytest.raises(ValueError, match="Unknown stage"):
        api.save_prompts({"personas": {"invented": "unsafe"}})


def test_full_prompt_override_is_saved_without_resending_other_large_requests(local_api):
    key = local_api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(local_api)
    source = "full source evidence\n" * 10000
    local_api.save_prompts({"override_key": "test_design", "override_prompt": source}, key)
    local_api.save_prompts({"repository_prompt": "Keep the exact source evidence."}, key)
    assert local_api.prompts(key)["settings"]["overrides"]["test_design"] == source
    local_api.save_prompts({"override_key": "test_design", "override_prompt": None}, key)
    assert "test_design" not in local_api.prompts(key)["settings"]["overrides"]


def test_delete_run_requires_confirmation_idle_and_preserves_recoverable_files(local_api):
    from concurrent.futures import Future

    from filelock import FileLock

    api = local_api
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    path = api.run_path(key)
    (path / "evidence.txt").write_text("preserved report")
    with pytest.raises(ValueError, match="exact run ID"):
        api.delete_run(key, {})
    api.job = Future()
    with pytest.raises(RuntimeError, match="active operation"):
        api.delete_run(key, {"confirm_run_id": key})
    api.job = None
    with FileLock(str(path / "run.lock")):
        with pytest.raises(RuntimeError, match="another process"):
            api.delete_run(key, {"confirm_run_id": key})
    result = api.delete_run(key, {"confirm_run_id": key})
    assert result == {"id": key, "deleted": True, "recoverable": True}
    assert not path.exists() and key not in {row["id"] for row in api.list_runs()["runs"]}
    preserved = next((api.runs.parent / ".deleted-runs").glob(key + "-*"))
    assert (preserved / "evidence.txt").read_text() == "preserved report"
    with pytest.raises(FileNotFoundError):
        api.read(key)

"""Adapter proof: real workflow/subprocesses, scripted model and local clone fixture."""

import base64
import io
import json
import os
import re
import sys
import threading
import time
import zipfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
from test_execution_stage import local_pytest_plan

import maf_test_harness.workflow as wf
from maf_test_harness.repo.git_transport import run_git
from maf_test_harness.ui.api import LocalAPI, make_server
from maf_test_harness.workflow_steps import execution as execution_steps


@pytest.fixture
def local_api(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    monkeypatch.setattr(execution_steps, "build_plan", local_pytest_plan)
    monkeypatch.setattr(execution_steps, "build_environment_plan", local_env_plan)
    api = LocalAPI(tmp_path / "runs", requirements_mode="model", runtime_factory=ScriptedStageRuntime)
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


def test_stalled_clone_has_live_operation_directory_and_can_be_cancelled_then_retried(local_api, monkeypatch):
    api = local_api

    def stalled(url, destination):
        run_git(
            [
                sys.executable,
                "-c",
                "import sys,time;print('Connecting to GitHub',file=sys.stderr,flush=True);time.sleep(30)",
            ],
            destination.parent,
            20,
            os.environ.copy(),
        )

    monkeypatch.setattr(wf, "clone_repository", stalled)
    key = api.start({"repo_url": "git clone https://github.com/acme/demo"})["id"]
    wait(api)
    assert api.read(key)["state"]["repo_url"] == "https://github.com/acme/demo.git"
    api.respond(key, {"request_id": api.read(key)["pending"]["id"], "decision": "approve"})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = api.read(key)
        if run["operation"].get("latest_output") == "Connecting to GitHub":
            break
        time.sleep(0.05)
    else:
        pytest.fail("Clone operation was not exposed while the workflow was running")
    assert run["busy"] and run["pending_consumed"]
    assert run["state"]["active_stage"] == "repo_analysis"
    assert run["run_directory"] == str(api.run_path(key))
    assert any("clone-operations" in f["path"] for f in api.evidence(key)["files"])
    api.cancel_clone(key)
    wait(api)
    cancelled = api.read(key)
    assert cancelled["pending"]["kind"] == "error"
    assert not cancelled["busy"] and "retry" in cancelled["actions"]
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    api.respond(key, {"request_id": cancelled["pending"]["id"], "decision": "retry"})
    wait(api)
    approve(api, key)
    wait(api)
    assert api.read(key)["pending"]["kind"] == "review"
    assert not api.errors


def test_checkout_storage_discovers_both_legacy_locations_without_moving_files(tmp_path):
    runs = tmp_path / "checkout" / ".maf-harness" / "runs"
    legacy = (tmp_path / "checkout" / ".harness_runs", tmp_path / "profile" / ".maf-harness" / "runs")
    for root, key in zip((runs, *legacy), ("newrun", "oldcheckout", "oldprofile"), strict=True):
        path = root / key
        path.mkdir(parents=True)
        (path / "run.json").write_text(
            json.dumps({"status": "stopped", "state": {"repo_url": "https://github.com/acme/demo"}})
        )
    api = LocalAPI(runs, legacy)
    try:
        assert {run["id"] for run in api.list_runs()["runs"]} == {"newrun", "oldcheckout", "oldprofile"}
        assert api.read("oldprofile")["run_directory"] == str(legacy[1] / "oldprofile")
        assert api.run_path("fresh", create=True) == runs / "fresh"
        assert all(
            (root / key / "run.json").is_file()
            for root, key in zip((runs, *legacy), ("newrun", "oldcheckout", "oldprofile"), strict=True)
        )
        assert not (runs / "oldprofile").exists()
    finally:
        api.close()


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
    assert len(cards) == 14 and all(card["valid"] for card in cards.values())
    assert cards["repository-requirements"]["primary_stages"] == ["requirements"]
    assert cards["requirement-analysis"]["primary_stages"] == ["requirements"]
    assert cards["reverse-engineering"]["primary_stages"] == ["repo_analysis"]
    assert cards["reverse-engineering"]["role"] == "Host-static primary"
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
    assert len([f for f in files if f["path"].startswith("executions/")]) == 2 and not any(
        ".env" in f["path"] for f in files
    )
    assert any(f["path"] == "audit.jsonl" and f["kind"] == "log" for f in files)
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
    log = next(f for f in files if f["name"] == "stdout.log")
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
    assert len([f for f in api.evidence(key)["files"] if f["path"].startswith("executions/")]) == 2


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
        response = urlopen(request)
        assert json.load(response) == {"runs": [], "busy": False}
        assert response.headers.get("Access-Control-Allow-Origin") is None  # the UI proxy is same-origin
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
    for _ in range(6):
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
    resumed = LocalAPI(api.runs, requirements_mode="model", runtime_factory=ScriptedStageRuntime)
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


def test_repository_knowledge_is_bounded_and_preserved(local_api):
    import pytest

    with pytest.raises(ValueError, match="Repository knowledge"):
        local_api.start({"repo_url": "https://github.com/acme/demo", "repo_knowledge": "x" * 32001})
    key = local_api.start(
        {"repo_url": "https://github.com/acme/demo", "repo_knowledge": "Use Python 3.11; see README."}
    )["id"]
    wait(local_api)
    assert local_api.read(key)["state"]["repo_knowledge"] == "Use Python 3.11; see README."


def test_rerun_step_preserves_evidence_and_resets_downstream(local_api):
    api = local_api
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    for _ in range(10):
        approve(api, key)
    assert api.read(key)["status"] == "complete"
    api.rerun(key, {"stage": "requirements", "request_id": None})
    wait(api)
    assert not api.errors
    run = api.read(key)
    assert run["pending"]["stage"] == "requirements"
    assert list(run["state"]["approved"]) == ["repo_analysis"]
    assert "test_execution" not in run["state"]["artifacts"]
    archives = list((api.run_path(key) / "attempts").glob("*/run.json"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text())["status"] == "complete"
    with pytest.raises(ValueError, match="preceding"):
        api.rerun(key, {"stage": "test_execution", "request_id": run["pending"]["id"]})
    with pytest.raises(RuntimeError, match="changed"):
        api.rerun(key, {"stage": "requirements", "request_id": "stale"})
    # Recreating the host must resume the new checkpoint graph, not the old completed cycle.
    api.reconnect(key)
    wait(api)
    approve(api, key)
    assert api.read(key)["pending"]["stage"] == "test_planning"
    for _ in range(10):
        if api.read(key)["status"] == "complete":
            break
        approve(api, key)
    assert api.read(key)["status"] == "complete"
    api.rerun(key, {"stage": "test_execution", "request_id": None})
    wait(api)
    assert not api.errors
    assert api.read(key)["pending"]["stage"] == "test_execution"
    assert api.read(key)["pending"]["kind"] == "tool"


@pytest.mark.parametrize(
    "stage",
    [
        "repo_analysis",
        "requirements",
        "test_planning",
        "test_design",
        "test_generation",
        "environment_setup",
        "test_execution",
    ],
)
def test_each_workflow_step_can_be_rerun(local_api, stage):
    api = local_api
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    for _ in range(10):
        approve(api, key)
    saved = api.read(key)
    api.rerun(key, {"stage": stage, "request_id": None})
    wait(api)
    assert not api.errors
    run = api.read(key)
    assert run["pending"]["stage"] == stage
    order = saved["state"]["stages"]
    assert set(run["state"]["approved"]) == set(order[: order.index(stage)])
    assert list((api.run_path(key) / "attempts").glob("*/run.json"))
    if stage == "repo_analysis":
        assert list((api.run_path(key) / "attempts").glob("*/repo"))


def test_runtime_start_failure_is_persisted_and_visible_in_logs(local_api):
    api = local_api

    def unavailable():
        raise RuntimeError("Gateway unavailable")

    api.runtime_factory = unavailable
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    run = api.read(key)
    assert run["status"] == "interrupted"
    assert run["operation_error"] == "Gateway unavailable"
    log = (api.run_path(key) / "workflow.log").read_text(encoding="utf-8")
    assert "starting repository workflow" in log and "Gateway unavailable" in log
    assert any(item["path"] == "workflow.log" for item in api.evidence(key)["files"])


def test_stop_active_model_preserves_analysis_and_allows_another_run(local_api):
    import asyncio

    entered = threading.Event()

    class Slow(ScriptedStageRuntime):
        async def run_stage(self, **kwargs):
            entered.set()
            await asyncio.sleep(30)

    api = local_api
    api.runtime_factory = Slow
    key = api.start({"repo_url": "https://github.com/acme/slow"})["id"]
    wait(api)
    approve(api, key)
    gate = api.read(key)["pending"]
    api.respond(key, {"request_id": gate["id"], "decision": "approve"})
    assert entered.wait(10)
    with pytest.raises(RuntimeError, match="No active operation"):
        other = api.runs / "other"
        other.mkdir()
        (other / "run.json").write_text('{"status":"new","state":{}}')
        api.stop_run("other")
    api.stop_run(key)
    wait(api)
    run = api.read(key)
    assert run["status"] == "stopped" and run["pending"] is None
    assert "repo_analysis" in run["state"]["artifacts"]
    assert "test_generation" not in run["state"]["artifacts"]
    assert "run_stopped_by_user" in (api.run_path(key) / "audit.jsonl").read_text()
    api.start({"repo_url": "https://github.com/acme/another"})
    wait(api)


def test_a_running_run_without_a_live_job_reads_as_interrupted(local_api):
    import json as _json

    run = local_api.run_path("orphan01", create=True)
    run.mkdir(parents=True, exist_ok=True)
    (run / "run.json").write_text(
        _json.dumps({"status": "running", "state": {"stages": ["repo_analysis"]}, "pending": None}),
        encoding="utf-8",
    )
    view = local_api.read("orphan01")
    assert view["status"] == "interrupted" and view["busy"] is False


def test_trace_archive_is_served_only_as_a_download(local_api):
    api = local_api
    key = api.start({"repo_url": "https://github.com/acme/demo"})["id"]
    wait(api)
    folder = api.run_path(key) / "browser-reports" / "playwright-report"
    folder.mkdir(parents=True)
    (folder / "trace.zip").write_bytes(b"PK trace")
    trace = next(f for f in api.evidence(key)["files"] if f["name"] == "trace.zip")
    with pytest.raises(ValueError, match="only as downloads"):
        api.evidence_file(key, trace["id"])
    assert api.evidence_file(key, trace["id"], download=True)[0] == b"PK trace"


def test_public_keeps_empty_and_numeric_values_under_credential_keys():
    from maf_test_harness.ui.presentation import public

    assert public({"api_key": None}) == {"api_key": None}
    assert public({"token_limit": "12"}) == {"token_limit": "12"}
    assert public({"token_limit": "lots"}) == {"token_limit": "[REDACTED]"}


def test_public_redacts_credential_keys_and_basic_auth_but_keeps_counters():
    from maf_test_harness.ui.presentation import public

    basic = "Basic dXNlcjphYmNkZWZnaGlqa2xtbm9w"
    assert public(f"Authorization: {basic}") == "Authorization: Basic [REDACTED]"
    assert public(
        {"jira_token": "abc123xyz", "Authorization": basic, "input_tokens": 12, "secrets": {"api_key": True}}
    ) == {
        "jira_token": "[REDACTED]",
        "Authorization": "[REDACTED]",
        "input_tokens": 12,
        "secrets": {"api_key": True},
    }


def test_run_listing_reads_run_files_without_holding_the_api_lock(local_api):
    api = local_api
    api.start({"repo_url": "https://github.com/acme/demo"})
    wait(api)
    lock_was_free = []

    def other_thread_can_lock():
        if api.lock.acquire(timeout=2):
            api.lock.release()
            return True
        return False

    original = api._view

    def view_while_probing(key):
        probe = threading.Thread(target=lambda: lock_was_free.append(other_thread_can_lock()))
        probe.start()
        probe.join()
        return original(key)

    api._view = view_while_probing
    try:
        assert api.list_runs()["runs"] and lock_was_free == [True]
    finally:
        del api._view


def test_failed_start_removes_the_half_created_run(local_api, monkeypatch):
    import maf_test_harness.ui.api as api_module

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(api_module, "atomic_json", fail)
    before = set(local_api.runs.iterdir())
    with pytest.raises(OSError, match="disk full"):
        local_api.start({"repo_url": "https://github.com/acme/demo"})
    assert set(local_api.runs.iterdir()) == before and local_api.job is None


def test_deleting_a_legacy_run_keeps_its_trash_inside_this_instance(tmp_path):
    legacy = tmp_path / "other" / "legacy"
    (legacy / "oldrun").mkdir(parents=True)
    (legacy / "oldrun" / "run.json").write_text(json.dumps({"status": "complete", "state": {}}))
    api = LocalAPI(tmp_path / "runs", legacy)
    try:
        api.delete_run("oldrun", {"confirm_run_id": "oldrun"})
        assert not (legacy / "oldrun").exists()
        assert list((tmp_path / ".deleted-runs").glob("oldrun-*"))
        assert not (legacy.parent / ".deleted-runs").exists()
    finally:
        api.close()


def test_http_model_test_route_passes_the_typed_values(local_api, monkeypatch):
    # The UI sends the typed (unsaved) settings; the route must forward the body, not test the saved ones.
    seen = {}
    monkeypatch.setattr(
        local_api, "test_model_settings", lambda body=None: seen.update(body=body) or {"ok": True}
    )
    server = make_server(local_api, "test-authorization-token-32-characters", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        typed = {"base_url": "https://gateway.example/v1", "model": "m"}
        request = Request(
            f"http://127.0.0.1:{server.server_port}/settings/model/test",
            data=json.dumps(typed).encode(),
            headers={
                "Authorization": "Bearer test-authorization-token-32-characters",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        assert json.load(urlopen(request)) == {"ok": True}
        assert seen["body"] == typed
    finally:
        server.shutdown()
        server.server_close()

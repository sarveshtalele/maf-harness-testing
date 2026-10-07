"""Run names (default, validation, rename, busy) and traceable generated-test headers."""

import json

import pytest
from test_ui_api import local_api, wait  # noqa: F401

from maf_test_harness.envs.runners import map_results_to_cases
from maf_test_harness.skills.contracts import (
    case_marker_meta,
    ensure_generated_case_markers,
    validate_generated_files,
)

URL = "https://github.com/acme/demo"


def test_default_custom_validation_rename_and_busy(local_api):  # noqa: F811
    api = local_api
    key = api.start({"repo_url": URL})["id"]
    wait(api)
    name = api.read(key)["state"]["run_name"]
    assert name.startswith("acme/demo · ") and len(name) == len("acme/demo · 2026-01-01 00:00")
    assert api.list_runs()["runs"][0]["run_name"] == name
    for bad in ("x" * 81, "a\nb", 5):
        with pytest.raises(ValueError):
            api.start({"repo_url": URL, "run_name": bad})
    assert api.rename_run(key, {"run_name": "  Smoke  "})["run_name"] == "Smoke"
    assert api.read(key)["state"]["run_name"] == "Smoke"
    events = [json.loads(x) for x in (api.run_path(key) / "audit.jsonl").read_text().splitlines()]
    assert any(e["event"] == "run_renamed" and e["run_id"] == key for e in events)
    assert api.rename_run(key, {"run_name": ""})["run_name"].startswith("acme/demo · ")
    api.job = type("J", (), {"done": lambda self: False})()  # simulate a running job
    with pytest.raises(RuntimeError):
        api.rename_run(key, {"run_name": "nope"})
    api.job = None


def test_marker_header_has_run_req_title_and_parsers_still_map():
    case = {
        "id": "TC-1",
        "title": "Rejects\nempty email",
        "traceability_links": [{"type": "requirement", "id": "REQ-1"}],
    }
    meta = {"TC-1": case_marker_meta(case, "abc123", "Smoke run")}
    for path, prefix in (("tests/test_a.py", "#"), ("src/test/java/ATest.java", "//")):
        payload = {"files": [{"path": path, "content": "x\n", "case_ids": ["TC-1"]}]}
        out = ensure_generated_case_markers(payload, meta)
        header = out["files"][0]["content"].splitlines()[0]
        assert (
            header
            == f"{prefix} CASE_ID: TC-1 | RUN: abc123 (Smoke run) | REQ: REQ-1 | TITLE: Rejects empty email"
        )
    py = {
        "files": [
            {
                "path": "tests/test_a.py",
                "content": "def test_a():\n    assert actual_value() == 1\n",
                "case_ids": ["TC-1"],
            }
        ]
    }
    py = ensure_generated_case_markers(py, meta)
    assert validate_generated_files(py, {"TC-1"})["uncovered_case_ids"] == []
    mapped = map_results_to_cases(
        [{"name": "test_a", "classname": "tests.test_a", "file": "tests/test_a.py"}], py["files"]
    )
    assert mapped[0]["case_id"] == "TC-1"

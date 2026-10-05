import json
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from maf_test_harness.storage import read_json
from maf_test_harness.tools.python_contracts import check_fixture_contracts


def test_windows_state_read_retries_sharing_error_and_decodes_utf8(tmp_path, monkeypatch):
    path = tmp_path / "run.json"
    path.write_text('{"status":"waiting","name":"é"}', encoding="utf-8")
    original = type(path).read_text
    calls = []

    def locked_once(source, **kwargs):
        calls.append(source)
        if len(calls) < 3:
            error = PermissionError(13, "Permission denied", str(source))
            error.winerror = 5
            raise error
        return original(source, **kwargs)

    monkeypatch.setattr(type(path), "read_text", locked_once)
    assert read_json(path) == {"status": "waiting", "name": "é"}
    assert len(calls) == 3


def test_corrupt_json_is_not_retried_or_reported_as_a_lock(tmp_path):
    path = tmp_path / "run.json"
    path.write_text("broken")
    with pytest.raises(json.JSONDecodeError):
        read_json(path)


SOURCE = """from dataclasses import dataclass, field
@dataclass
class FileIndex:
    path: str
    language: str
    module_type: str
    symbols: list = field(default_factory=list)
"""


@pytest.mark.parametrize(
    "fixture,missing",
    [
        ('FileIndex(path="a.py", module_type="api", content="")', "content"),
        ('FileIndex(path="a.py", module_type="api")', "language"),
    ],
)
def test_source_contract_rejects_invented_dataclass_fixtures(fixture, missing):
    ctx = SimpleNamespace(source_files=["scripts/engine/codebase_indexer.py"], read=lambda _: SOURCE)
    with pytest.raises(ValueError, match=missing):
        check_fixture_contracts(
            "from scripts.engine.codebase_indexer import FileIndex\n" + fixture, "test.py", ctx
        )


def test_correct_source_fixture_and_unpacking_are_allowed():
    ctx = SimpleNamespace(source_files=["scripts/engine/codebase_indexer.py"], read=lambda _: SOURCE)
    check_fixture_contracts(
        'from scripts.engine.codebase_indexer import FileIndex\nFileIndex("a.py", "python", "api")\nFileIndex(**dynamic)',
        "test.py",
        ctx,
    )


def test_unittest_mock_requires_explicit_import():
    ctx = SimpleNamespace(source_files=[], read=lambda _: None)
    with pytest.raises(ValueError, match="explicitly import"):
        check_fixture_contracts("import unittest\nunittest.mock.patch('x')", "test.py", ctx)
    check_fixture_contracts("import unittest.mock\nunittest.mock.patch('x')", "test.py", ctx)


def test_finished_permission_failure_cannot_leave_api_busy(tmp_path, monkeypatch):
    from maf_test_harness.ui import api as api_module

    api = api_module.LocalAPI(tmp_path / "runs")
    run = api.runs / "lockedrun"
    run.mkdir()
    (run / "run.json").write_text('{"status":"working"}')
    api.job_run = "lockedrun"
    api.job = Future()
    api.job.set_exception(PermissionError(13, "Permission denied"))

    def locked(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(api_module, "read_json", locked)
    try:
        assert api.busy() is False
        assert "State file is inaccessible" in api.errors["lockedrun"]
    finally:
        api.close()

import json
import sys

import pytest

from maf_test_harness.envs import execution


def test_command_result_records_failure_and_sanitizes_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    report = execution.run_command(
        [sys.executable, "-c", 'import os,sys; print(os.getenv("OPENAI_API_KEY", "absent")); sys.exit(3)'],
        tmp_path,
        tmp_path / "result",
    )
    assert report["status"] == "failed"
    assert report["exit_code"] == 3
    assert (tmp_path / "result/stdout.log").read_text().strip() == "absent"
    assert json.loads((tmp_path / "result/result.json").read_text()) == report


def test_timeout_is_not_success(tmp_path):
    report = execution.run_command(
        [sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, tmp_path / "result", timeout=0.1
    )
    assert report["status"] == "timeout"


def test_junit_zero_or_skipped_tests_never_pass(tmp_path):
    xml = tmp_path / "report.xml"
    xml.write_text(
        '<testsuites><testsuite tests="1"><testcase name="x"><skipped/></testcase></testsuite></testsuites>'
    )
    summary = execution.read_junit([xml])
    assert summary == {"tests": 1, "passed": 0, "failed": 0, "errors": 0, "skipped": 1}
    report = execution.run_command(
        [sys.executable, "-c", 'print("no tests")'], tmp_path, tmp_path / "result", junit_glob="fresh/*.xml"
    )
    assert report["status"] == "no_test_evidence"


def test_generated_files_never_overwrite_repository_files_or_escape_the_workspace(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test_existing.py").write_text("original")
    workspace = execution.copy_repo(repo, tmp_path / "workspace")
    with pytest.raises(ValueError, match="overwrite"):
        execution.write_generated(workspace, [{"path": "test_existing.py", "content": "x"}], repo=repo)
    with pytest.raises(ValueError, match="escapes"):
        execution.write_generated(workspace, [{"path": "../evil.py", "content": "x"}], repo=repo)
    assert (repo / "test_existing.py").read_text() == "original"


def test_workspace_copy_skips_vcs_dependency_dirs_and_secrets_but_keeps_source(tmp_path):
    repo = tmp_path / "repo"
    for rel in (
        ".git/config",
        "node_modules/x/index.js",
        ".env",
        "bin/tool.sh",
        "vendor/mod.go",
        "src/app.py",
    ):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("x")
    copied = {
        p.relative_to(tmp_path / "ws").as_posix()
        for p in execution.copy_repo(repo, tmp_path / "ws").rglob("*")
        if p.is_file()
    }
    assert copied == {"bin/tool.sh", "vendor/mod.go", "src/app.py"}
    with pytest.raises(ValueError, match="new directory"):
        execution.copy_repo(repo, tmp_path / "ws")


def test_regenerated_files_replace_the_previous_attempt_and_run_from_source_not_stale_bytecode(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("def add(a, b):\n    return a + b\n")
    workspace = execution.copy_repo(repo, tmp_path / "workspace")
    run = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "test_add.py", "--junitxml=result.xml"]
    outcomes = []
    previous: list[str] = []
    for index, expected in enumerate(("5", "4"), 1):  # same file size, written within one second
        previous = execution.write_generated(
            workspace,
            [
                {
                    "path": "test_add.py",
                    "content": f"from service import add\n\n\ndef test_add():\n    assert add(2, 3) == {expected}\n",
                }
            ],
            previous=previous,
        )
        report = execution.run_command(
            run,
            workspace,
            tmp_path / f"logs{index}",
            junit_glob="result.xml",
            env={"PYTHONPATH": str(workspace)},
        )
        outcomes.append(report["status"])
    assert outcomes == ["passed", "failed"]
    assert not (repo / "test_add.py").exists()


def test_run_stop_kills_command_and_preserves_cancelled_result(tmp_path):
    import asyncio
    import time

    from maf_test_harness.ui.runtime_service import RuntimeService

    service = RuntimeService()
    out = tmp_path / "stopped"

    async def run():
        return await asyncio.to_thread(
            execution.run_command,
            [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(30)"],
            tmp_path,
            out,
        )

    service.submit(run())
    try:
        deadline = time.monotonic() + 10
        while not (out / "stdout.log").exists() or not (out / "stdout.log").read_text():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        service.stop_active(lambda: None).result(10)
        while not (out / "result.json").exists():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert json.loads((out / "result.json").read_text())["status"] == "cancelled"
    finally:
        service.close()

"""Real process proof: output stays live and stalled clone process trees terminate."""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from maf_test_harness.repo.analyzer import RepoInputError, validate_github_url
from maf_test_harness.repo.git_transport import read_operation, run_git
from maf_test_harness.storage import atomic_json


def test_timeout_kills_descendants_without_waiting_for_inherited_output(tmp_path):
    heartbeat = tmp_path / "heartbeat.txt"
    child = "import time,sys;from pathlib import Path;p=Path(sys.argv[1]);\nfor i in range(300):\n p.write_text(str(i));time.sleep(.05)"
    parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],stdout=sys.stdout,stderr=sys.stderr);print('Git connection stalled',file=sys.stderr,flush=True);time.sleep(30)"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_git([sys.executable, "-c", parent, child, str(heartbeat)], tmp_path, 2, os.environ.copy())
    assert time.monotonic() - started < 15
    assert read_operation(tmp_path)["status"] == "timeout"
    assert "Git connection stalled" in read_operation(tmp_path)["latest_output"]
    assert heartbeat.exists()
    value = heartbeat.read_text()
    time.sleep(0.3)
    assert heartbeat.read_text() == value, "A descendant survived clone timeout"


def test_live_progress_and_explicit_cancel_keep_output(tmp_path):
    script = "import sys,time;print('Receiving objects: 12%',file=sys.stderr,flush=True);time.sleep(30)"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_git, [sys.executable, "-c", script], tmp_path, 20, os.environ.copy())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            operation = read_operation(tmp_path)
            if "12%" in operation.get("latest_output", ""):
                break
            time.sleep(0.05)
        else:
            pytest.fail("Live output was not visible while the process was running")
        assert not future.done()
        atomic_json(tmp_path / "clone_cancel.json", {"operation_id": operation["id"]})
        with pytest.raises(InterruptedError):
            future.result(timeout=15)
    assert read_operation(tmp_path)["status"] == "cancelled"
    assert "12%" in read_operation(tmp_path)["latest_output"]


def test_clone_command_is_parsed_as_url_not_executed():
    assert (
        validate_github_url("git clone https://github.com/agoncal/agoncal-application-petstore-ee7.git")[0]
        == "https://github.com/agoncal/agoncal-application-petstore-ee7.git"
    )
    for value in (
        "git clone https://github.com/acme/repo && whoami",
        "git clone --depth 1 https://github.com/acme/repo",
    ):
        with pytest.raises(RepoInputError):
            validate_github_url(value)

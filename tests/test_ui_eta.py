import json
from pathlib import Path

from maf_test_harness.ui.eta import run_eta

STAGES = ["repo_analysis", "test_design", "test_generation"]
T0 = 1767225600  # 2026-01-01T00:00:00Z


def _jl(path: Path, rows, extra=""):
    path.write_text("\n".join(json.dumps(r) for r in rows) + extra, encoding="utf-8")


def _view(approved, active):
    return {"state": {"stages": STAGES, "approved": dict.fromkeys(approved, {}), "active_stage": active}}


def _at(s):
    return f"2026-01-01T00:{s // 60:02d}:{s % 60:02d}+00:00"


def _run(root: Path, name="r1", batches=4, total=10, done_stages=("repo_analysis",)):
    d = root / name
    d.mkdir(parents=True)
    audit = [{"event": "requested", "at": _at(0)}]
    if "repo_analysis" in done_stages:
        audit.append({"event": "artifact_approved", "at": _at(10), "stage": "repo_analysis"})
    audit += [
        {
            "event": "design_batch_accepted",
            "at": _at(10 + 40 * i),
            "stage": "test_design",
            "batch": i,
            "total_batches": total,
        }
        for i in range(1, batches + 1)
    ]
    _jl(d / "audit.jsonl", audit, "\nnot json{")
    trace = [{"kind": "chat_call", "stage": "test_design", "duration_ms": 40000} for _ in range(batches)]
    _jl(d / "trace.jsonl", trace + [{"kind": "tool_call", "stage": "repo_analysis", "duration_ms": 5000}])
    return d


def test_running_mid_design_with_corrupt_lines(tmp_path):
    d = _run(tmp_path)
    out = run_eta(d, _view(["repo_analysis"], "test_design"), True, now=T0 + 100)
    design = out["stages"][1]
    assert design["status"] == "running"
    assert design["batch"] == {"done": 4, "total": 10}
    assert design["avg_batch_s"] == 40.0
    assert design["eta_s"] == 240
    assert design["elapsed_s"] == 90
    assert out["stages"][0]["status"] == "done" and out["stages"][0]["elapsed_s"] == 10
    assert out["stages"][2]["status"] == "pending"


def test_finished_stage_waiting_and_history_estimate(tmp_path):
    old = _run(tmp_path, "old", batches=10, done_stages=("repo_analysis", "test_design"))
    (old / "run.json").write_text(
        json.dumps({"state": {"approved": {"repo_analysis": {}, "test_design": {}}}}), encoding="utf-8"
    )
    d = _run(tmp_path, "cur", batches=10)
    out = run_eta(d, _view(["repo_analysis"], "test_design"), False)
    assert "eta_s" not in out["stages"][1]  # waiting, not running
    assert out["stages"][2].get("est_s") is None  # no history for test_generation
    out = run_eta(d, _view([], "repo_analysis"), True)
    assert out["stages"][1]["est_s"] == 400


def test_no_history_and_missing_files(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    out = run_eta(d, _view([], "repo_analysis"), True)
    assert [s["status"] for s in out["stages"]] == ["running", "pending", "pending"]
    assert out["stages"][0]["batch"] is None and out["stages"][1].get("est_s") is None
    assert run_eta(d, {}, False) == {"stages": []}

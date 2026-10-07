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
    assert out["stages"][2]["source"] == "default"  # no history for test_generation
    out = run_eta(d, _view([], "repo_analysis"), True)
    assert out["stages"][1]["source"] == "history" and out["stages"][1]["predicted_s"] == 400


def test_no_history_and_missing_files(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    out = run_eta(d, _view([], "repo_analysis"), True)
    assert [s["status"] for s in out["stages"]] == ["running", "pending", "pending"]
    assert out["stages"][0]["batch"] is None and out["stages"][1]["source"] == "default"
    assert run_eta(d, {}, False)["stages"] == []


def _gen_run(root: Path, gate=0):
    d = root / "g"
    d.mkdir()
    cases = [{"id": f"C{i}"} for i in range(30)]
    audit = [
        {"event": "requested", "at": _at(0), "request_id": "a", "stage": "repo_analysis"},
        {"event": "human_response", "at": _at(gate), "request_id": "a", "stage": "repo_analysis"},
        {"event": "artifact_approved", "at": _at(gate + 10), "stage": "repo_analysis"},
        {"event": "generation_attempt_saved", "at": _at(gate + 50), "batch": 2},
        {"event": "test_reviewer_result", "at": _at(gate + 60), "stage": "test_generation", "batch": 3},
    ]
    _jl(d / "audit.jsonl", audit)
    _jl(d / "trace.jsonl", [{"kind": "chat_call", "stage": "test_generation", "duration_ms": 30000}] * 3)
    view = {
        "state": {
            "stages": ["repo_analysis", "test_generation", "test_execution"],
            "approved": {"repo_analysis": {}},
            "active_stage": "test_generation",
            "artifacts": {"test_design": {"positive_tests": cases[:20], "negative_tests": cases[20:]}},
        }
    }
    return d, view


def test_defaults_predict_every_stage(tmp_path):
    d = tmp_path / "e"
    d.mkdir()
    out = run_eta(d, _view([], "repo_analysis"), True, now=T0)
    assert all(s["predicted_s"] > 0 and s["remaining_s"] > 0 for s in out["stages"])
    assert out["total_remaining_s"] == sum(s["remaining_s"] for s in out["stages"])
    assert out["finish_estimate"].startswith("2026-01-01T")


def test_generation_progress_from_audit_and_measured_average(tmp_path):
    from maf_test_harness.workflow_support import TEST_GENERATION_BATCH_SIZE as N

    d, view = _gen_run(tmp_path)
    gen = run_eta(d, view, True, now=T0 + 100)["stages"][1]
    total = -(-30 // N)
    assert gen["batch"] == {"done": 3, "total": total}
    assert gen["avg_batch_s"] == 30.0 and gen["source"] == "measured"  # beats the 45s default
    assert gen["remaining_s"] == (total - 3) * 30


def test_gate_wait_excluded(tmp_path):
    d, view = _gen_run(tmp_path, gate=3000)  # human sat on the first gate for 50 min
    first = run_eta(d, view, True, now=T0 + 3100)["stages"][0]
    assert first["wall_s"] == 3010 and first["elapsed_s"] == 10


def test_interrupted_has_no_countdown(tmp_path):
    d, view = _gen_run(tmp_path)
    out = run_eta(d, view, False, now=T0 + 99999, interrupted=True)
    gen = out["stages"][1]
    assert gen["status"] == "interrupted" and gen["remaining_s"] is None
    assert gen["elapsed_s"] < 100  # idle downtime not counted
    assert out["total_remaining_s"] is None and out["finish_estimate"] is None

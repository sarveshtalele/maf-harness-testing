"""Deterministic per-stage progress and ETA derived from a run's audit.jsonl and trace.jsonl."""

import json
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from statistics import median

from ..workflow_steps.design import requirement_batches
from ..workflow_support import TEST_GENERATION_BATCH_SIZE

_HISTORY_RUNS = 40
# seconds per work unit (design/generation: per batch; others: per stage) when nothing was measured
DEFAULTS = {
    "repo_analysis": 15,
    "requirements": 10,
    "test_planning": 60,
    "test_design": 45,
    "test_generation": 45,
    "environment_setup": 180,
    "test_execution": 120,
}
_GEN_EVENTS = {"generation_attempt_saved", "test_reviewer_result", "test_generation_batch_failed"}
_cache: dict[Path, tuple[tuple, dict]] = {}


def _rows(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _ts(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def _busy(run_dir: Path) -> dict[str, float]:
    """Seconds spent in model/tool calls per stage (chat_call+tool_call; model_run wraps chat calls)."""
    out: dict[str, float] = {}
    for row in _rows(run_dir / "trace.jsonl"):
        if row.get("kind") in {"chat_call", "tool_call"} and isinstance(row.get("duration_ms"), int | float):
            out[row.get("stage")] = out.get(row.get("stage"), 0) + row["duration_ms"] / 1000
    return out


def _units(audit: list[dict]) -> dict[str, int]:
    """Work units completed so far per batched stage, from audit events."""
    out = {"test_design": 0, "test_generation": 0}
    for r in audit:
        if not isinstance(r.get("batch"), int):
            continue
        if r.get("event") == "design_batch_accepted":
            out["test_design"] = max(out["test_design"], r["batch"])
        elif r.get("event") in _GEN_EVENTS:
            out["test_generation"] = max(out["test_generation"], r["batch"])
    return out


def _gate_waits(audit: list[dict], now: float) -> dict[str, list[tuple[float, float]]]:
    """Human-gate intervals per stage: `requested` until its human_response / artifact_approved."""
    open_, waits = {}, {}
    for r in audit:
        at = _ts(r.get("at"))
        if at is None:
            continue
        if r.get("event") == "requested":
            open_[r.get("request_id")] = (r.get("stage"), at)
        elif r.get("event") == "human_response" and r.get("request_id") in open_:
            stage, begin = open_.pop(r["request_id"])
            waits.setdefault(stage, []).append((begin, at))
        elif r.get("event") == "artifact_approved":
            for rid, (stage, begin) in list(open_.items()):
                if stage == r.get("stage"):
                    waits.setdefault(stage, []).append((begin, at))
                    del open_[rid]
    for stage, begin in open_.values():
        waits.setdefault(stage, []).append((begin, now))
    return waits


def _history(run_dir: Path) -> dict[str, float]:
    """Median busy seconds per work unit over sibling runs' approved stages (cached per process)."""
    try:
        files = [p for p in run_dir.parent.glob("*/run.json") if p.parent != run_dir]
        files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:_HISTORY_RUNS]
    except OSError:
        return {}
    samples: dict[str, list[float]] = {}
    for file in files:
        try:
            trace = file.parent / "trace.jsonl"
            stat = (file.stat().st_mtime, trace.stat().st_mtime if trace.exists() else 0)
        except OSError:
            continue
        hit = _cache.get(file.parent)
        if not hit or hit[0] != stat:
            try:
                approved = set(
                    json.loads(file.read_text(encoding="utf-8")).get("state", {}).get("approved", {})
                )
            except (OSError, ValueError, AttributeError):
                approved = set()
            units = _units(_rows(file.parent / "audit.jsonl"))
            hit = _cache[file.parent] = (
                stat,
                {s: v / max(units.get(s, 0), 1) for s, v in _busy(file.parent).items() if s in approved},
            )
        for stage, seconds in hit[1].items():
            samples.setdefault(stage, []).append(seconds)
    return {stage: median(values) for stage, values in samples.items()}


def _design_total(audit: list[dict], artifacts: dict) -> int:
    totals = [r.get("total_batches") for r in audit if r.get("event") == "design_batch_accepted"]
    if any(totals):
        return int(max(t for t in totals if t))
    try:
        return sum(1 for _ in requirement_batches(artifacts["requirements"]))
    except (KeyError, TypeError, IndexError, AttributeError):
        return 1


def _case_count(run_dir: Path, artifacts: dict) -> int:
    design = artifacts.get("test_design")
    if not isinstance(design, dict):
        files = sorted(run_dir.glob("test_design-*.json"))
        try:
            design = json.loads(files[-1].read_text(encoding="utf-8")) if files else {}
        except (OSError, ValueError):
            design = {}
    cases = design.get("cases")
    if isinstance(cases, list):
        return len(cases)
    return sum(len(v) for k, v in design.items() if k.endswith("_tests") and isinstance(v, list))


def run_eta(
    run_dir: Path, view: dict, running: bool, now: float | None = None, interrupted: bool = False
) -> dict:
    """Per-stage progress and prediction: units x seconds/unit (measured > history > defaults)."""
    now = now or datetime.now(UTC).timestamp()
    state = view.get("state") or {}
    stages = state.get("stages") or []
    approved = state.get("approved") or {}
    active = state.get("active_stage")
    artifacts = state.get("artifacts") or {}
    audit = _rows(run_dir / "audit.jsonl")
    if interrupted:  # nothing ran since the last recorded event; don't count the downtime
        now = max([t for t in (_ts(r.get("at")) for r in audit) if t] or [now])
    busy, history = _busy(run_dir), _history(run_dir)
    approved_at = {r.get("stage"): _ts(r.get("at")) for r in audit if r.get("event") == "artifact_approved"}
    waits, units = _gate_waits(audit, now), _units(audit)
    gate_open = (view.get("pending") or {}).get("kind") == "review"
    files = len((artifacts.get("test_generation") or {}).get("files") or [])
    scale = max(1.0, files / 10)
    first = next((t for t in (_ts(r.get("at")) for r in audit) if t), None)
    totals = {"test_design": _design_total(audit, artifacts) if "test_design" in stages else 0}
    cases = _case_count(run_dir, artifacts) if "test_generation" in stages else 0
    totals["test_generation"] = (
        ceil(cases / TEST_GENERATION_BATCH_SIZE) if cases else units["test_generation"]
    )
    out, start = [], first
    for stage in stages:
        row = {
            "stage": stage,
            "status": "pending",
            "started_at": None,
            "wall_s": None,
            "elapsed_s": None,
            "batch": None,
        }
        total = max(totals.get(stage, 1), units.get(stage, 0), 1)
        done = units.get(stage, 0) if stage in totals else 0
        if stage in approved:
            end = approved_at.get(stage)
            wall = end - start if end and start else None
            lo, hi = (start or 0), (end or now)
            gate = sum(max(0, min(b, hi) - max(a, lo)) for a, b in waits.get(stage, []))
            row.update(status="done", wall_s=round(wall) if wall is not None else None)
            row["elapsed_s"] = round(max(wall - gate, 0)) if wall is not None else None
            row["predicted_s"], row["remaining_s"] = row["elapsed_s"], 0
            start = end or start
            out.append(row)
            continue
        measured = bool(done and busy.get(stage) and stage == active)
        if measured:
            per, source = busy[stage] / done, "measured"
        elif stage in history:
            per, source = history[stage], "history"
        else:
            per, source = (
                DEFAULTS.get(stage, 60) * (scale if stage in {"environment_setup", "test_execution"} else 1),
                "default",
            )
        row.update(batch={"done": done, "total": total} if stage in totals else None, source=source)
        row["predicted_s"] = round(total * per)
        row["remaining_s"] = round(max(total - done, 0) * per)
        if stage == active:
            row["status"] = "interrupted" if interrupted else "running" if running else "waiting"
            row["started_at"] = datetime.fromtimestamp(start, UTC).isoformat() if start else None
            if start:
                row["wall_s"] = round(now - start)
                gate = sum(max(0, min(b, now) - max(a, start)) for a, b in waits.get(stage, []))
                row["elapsed_s"] = round(max(now - start - gate, 0))
            if measured:
                row["avg_batch_s"] = round(per, 1)
            if interrupted:
                row["remaining_s"] = None
                if busy.get(stage):  # wall time since start includes the downtime after the crash/restart
                    row["elapsed_s"] = round(busy[stage])
            elif row["status"] == "waiting" and gate_open:
                row["remaining_s"] = 0
            if row["remaining_s"] is not None and running:
                row["eta_s"] = row["remaining_s"]
        out.append(row)
    live = [r["remaining_s"] for r in out if r["status"] != "done" and r["remaining_s"] is not None]
    result = {
        "stages": out,
        "total_remaining_s": sum(live) if not interrupted else None,
        "finish_estimate": None,
    }
    if running and not interrupted:
        result["finish_estimate"] = datetime.fromtimestamp(now + sum(live), UTC).isoformat()
    return result

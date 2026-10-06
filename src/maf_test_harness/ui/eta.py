"""Deterministic per-stage progress and ETA derived from a run's audit.jsonl and trace.jsonl."""

import json
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from statistics import median

from ..workflow_support import TEST_GENERATION_BATCH_SIZE

_HISTORY_RUNS = 40
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
    """Seconds spent in model/tool calls per stage."""
    out: dict[str, float] = {}
    for row in _rows(run_dir / "trace.jsonl"):
        if row.get("kind") in {"chat_call", "tool_call"} and isinstance(row.get("duration_ms"), int | float):
            out[row.get("stage")] = out.get(row.get("stage"), 0) + row["duration_ms"] / 1000
    return out


def _history(run_dir: Path) -> dict[str, float]:
    """Median busy seconds per stage over sibling runs' approved stages (cached per process)."""
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
            hit = _cache[file.parent] = (stat, {s: v for s, v in _busy(file.parent).items() if s in approved})
        for stage, seconds in hit[1].items():
            samples.setdefault(stage, []).append(seconds)
    return {stage: median(values) for stage, values in samples.items()}


def run_eta(run_dir: Path, view: dict, running: bool, now: float | None = None) -> dict:
    """Return {"stages": [...]} with status, elapsed_s, batch, avg_batch_s, eta_s, est_s per stage."""
    now = now or datetime.now(UTC).timestamp()
    state = view.get("state") or {}
    stages = state.get("stages") or []
    approved = state.get("approved") or {}
    active = state.get("active_stage")
    audit = _rows(run_dir / "audit.jsonl")
    busy, history = _busy(run_dir), _history(run_dir)
    approved_at = {r.get("stage"): _ts(r.get("at")) for r in audit if r.get("event") == "artifact_approved"}
    designed = {r["batch"]: r for r in audit if r.get("event") == "design_batch_accepted" and "batch" in r}
    first = next((t for t in (_ts(r.get("at")) for r in audit) if t), None)
    out, start = [], first
    for stage in stages:
        row = {"stage": stage, "status": "pending", "started_at": None, "elapsed_s": None, "batch": None}
        if stage in approved:
            row["status"] = "done"
            end = approved_at.get(stage)
            row["elapsed_s"] = round(end - start) if end and start else None
            start = end or start
        elif stage == active:
            row["status"] = "running" if running else "waiting"
            row["started_at"] = datetime.fromtimestamp(start, UTC).isoformat() if start else None
            row["elapsed_s"] = round(now - start) if start else None
            done = total = 0
            if stage == "test_design" and designed:
                done = max(designed)
                total = int(designed[done].get("total_batches") or 0)
            elif stage == "test_generation":
                cases = (state.get("artifacts", {}).get("test_design") or {}).get("cases") or []
                total = ceil(len(cases) / TEST_GENERATION_BATCH_SIZE)
                done = len((state.get("accepted_generation_batches") or {}).get("batches") or {})
            if total:
                row["batch"] = {"done": done, "total": total}
                if done and busy.get(stage):
                    row["avg_batch_s"] = round(busy[stage] / done, 1)
                    if running:
                        row["eta_s"] = round(max(total - done, 0) * busy[stage] / done)
        elif stage in history:
            row["est_s"] = round(history[stage])
        out.append(row)
    return {"stages": out}

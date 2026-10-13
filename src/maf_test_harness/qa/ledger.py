"""Confidence ledger: one JSON line per host-computed plan/design/test score in ``<run_dir>/confidence.jsonl``.

Kinds are plan (PLN area), design (case) and test (generated case). Every review pass appends a record, including
repair rounds and re-asks, so the round number counts the scores a subject has received in one batch of one epoch.
``epoch`` is the run's ``ledger_epoch``: a rerun-from-step starts a new epoch, and reports show the latest epoch unless
asked for all of them. Pure helpers plus append/read; old runs without a ledger read as an empty list.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..storage import append_jsonl

LEDGER = "confidence.jsonl"
TEXT_LIMIT = (
    300  # derivation steps and quotes are truncated in the ledger; the full texts stay in prompt records
)
MAX_ENTRIES = 50_000  # entries() streams the file and keeps at most this many (the newest) records

# Rounds are counted in memory per run: rebuilt from the file once, then incremented on every append (no re-read).
_rounds: dict[tuple[str, str, str, str, int], int] = {}
_counted_runs: set[str] = set()
_lock = threading.Lock()


def clip(text: Any, limit: int = TEXT_LIMIT) -> str:
    collapsed = " ".join(str(text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stream(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """(line number, record) for every well-formed JSON object line; torn lines are skipped."""
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield number, record


def entries(run_dir: Path, limit: int = MAX_ENTRIES) -> list[dict[str, Any]]:
    """Ledger records (newest ``limit``) with their 1-based line number (``line``). A missing file is empty."""
    path = run_dir / LEDGER
    if not path.is_file():
        return []
    recent: deque[dict[str, Any]] = deque(maxlen=max(1, limit))
    for number, record in _stream(path):
        recent.append({**record, "line": number})
    return list(recent)


def epoch_of(record: dict[str, Any]) -> int:
    """The record's epoch; records written before epochs existed are epoch 0."""
    try:
        return int(record.get("epoch") or 0)
    except (TypeError, ValueError):
        return 0


def latest_epoch(records: list[dict[str, Any]]) -> int:
    return max((epoch_of(r) for r in records), default=0)


def in_epoch(records: list[dict[str, Any]], epoch: str | int = "latest") -> list[dict[str, Any]]:
    """Records of one epoch: ``"latest"`` (default, the newest epoch), an integer, or ``"all"`` for the history."""
    if epoch == "all":
        return list(records)
    target = latest_epoch(records) if epoch == "latest" else int(epoch)
    return [r for r in records if epoch_of(r) == target]


def _round_key(
    run: str, kind: str, subject_id: Any, batch: Any, epoch: int
) -> tuple[str, str, str, str, int]:
    return (run, str(kind), str(subject_id), json.dumps(batch, sort_keys=True, default=str), epoch)


def _next_round(run_dir: Path, *, kind: str, subject_id: Any, batch: Any, epoch: int) -> int:
    run = str(run_dir.resolve())
    with _lock:
        if run not in _counted_runs:
            path = run_dir / LEDGER
            if path.is_file():
                for _, record in _stream(path):
                    key = _round_key(
                        run,
                        record.get("kind", ""),
                        record.get("subject_id", ""),
                        record.get("batch"),
                        epoch_of(record),
                    )
                    _rounds[key] = _rounds.get(key, 0) + 1
            _counted_runs.add(run)
        key = _round_key(run, kind, subject_id, batch, epoch)
        _rounds[key] = _rounds.get(key, 0) + 1
        return _rounds[key]


def _forget(run_dir: Path) -> None:
    run = str(run_dir.resolve())
    with _lock:
        _counted_runs.discard(run)
        for key in [k for k in _rounds if k[0] == run]:
            del _rounds[key]


def append_score(
    run_dir: Path,
    *,
    kind: str,
    subject_id: str,
    control_ids: list[str],
    batch: Any,
    scored: dict[str, Any],
    threshold: int,
    calls: list[dict[str, Any]],
    gate: str,
    epoch: int = 0,
) -> dict[str, Any]:
    """Append one score. ``scored`` carries scores, points, caps, notes, confidence, verdict, reason, accepted,
    derivations and evidence. ``calls`` are the model calls behind it (``_call_model`` results, newest last). Evidence
    items carry ``verified`` (a quote is verbatim) or ``exists`` (a repository file is present), never both meanings."""
    last = calls[-1] if calls else {}
    record = {
        "kind": kind,
        "subject_id": str(subject_id),
        "control_ids": list(control_ids),
        "batch": batch,
        "round": _next_round(run_dir, kind=kind, subject_id=subject_id, batch=batch, epoch=int(epoch)),
        "epoch": int(epoch),
        "scores": scored["scores"],
        "points": scored["points"],
        "caps": scored["caps"],
        "notes": scored.get("notes", []),
        "confidence": scored["confidence"],
        "threshold": threshold,
        "gate": gate,
        "accepted": bool(scored["accepted"]),
        "verdict": scored["verdict"],
        "reason": clip(scored.get("reason"), 600),
        "derivations": [
            {key: clip(value) if isinstance(value, str) else value for key, value in item.items()}
            for item in scored.get("derivations", [])
        ],
        "evidence": [
            {
                "quote": clip(item.get("quote")),
                **{key: bool(item[key]) for key in ("verified", "exists") if key in item},
            }
            for item in scored.get("evidence", [])
        ],
        "prompt_record_id": last.get("prompt_record_id"),
        "prompt_record_ids": [c.get("prompt_record_id") for c in calls if c.get("prompt_record_id")],
        "prompt_sha256": last.get("prompt_sha256"),
        "persona_override_sha256": last.get("persona_override_sha256"),
        "model": last.get("model"),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    append_jsonl(run_dir / LEDGER, record)
    return record


def append_event(
    run_dir: Path, *, kind: str, subject_id: str, epoch: int, data: dict[str, Any]
) -> dict[str, Any]:
    """Append a non-score record (for example ``execution_triage``). Text values are clipped like score evidence;
    the record carries no scores, so gates and round counts ignore it."""
    record = {
        "kind": kind,
        "subject_id": str(subject_id),
        "epoch": int(epoch),
        **{key: clip(value) if isinstance(value, str) else value for key, value in data.items()},
        "timestamp": datetime.now(UTC).isoformat(),
    }
    append_jsonl(run_dir / LEDGER, record)
    return record


TRIAGE = "triage.jsonl"  # verification triage lives beside the score ledger, so it never pushes scores out of the window


def append_triage(
    run_dir: Path, *, case_id: str, attempt: int, epoch: int, data: dict[str, Any]
) -> dict[str, Any] | None:
    """Append one triage record for (case, attempt). A record already written for that pair is kept, not duplicated;
    returns None in that case."""
    path = run_dir / TRIAGE
    if path.is_file():
        for _, existing in _stream(path):
            if str(existing.get("case_id")) == str(case_id) and int(existing.get("attempt") or 0) == int(
                attempt
            ):
                return None
    record = {
        "kind": "execution_triage",
        "case_id": str(case_id),
        "attempt": int(attempt),
        "epoch": int(epoch),
        **{key: clip(value) if isinstance(value, str) else value for key, value in data.items()},
        "timestamp": datetime.now(UTC).isoformat(),
    }
    append_jsonl(path, record)
    return record


def triage_entries(run_dir: Path) -> list[dict[str, Any]]:
    """Every triage record of the run, oldest first; a missing file is empty."""
    path = run_dir / TRIAGE
    if not path.is_file():
        return []
    return [{**record, "line": number} for number, record in _stream(path)]


def remap_plan_subjects(run_dir: Path, mapping: dict[tuple[Any, str], str]) -> int:
    """Rewrite plan records of a batched plan to the FINAL merged area IDs.

    ``mapping`` maps (batch, batch-local area ID) to the final ID. A plan record whose batch-local area did not survive
    the merge keeps a non-area subject (``batch<n>/<PLN-id>``), so it can never collide with a final area. Returns the
    number of records rewritten; the file is replaced atomically and the line count (report links) is unchanged."""
    path = run_dir / LEDGER
    if not path.is_file():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    rewritten = 0
    out: list[str] = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            out.append(line)  # a torn line stays as it is
            continue
        if not isinstance(record, dict) or record.get("kind") != "plan" or record.get("batch") is None:
            out.append(line)
            continue
        local = str(record.get("local_subject_id") or record.get("subject_id"))
        final = mapping.get((record["batch"], local), f"batch{record['batch']}/{local}")
        if final != record.get("subject_id"):
            record["local_subject_id"] = local
            record["subject_id"] = final
            rewritten += 1
            out.append(json.dumps(record, ensure_ascii=False))
        else:
            out.append(line)
    if rewritten:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text("\n".join(out) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        _forget(run_dir)
    return rewritten


def gate_mode(enforced: bool) -> str:
    return "hard" if enforced else "advisory"


def latest_by_subject(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """The last record per (kind, subject_id); ledger order is time order."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        latest[(record["kind"], record["subject_id"])] = record
    return latest

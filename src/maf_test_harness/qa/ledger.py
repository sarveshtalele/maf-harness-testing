"""Confidence ledger: one JSON line per host-computed score in ``<run_dir>/confidence.jsonl``.

Kinds are plan (PLN area), design (case) and test (generated case). Every review pass appends a record, including
repair rounds and re-asks, so the round number counts the scores a subject has received in one batch. Pure helpers
plus append/read; old runs without a ledger read as an empty list.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..storage import append_jsonl

LEDGER = "confidence.jsonl"
TEXT_LIMIT = (
    300  # derivation steps and quotes are truncated in the ledger; the full texts stay in prompt records
)


def clip(text: Any, limit: int = TEXT_LIMIT) -> str:
    collapsed = " ".join(str(text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def entries(run_dir: Path) -> list[dict[str, Any]]:
    """Every ledger record with its 1-based line number (``line``). A torn line is skipped, a missing file is empty."""
    path = run_dir / LEDGER
    if not path.is_file():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append({**record, "line": number})
    return records


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
) -> dict[str, Any]:
    """Append one score. ``scored`` carries scores, points, caps, notes, confidence, verdict, reason, accepted,
    derivations and evidence. ``calls`` are the model calls behind it (``_call_model`` results, newest last)."""
    # ponytail: re-reads the ledger per append to count rounds; O(n^2) on very large runs, cache a counter per subject if it shows up.
    earlier = sum(
        1
        for entry in entries(run_dir)
        if entry.get("kind") == kind and entry.get("subject_id") == subject_id and entry.get("batch") == batch
    )
    last = calls[-1] if calls else {}
    record = {
        "kind": kind,
        "subject_id": str(subject_id),
        "control_ids": list(control_ids),
        "batch": batch,
        "round": earlier + 1,
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
            {"quote": clip(item.get("quote")), "verified": bool(item.get("verified"))}
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


def gate_mode(enforced: bool) -> str:
    return "hard" if enforced else "advisory"


def latest_by_subject(records: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """The last record per (kind, subject_id); ledger order is time order."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        latest[(record["kind"], record["subject_id"])] = record
    return latest

"""Confidence ledger read API: ``GET runs/<id>/confidence`` pages through the run's score records."""

from __future__ import annotations

from ..qa import ledger
from .presentation import public

KINDS = ("plan", "design", "test")


def _number(query: dict, name: str, default: int, low: int, high: int) -> int:
    raw = (query.get(name) or [""])[0]
    if raw == "":
        return default
    if not raw.isdigit():
        raise ValueError(f"{name} must be a whole number.")
    return max(low, min(high, int(raw)))


class ConfidenceAPI:
    def confidence(self, key: str, query: dict | None = None) -> dict:
        """Ledger records of one run, newest last. Filters: ``kind`` (plan, design, test), ``subject`` (a PLN area, a
        case ID, or a CTL control ID), ``epoch`` (default: the latest epoch; an integer, or ``all`` for the history),
        ``limit`` (1-200) and ``offset``. A run without a ledger has no records."""
        query = query or {}
        kind = (query.get("kind") or [""])[0]
        subject = (query.get("subject") or [""])[0].strip()
        epoch = (query.get("epoch") or ["latest"])[0].strip() or "latest"
        if kind and kind not in KINDS:
            raise ValueError(f"Unknown confidence kind; expected one of {', '.join(KINDS)}.")
        if epoch not in {"latest", "all"} and not epoch.isdigit():
            raise ValueError("epoch must be latest, all or a whole number.")
        limit = _number(query, "limit", 50, 1, 200)
        offset = _number(query, "offset", 0, 0, 10**9)
        records = [
            record
            for record in ledger.in_epoch(ledger.entries(self.run_path(key)), epoch)
            if (not kind or record["kind"] == kind)
            and (not subject or record["subject_id"] == subject or subject in record.get("control_ids", []))
        ]
        return public(
            {
                "ledger": ledger.LEDGER,
                "total": len(records),
                "offset": offset,
                "limit": limit,
                "records": records[offset : offset + limit],
            }
        )

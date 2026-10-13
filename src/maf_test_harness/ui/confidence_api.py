"""Confidence ledger read API: ``GET runs/<id>/confidence`` pages through the run's score records."""

from __future__ import annotations

from ..qa import ledger
from .presentation import public

KINDS = ("plan", "design", "test")  # the gate kinds: the default listing
TRIAGE_KIND = "execution_triage"  # read from triage.jsonl, only when asked for explicitly


def _number(query: dict, name: str, default: int, low: int, high: int) -> int:
    raw = (query.get(name) or [""])[0]
    if raw == "":
        return default
    if not raw.isdigit():
        raise ValueError(f"{name} must be a whole number.")
    return max(low, min(high, int(raw)))


class ConfidenceAPI:
    def confidence(self, key: str, query: dict | None = None) -> dict:
        """Ledger records of one run, newest last. Filters: ``kind`` (plan, design, test, or execution_triage, which
        reads triage.jsonl; the default lists only the gate kinds), ``subject`` (a PLN area, a
        case ID, or a CTL control ID), ``epoch`` (default: the latest epoch; an integer, or ``all`` for the history),
        ``limit`` (1-200) and ``offset``. A run without a ledger has no records."""
        query = query or {}
        kind = (query.get("kind") or [""])[0]
        subject = (query.get("subject") or [""])[0].strip()
        epoch = (query.get("epoch") or ["latest"])[0].strip() or "latest"
        if kind and kind not in (*KINDS, TRIAGE_KIND):
            raise ValueError(f"Unknown confidence kind; expected one of {', '.join((*KINDS, TRIAGE_KIND))}.")
        if epoch not in {"latest", "all"} and not epoch.isdigit():
            raise ValueError("epoch must be latest, all or a whole number.")
        limit = _number(query, "limit", 50, 1, 200)
        offset = _number(query, "offset", 0, 0, 10**9)
        run = self.run_path(key)
        source = (
            ledger.triage_entries(run) if kind == TRIAGE_KIND else ledger.entries(run)
        )  # triage has its own file, so score records never push it out of the window
        records = [
            record
            for record in ledger.in_epoch(source, epoch)
            if (record["kind"] == kind if kind else record["kind"] in KINDS)
            and (
                not subject
                or record.get("subject_id") == subject
                or record.get("case_id") == subject
                or subject in record.get("control_ids", [])
            )
        ]
        return public(
            {
                "ledger": ledger.TRIAGE if kind == TRIAGE_KIND else ledger.LEDGER,
                "total": len(records),
                "offset": offset,
                "limit": limit,
                "records": records[offset : offset + limit],
            }
        )

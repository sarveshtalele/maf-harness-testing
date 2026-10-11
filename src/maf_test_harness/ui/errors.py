"""Collect every run error (generation, stage, build gate, quarantine, gates) into one flat list."""

from __future__ import annotations

import json
import re
from pathlib import Path

CATEGORIES = (
    ("accuracy_gate", r"^Accuracy gate:"),
    ("review_rejected", r"Independent test review"),
    ("import", r"cannot work: the apparent repository|ModuleNotFoundError|No module named|import '"),
    ("syntax", r"source is invalid|was never closed|SyntaxError|NameError"),
    ("truncated", r"EOF while parsing|cut off"),
    ("one_test_per_file", r"exactly one concrete test declaration"),
    ("unknown_case_ids", r"unknown case id|unknown_case_ids|unapproved case"),
    ("schema", r"schema validation"),
    ("build_gate", r"build gate"),
    (
        "gateway",
        r"\[gateway\]|gateway was unreachable|service failed to complete the prompt: "
        r"(?:Connection error|.*Error code: (?:429|50[234]))",
    ),
    ("quarantined", r"quarantined"),
)


def categorize(text: str, default: str = "other") -> str:
    return next((name for name, rx in CATEGORIES if re.search(rx, text, re.I)), default)


def _summary(text: str) -> str:
    first = re.split(r"(?<=[.!?])\s|\n", text.strip(), maxsplit=1)[0]
    return first if len(first) <= 160 else first[:157] + "..."


def _audit(run_dir: Path) -> list[dict]:
    try:
        lines = (run_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def run_errors(run_dir, view) -> dict:
    view = view if isinstance(view, dict) else {}
    arts = (view.get("state") or {}).get("artifacts") or {}
    raw: list[dict] = []

    def add(source, stage, detail, at="", batch=None, case_ids=(), category=None):
        detail = str(detail or "").strip()
        if detail:
            raw.append(
                {
                    "at": str(at or ""),
                    "stage": stage,
                    "batch": batch,
                    "case_ids": [str(c) for c in case_ids or ()],
                    "category": category or categorize(detail),
                    "summary": _summary(detail),
                    "detail": detail,
                    "source": source,
                }
            )

    generation = arts.get("test_generation") or {}
    for f in generation.get("generation_failures") or []:
        add(
            "generation_failures",
            "test_generation",
            f.get("last_error"),
            f.get("at"),
            f.get("batch"),
            f.get("case_ids"),
        )
    for e in _audit(Path(run_dir)):
        ev, at = e.get("event"), e.get("at", "")
        if ev == "test_generation_batch_failed":
            add("audit", "test_generation", e.get("error"), at, e.get("batch"), e.get("case_ids"))
        elif ev == "stage_error":
            add("audit", e.get("stage"), e.get("error"), at)
        elif ev == "build_gate_failed_continuing":
            add("audit", e.get("stage") or "test_execution", e.get("error"), at, category="build_gate")
    execution = arts.get("test_execution") or {}
    execution = execution.get("execution") or execution
    for path in execution.get("quarantined") or []:
        add(
            "execution",
            "test_execution",
            f"Quarantined generated file after build failure: {path}",
            category="quarantined",
        )
    pending = view.get("pending") or {}
    if pending.get("error"):
        add(
            "pending",
            pending.get("stage") or (view.get("state") or {}).get("active_stage"),
            pending["error"],
            pending.get("at"),
        )
    add("operation", None, view.get("error") or view.get("operation_error"))

    seen, items = set(), []
    for i in sorted(raw, key=lambda r: r["at"], reverse=True):
        key = (i["stage"], i["batch"], i["category"], i["summary"])
        if key not in seen:
            seen.add(key)
            items.append({"id": f"e{len(items) + 1}", **i})
    counts: dict[str, int] = {}
    for i in items:
        counts[i["category"]] = counts.get(i["category"], 0) + 1
    return {"items": items, "counts": counts}

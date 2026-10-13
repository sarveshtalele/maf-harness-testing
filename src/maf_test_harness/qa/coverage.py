"""Measured coverage (accuracy 7.6): pure reading of coverage.py JSON reports for the repository source.

The runner (envs/runners.py) runs the generated tests once with coverage.py through pytest-cov, omitting tests and
the isolated venv, and writes ``coverage.json`` into the execution evidence. This module turns that file into per-file
and per-requirement line and branch percentages. Nothing here is "measured" unless a coverage file was read.
"""

from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path
from typing import Any

# Patterns are matched against workspace-relative paths (coverage runs with relative_files), so a workspace that
# itself lives under a tests/ or test/ directory still measures its own source files.
OMIT_PATTERNS = (
    ".harness-*/*",
    "*/.harness-*/*",
    "*/site-packages/*",
    ".venv/*",
    "*/.venv/*",
    "venv/*",
    "*/venv/*",
    "node_modules/*",
    "*/node_modules/*",
    "tests/*",
    "*/tests/*",
    "test/*",
    "*/test/*",
    "conftest.py",
    "*/conftest.py",
    "test_*.py",
    "*/test_*.py",
    "*_test.py",
    "*/*_test.py",
)

COUNT_KEYS = ("covered_lines", "num_statements", "covered_branches", "num_branches")


def _path(value: str) -> str:
    """A comparable repository-relative path: forward slashes, no leading ./, case-folded on Windows."""
    text = str(value).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lower() if sys.platform.startswith("win") else text


def is_omitted(relative: str) -> bool:
    rel = _path(relative)
    return any(fnmatch.fnmatch(rel, pattern) for pattern in OMIT_PATTERNS)


def count_source_files(root: Path) -> int:
    """Repository Python source files under ``root`` that coverage would consider (tests, venvs and generated
    directories excluded). Reported next to the measured files so the measurement's reach is visible."""
    total = 0
    for folder, dirs, names in os.walk(root):
        rel_dir = Path(folder).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else f"{rel_dir}/"
        dirs[:] = [d for d in dirs if not is_omitted(f"{prefix}{d}/_")]
        for name in names:
            if name.endswith(".py") and not is_omitted(f"{prefix}{name}"):
                total += 1
    return total


def file_counts(report: dict[str, Any], prefix: str = "") -> dict[str, dict[str, int]]:
    """Per-file counts from one coverage.py JSON report (``files`` -> ``summary``), keyed repository-relative: the
    component's directory (``prefix``) is prepended. Malformed rows are skipped."""
    counts: dict[str, dict[str, int]] = {}
    for name, data in (report.get("files") or {}).items():
        summary = data.get("summary") if isinstance(data, dict) else None
        if not isinstance(summary, dict):
            continue
        try:
            row = {key: int(summary.get(key) or 0) for key in COUNT_KEYS}
        except (TypeError, ValueError):
            continue
        key = _path(f"{prefix}/{name}" if prefix and prefix != "." else name)
        counts[key] = row
    return counts


def merge(*parts: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """Several components measure disjoint directories; a file seen twice keeps its last reading."""
    merged: dict[str, dict[str, int]] = {}
    for part in parts:
        merged.update(part)
    return merged


def percent(covered: int, total: int) -> float | None:
    """Percentage to one decimal place, or None when there is nothing to measure (no statements or branches)."""
    if total <= 0:
        return None
    return round(100.0 * covered / total, 1)


def _row(name: str, counts: dict[str, int]) -> dict[str, Any]:
    return {
        "file": name,
        "line_percent": percent(counts["covered_lines"], counts["num_statements"]),
        "branch_percent": percent(counts["covered_branches"], counts["num_branches"]),
        **counts,
    }


def _sum(rows: list[dict[str, int]]) -> dict[str, int]:
    return {key: sum(row[key] for row in rows) for key in COUNT_KEYS}


def summarize(
    files: dict[str, dict[str, int]],
    requirement_sources: dict[str, list[str]] | None = None,
    total_source_files: int | None = None,
) -> dict:
    """Totals, per-file rows and, per requirement, the measured files among its source files.

    A requirement whose source files were not measured reports ``None`` percentages: it is never shown as covered.
    """
    rows = [_row(name, files[name]) for name in sorted(files)]
    totals = _sum(list(files.values())) if files else dict.fromkeys(COUNT_KEYS, 0)
    requirements = []
    for req_id, sources in (requirement_sources or {}).items():
        wanted = sorted({_path(s) for s in sources or []})
        measured = [name for name in wanted if name in files]
        counts = _sum([files[name] for name in measured]) if measured else dict.fromkeys(COUNT_KEYS, 0)
        requirements.append(
            {
                "id": req_id,
                "source_files": wanted,
                "measured_files": measured,
                "line_percent": percent(counts["covered_lines"], counts["num_statements"])
                if measured
                else None,
                "branch_percent": percent(counts["covered_branches"], counts["num_branches"])
                if measured
                else None,
            }
        )
    return {
        "status": "measured",
        "tool": "coverage.py",
        "scope": (
            f"Measured over files imported during the tests ({len(files)} files); unimported files are not counted. "
            + (
                f"The repository has {total_source_files} Python source files."
                if total_source_files is not None
                else ""
            )
        ).strip(),
        "measured_files": len(files),
        "total_source_files": total_source_files,
        "totals": {
            **totals,
            "line_percent": percent(totals["covered_lines"], totals["num_statements"]),
            "branch_percent": percent(totals["covered_branches"], totals["num_branches"]),
        },
        "files": rows,
        "requirements": requirements,
    }


def not_measured(reason: str, tool: str | None = None) -> dict[str, Any]:
    return {"status": "not_measured", "tool": tool, "reason": reason}

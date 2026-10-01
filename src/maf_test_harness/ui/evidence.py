"""Read-only, contained access to execution evidence; never serve target source or configuration."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

ROOTS = (
    "executions",
    "environment",
    "build_checks",
    "generation_attempts",
    "browser-reports",
    "workspace/.harness-reports",
    "workspace/playwright-report",
    "workspace/test-results",
)
MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".html": "text/html",
    ".zip": "application/zip",
    ".xml": "application/xml",
}
MAX_FILE_BYTES = 32 * 1024 * 1024


def inventory(run_dir: Path) -> dict:
    root = run_dir.resolve()
    files = []
    visited = 0
    for folder in ROOTS:
        directory = root / folder
        if not directory.resolve().is_relative_to(root):
            continue
        for current, dirs, names in os.walk(directory, followlinks=False):
            dirs[:] = [
                d
                for d in sorted(dirs)
                if d not in {"node_modules", ".git", ".harness-venv", "__pycache__", "home"}
                and not (Path(current) / d).is_symlink()
            ]
            for name in sorted(names):
                visited += 1
                if visited > 20000:
                    return {"files": files, "truncated": True}
                path = Path(current) / name
                if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
                    continue
                ext = path.suffix.lower()
                kind = (
                    "report"
                    if ext == ".html" and "trace" not in path.relative_to(root).parts
                    else "screenshot"
                    if ext in {".png", ".jpg", ".jpeg", ".webp"}
                    else "trace"
                    if name == "trace.zip"
                    else "junit"
                    if ext == ".xml"
                    else "log"
                    if name.endswith(".log") or name == "error-context.md"
                    else None
                )
                if not kind or path.stat().st_size > MAX_FILE_BYTES:
                    continue
                relative = path.relative_to(root).as_posix()
                files.append(
                    {
                        "id": hashlib.sha256(relative.encode()).hexdigest()[:24],
                        "path": relative,
                        "name": name,
                        "kind": kind,
                        "bytes": path.stat().st_size,
                        "updated": path.stat().st_mtime,
                    }
                )
    return {"files": files, "truncated": False}


def file_path(run_dir: Path, file_id: str, asset: str = "") -> Path:
    item = next((f for f in inventory(run_dir)["files"] if f["id"] == file_id), None)
    if not item:
        raise FileNotFoundError("Evidence not found in this run.")
    path = run_dir.resolve() / item["path"]
    if asset:
        if item["kind"] != "report" or "\\" in asset or any(p in {".", "..", ""} for p in asset.split("/")):
            raise ValueError("Invalid report asset.")
        candidate = (path.parent / asset).resolve()
        if not candidate.is_relative_to(path.parent.resolve()) or candidate.suffix.lower() not in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".zip",
            ".json",
            ".css",
            ".js",
            ".woff2",
            ".md",
            ".txt",
        }:
            raise ValueError("Report asset must remain inside its report directory.")
        path = candidate
    if not path.is_file() or not path.resolve().is_relative_to(run_dir.resolve()):
        raise FileNotFoundError("Evidence file is unavailable.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Evidence exceeds the 32 MB viewer limit.")
    return path

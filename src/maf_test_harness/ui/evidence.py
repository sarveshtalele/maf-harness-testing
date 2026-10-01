"""Read-only, contained access to execution evidence; never serve target source or configuration."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import zipfile
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


def clean_content(path: Path) -> bytes:
    """Redact report text and embedded Playwright data while preserving native assets."""
    from ..hooks.builtin import _redact_value, redact

    content = path.read_bytes()
    mime = MIMES.get(path.suffix.lower())
    if mime == "text/html":
        html = content.decode("utf-8", errors="replace")
        payload = re.search(
            r'(<template id="playwrightReportBase64">data:application/zip;base64,)([^<]+)', html
        )
        if payload:
            source, destination = io.BytesIO(base64.b64decode(payload[2])), io.BytesIO()
            with (
                zipfile.ZipFile(source) as archive,
                zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as clean,
            ):
                if (
                    len(archive.infolist()) > 500
                    or sum(f.file_size for f in archive.infolist()) > 32 * 1024 * 1024
                ):
                    raise ValueError("Embedded report data exceeds the 32 MB viewer limit.")
                for item in archive.infolist():
                    data = archive.read(item)
                    if item.filename.endswith(".json"):
                        data = json.dumps(_redact_value(json.loads(data))[0]).encode()
                    clean.writestr(item.filename, data)
            html = (
                html[: payload.start(2)]
                + base64.b64encode(destination.getvalue()).decode()
                + html[payload.end(2) :]
            )
        # Key/value heuristics confuse minified JS token enums with credentials. Still redact
        # configured secrets and credential-shaped strings inside code, and all report text/data.
        pieces = re.split(r"(<script\b[^>]*>.*?</script>)", html, flags=re.I | re.S)
        content = "".join(
            redact(piece, keyed=False)[0] if re.match(r"<script\b", piece, re.I) else redact(piece)[0]
            for piece in pieces
        ).encode()
    elif path.suffix.lower() == ".js":
        content = redact(content.decode("utf-8", errors="replace"), keyed=False)[0].encode()
    elif path.suffix.lower() in {".json", ".css", ".xml", ".log", ".md", ".txt"}:
        content = redact(content.decode("utf-8", errors="replace"))[0].encode()
    return content

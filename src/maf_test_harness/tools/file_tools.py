"""File tools with hard scopes: read only inside the repository/workspace, write only inside the writable roots.

The scope is enforced here (the handler resolves the path and refuses anything outside) and again by the
``path_scope`` hook: either alone is enough, both together mean disabling hooks does not open the filesystem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..repo.analyzer import SENSITIVE_PATH
from .registry import ToolContext, ToolResult, ToolSpec, object_schema

MAX_LINES = 200
MAX_ENTRIES = 200
MAX_WRITE_BYTES = 200_000


def inside(roots: list[Path], raw: str) -> tuple[Path, Path] | None:
    """(root, resolved path) when ``raw`` (relative to a root, or absolute) stays inside one of ``roots``.

    With several roots (workspace, then repository) a root where the path exists wins over one where it does not.
    """
    candidates = []
    for root in roots:
        candidate = Path(raw)
        target = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if target.is_relative_to(root):
            candidates.append((root, target))
    return next((c for c in candidates if c[1].exists()), candidates[0] if candidates else None)


def _outside(raw: str, what: str) -> ToolResult:
    return ToolResult.failure(
        "FILE-SCOPE", f"'{raw}' is outside the {what}", fixes=["use a path relative to the repository root"]
    )


def read_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    found = inside(ctx.read_roots(), args["path"])
    if not found:
        return _outside(args["path"], "repository")
    root, target = found
    relative = target.relative_to(root).as_posix()
    if SENSITIVE_PATH.search(relative):
        return ToolResult.failure("FILE-SENSITIVE", f"'{relative}' looks like a secret or credential file")
    if not target.is_file():
        return ToolResult.failure(
            "FILE-NOT-FOUND", f"'{relative}' is not a file", fixes=["call list_dir first"]
        )
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(args.get("start", 1)))
    end = min(int(args.get("end", start + MAX_LINES - 1)), start + MAX_LINES - 1, len(lines))
    numbered = [f"{n}: {lines[n - 1]}" for n in range(start, end + 1)]
    return ToolResult(
        True,
        evidence=f"{relative} lines {start}-{end} of {len(lines)}",
        data={"path": relative, "lines": numbered, "total_lines": len(lines)},
        next_actions=[f"call read_file with start={end + 1} for more"] if end < len(lines) else [],
    )


def list_dir(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    found = inside(ctx.read_roots(), args.get("path", "."))
    if not found:
        return _outside(args.get("path", "."), "repository")
    root, target = found
    if not target.is_dir():
        return ToolResult.failure("FILE-NOT-FOUND", f"'{args.get('path', '.')}' is not a directory")
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    rows = [
        f"{p.name}/" if p.is_dir() else p.name
        for p in entries
        if not SENSITIVE_PATH.search(p.relative_to(root).as_posix())
    ]
    return ToolResult(
        True,
        evidence=f"{len(rows)} entries in {target.relative_to(root).as_posix() or '.'}",
        data={"entries": rows[:MAX_ENTRIES], "truncated": len(rows) > MAX_ENTRIES},
    )


def write_test_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    roots = [p.resolve() for p in ctx.writable_roots]
    if not roots:
        return ToolResult.failure("FILE-SCOPE", "no writable directory is configured for this run")
    found = inside(roots, args["path"])
    if not found:
        return _outside(args["path"], "writable test directory")
    root, target = found
    content = args["content"]
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        return ToolResult.failure("FILE-TOO-LARGE", f"content is over {MAX_WRITE_BYTES} bytes")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return ToolResult(
        True,
        evidence=f"wrote {target.relative_to(root).as_posix()}",
        data={"path": target.relative_to(root).as_posix(), "bytes": len(content.encode("utf-8"))},
        next_actions=["run check_test / try_build on the file"],
    )


def delete_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    roots = [p.resolve() for p in ctx.writable_roots]
    found = inside(roots, args["path"]) if roots else None
    if not found:
        return _outside(args["path"], "writable test directory")
    root, target = found
    if not target.is_file():
        return ToolResult.failure("FILE-NOT-FOUND", f"'{args['path']}' is not a file")
    target.unlink()
    return ToolResult(True, evidence=f"deleted {target.relative_to(root).as_posix()}")


PATH = {"type": "string", "description": "Path relative to the repository root"}
FILE_TOOLS = (
    ToolSpec(
        "read_file",
        "Numbered lines of a repository file (at most 200 per call). Secret-looking files are refused.",
        read_file,
        object_schema(
            ["path"],
            path=PATH,
            start={"type": "integer", "minimum": 1},
            end={"type": "integer", "minimum": 1},
        ),
    ),
    ToolSpec("list_dir", "List one directory of the repository.", list_dir, object_schema(path=PATH)),
    ToolSpec(
        "write_test_file",
        "Create or overwrite a generated test file inside the writable test directory.",
        write_test_file,
        object_schema(["path", "content"], path=PATH, content={"type": "string"}),
        writes=True,
    ),
    ToolSpec(
        "delete_file",
        "Delete a generated test file inside the writable test directory.",
        delete_file,
        object_schema(["path"], path=PATH),
        writes=True,
    ),
)

__all__ = ["FILE_TOOLS", "delete_file", "inside", "list_dir", "read_file", "write_test_file"]

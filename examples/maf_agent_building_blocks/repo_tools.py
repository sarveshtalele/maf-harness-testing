"""Small, bounded repository tools for the MAF reference harness."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from agent_framework import FunctionTool, tool

_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}

_SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "id_rsa",
    "id_ed25519",
}


def _safe_file(repo_root: Path, relative_path: str) -> Path:
    root = repo_root.resolve()
    candidate = (root / relative_path).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Path must remain inside the repository.") from exc

    if candidate.name in _SENSITIVE_NAMES:
        raise ValueError("Refusing to read a credential-bearing file.")
    if any(part in _EXCLUDED_DIRS for part in candidate.parts):
        raise ValueError("Refusing to read an excluded directory.")
    if not candidate.is_file():
        raise ValueError(f"File not found: {relative_path}")
    if candidate.stat().st_size > 1_000_000:
        raise ValueError("File is larger than the 1 MB reference-tool limit.")

    return candidate


def build_repository_tools(repo_root: Path) -> list[FunctionTool]:
    """Create the two read-only tools used by the reference harness."""

    root = repo_root.resolve()

    @tool(name="list_repository_files", approval_mode="never_require")
    def list_repository_files(
        limit: Annotated[int, "Maximum number of repository-relative file paths to return"] = 80,
    ) -> str:
        """List a bounded set of repository files, excluding common generated and secret locations."""

        capped = max(1, min(int(limit), 200))
        paths: list[str] = []

        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if path.name in _SENSITIVE_NAMES:
                continue
            if any(part in _EXCLUDED_DIRS for part in rel.parts):
                continue
            paths.append(rel.as_posix())
            if len(paths) >= capped:
                break

        return "\n".join(paths) if paths else "(no files found)"

    @tool(name="read_repository_file", approval_mode="never_require")
    def read_repository_file(
        path: Annotated[str, "Repository-relative path to a text file"],
        start_line: Annotated[int, "First 1-based line to return"] = 1,
        end_line: Annotated[int, "Last 1-based line to return; at most 160 lines are returned"] = 120,
    ) -> str:
        """Read a bounded line range from a repository text file."""

        target = _safe_file(root, path)
        start = max(1, int(start_line))
        requested_end = max(start, int(end_line))
        stop = min(requested_end, start + 159)

        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("Only UTF-8 text files are supported by this reference tool.") from exc

        selected = lines[start - 1 : stop]
        if not selected:
            return "(requested range is empty)"

        return "\n".join(f"{line_no}: {line}" for line_no, line in enumerate(selected, start=start))

    return [list_repository_files, read_repository_file]

"""Artifact presentation helpers shared by Streamlit UI and export scripts.

The workflow stores structured JSON artifacts. This module converts them into
human-readable Markdown and can render the same structure in Streamlit without
losing access to the raw JSON.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any


def humanize(value: str) -> str:
    return str(value).replace("_", " ").strip().title()


def _scalar(value: Any) -> str:
    if value is None:
        return "Not specified"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _escape_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = _scalar(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _is_table_candidate(items: list[Any]) -> bool:
    if not items or not all(isinstance(item, dict) for item in items):
        return False
    if any(isinstance(item.get("content"), str) and "\n" in item.get("content", "") for item in items):
        return False
    keys: list[str] = []
    for item in items:
        for key in item:
            if key not in keys:
                keys.append(key)
    return 0 < len(keys) <= 10


def _markdown_table(items: list[dict[str, Any]]) -> str:
    keys: list[str] = []
    for item in items:
        for key in item:
            if key not in keys:
                keys.append(key)
    header = "| " + " | ".join(humanize(key) for key in keys) + " |"
    rule = "| " + " | ".join("---" for _ in keys) + " |"
    rows = ["| " + " | ".join(_escape_cell(item.get(key)) for key in keys) + " |" for item in items]
    return "\n".join([header, rule, *rows])


def _fence_for_path(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "jsx",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".java": "java",
        ".cs": "csharp",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".kt": "kotlin",
        ".swift": "swift",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
        ".sh": "bash",
        ".ps1": "powershell",
    }.get(suffix, "text")


def json_to_markdown(data: Any, title: str | None = None) -> str:
    """Convert arbitrary JSON-compatible data to readable Markdown."""
    lines: list[str] = []
    if title:
        lines.extend([f"# {title}", ""])

    def emit(value: Any, level: int, key: str | None = None, path_hint: str | None = None) -> None:
        heading_level = min(max(level, 2), 6)
        if key is not None:
            lines.extend([f"{'#' * heading_level} {humanize(key)}", ""])

        if isinstance(value, dict):
            if not value:
                lines.extend(["_None_", ""])
                return
            for child_key, child_value in value.items():
                if child_key == "content" and isinstance(child_value, str) and path_hint:
                    language = _fence_for_path(path_hint)
                    lines.extend([f"```{language}", child_value.rstrip(), "```", ""])
                    continue
                child_path = path_hint
                if child_key == "path" and isinstance(child_value, str):
                    child_path = child_value
                if isinstance(child_value, (dict, list)):
                    emit(child_value, level + 1, child_key, child_path)
                else:
                    label = humanize(child_key)
                    text = _scalar(child_value)
                    if "\n" in text:
                        lines.extend([f"**{label}**", "", text, ""])
                    else:
                        lines.append(f"- **{label}:** {text}")
            lines.append("")
            return

        if isinstance(value, list):
            if not value:
                lines.extend(["_None_", ""])
                return
            if _is_table_candidate(value):
                lines.extend([_markdown_table(value), ""])
                return
            if all(not isinstance(item, (dict, list)) for item in value):
                lines.extend([f"- {_scalar(item)}" for item in value])
                lines.append("")
                return
            for index, item in enumerate(value, 1):
                if isinstance(item, dict):
                    item_title = item.get("title") or item.get("name") or item.get("id") or f"Item {index}"
                    lines.extend([f"{'#' * min(heading_level + 1, 6)} {item_title}", ""])
                    emit(
                        item,
                        level + 2,
                        path_hint=item.get("path") if isinstance(item.get("path"), str) else path_hint,
                    )
                else:
                    emit(item, level + 1, f"Item {index}", path_hint)
            return

        lines.extend([_scalar(value), ""])

    emit(data, 1)
    return "\n".join(lines).strip() + "\n"


def _display_value(value: Any, max_chars: int = 240) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = _scalar(value)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"

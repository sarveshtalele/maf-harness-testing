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


def render_json_ui(data: Any, *, key_prefix: str = "artifact", heading_level: int = 3) -> None:
    """Render structured JSON as Streamlit-native headings, tables, lists and code.

    Streamlit is imported lazily so Markdown export utilities can be used from
    scripts and tests without importing the UI runtime.
    """
    import streamlit as st

    def render(value: Any, prefix: str, level: int, path_hint: str | None = None) -> None:
        if isinstance(value, dict):
            if not value:
                st.caption("None")
                return
            for child_key, child_value in value.items():
                label = humanize(child_key)
                if child_key == "content" and isinstance(child_value, str):
                    st.code(child_value, language=_fence_for_path(path_hint or ""), line_numbers=False)
                    continue
                if child_key == "path" and isinstance(child_value, str):
                    st.markdown(f"**{label}:** `{child_value}`")
                    path_hint = child_value
                    continue
                if isinstance(child_value, (dict, list)):
                    st.markdown(f"{'#' * min(level, 6)} {label}")
                    render(child_value, f"{prefix}-{child_key}", level + 1, path_hint)
                else:
                    if isinstance(child_value, str) and "\n" in child_value:
                        st.markdown(f"**{label}**")
                        st.markdown(child_value)
                    else:
                        st.markdown(f"**{label}:** {_scalar(child_value)}")
            return

        if isinstance(value, list):
            if not value:
                st.caption("None")
                return
            if _is_table_candidate(value):
                rows = []
                keys: list[str] = []
                for item in value:
                    for key in item:
                        if key not in keys:
                            keys.append(key)
                for item in value:
                    rows.append({humanize(key): _display_value(item.get(key)) for key in keys})
                st.dataframe(rows, width="stretch", hide_index=True)
                with st.expander("Full row details", expanded=False):
                    for index, item in enumerate(value, 1):
                        st.markdown(
                            f"**{index}. {item.get('title') or item.get('name') or item.get('id') or 'Details'}**"
                        )
                        render(item, f"{prefix}-detail-{index}", level + 1, item.get("path"))
                return
            if all(not isinstance(item, (dict, list)) for item in value):
                st.markdown("\n".join(f"- {_scalar(item)}" for item in value))
                return
            for index, item in enumerate(value, 1):
                if isinstance(item, dict):
                    title = item.get("title") or item.get("name") or item.get("id") or f"Item {index}"
                    with st.expander(str(title), expanded=False):
                        render(item, f"{prefix}-{index}", level + 1, item.get("path") or path_hint)
                else:
                    render(item, f"{prefix}-{index}", level + 1, path_hint)
            return

        st.write(value)

    render(data, key_prefix, heading_level)


def artifact_downloads(name: str, artifact: Any, *, key_prefix: str) -> None:
    """Render JSON and Markdown download buttons for an artifact."""
    import streamlit as st

    title = humanize(name)
    json_text = json.dumps(artifact, indent=2, ensure_ascii=False, default=str)
    md_text = json_to_markdown(artifact, title=title)
    col_json, col_md = st.columns(2)
    col_json.download_button(
        f"Download {title} JSON",
        json_text,
        file_name=f"{name}.json",
        mime="application/json",
        key=f"{key_prefix}-json",
        width="stretch",
    )
    col_md.download_button(
        f"Download {title} Markdown",
        md_text,
        file_name=f"{name}.md",
        mime="text/markdown",
        key=f"{key_prefix}-md",
        width="stretch",
    )

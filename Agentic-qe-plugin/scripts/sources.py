"""Deterministic, read-only source intake for Agentic QE."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

MAX_FILES = 10
MAX_FILE_BYTES = 10_000_000
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_SECTIONS = 500
MAX_ISSUES = 200
ALLOWED_SUFFIXES = {".md", ".txt", ".csv", ".docx", ".pdf"}
JIRA_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _section(source_id: str, locator: str, title: str, text: str) -> dict:
    return {"source_id": source_id, "locator": locator, "title": title, "text": text[:20_000]}


def _markdown(source_id: str, text: str) -> list[dict]:
    blocks: list[tuple[str, int, list[str]]] = [("Preamble", 1, [])]
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = None if fenced else HEADING.match(line)
        if match:
            blocks.append((match.group(1).strip(), number, [line]))
        else:
            blocks[-1][2].append(line)
    return [
        _section(source_id, f"line:{line}", title, "\n".join(lines).strip())
        for title, line, lines in blocks
        if "\n".join(lines).strip()
    ]


def _csv_sections(source_id: str, data: bytes) -> list[dict]:
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace"), newline="")))
    if not rows:
        return []
    headers = rows[0]
    out = []
    for number, row in enumerate(rows[1:], 2):
        cells = [
            f"{headers[index] if index < len(headers) and headers[index] else f'Column {index + 1}'}: {value.strip()}"
            for index, value in enumerate(row)
            if value.strip()
        ]
        if cells:
            out.append(_section(source_id, f"row:{number}", f"Row {number}", "\n".join(cells)))
    return out


def _docx_sections(source_id: str, data: bytes) -> list[dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_FILE_BYTES:
                raise ValueError("DOCX document.xml exceeds the size limit")
            xml = archive.read(info)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("invalid DOCX") from exc
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise ValueError("DOCX DTD/entity declarations are not allowed")
    root = ElementTree.fromstring(xml)
    out = []
    for number, paragraph in enumerate(root.iter(f"{W}p"), 1):
        text = "".join(node.text or "" for node in paragraph.iter(f"{W}t")).strip()
        if text:
            out.append(_section(source_id, f"paragraph:{number}", f"Paragraph {number}", text))
    return out


def _pdf_sections(source_id: str, data: bytes) -> list[dict]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF intake requires pypdf; install requirements.txt") from exc
    reader = PdfReader(io.BytesIO(data), strict=False)
    if len(reader.pages) > 200:
        raise ValueError("PDF exceeds 200 pages")
    sections = []
    for number, page in enumerate(reader.pages, 1):
        content = page.extract_text() or ""
        if content.strip():
            sections.append(_section(source_id, f"page:{number}", f"Page {number}", content))
    return sections


def ingest_files(files: list[Path]) -> dict:
    if not files or len(files) > MAX_FILES:
        raise ValueError(f"Select 1 to {MAX_FILES} source files")
    sources, sections, total, seen = [], [], 0, set()
    for path in files:
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"Unsupported source file: {path.name}")
        if path.name in seen:
            raise ValueError(f"Duplicate source name: {path.name}")
        seen.add(path.name)
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("Source files exceed the 10 MB limit")
        data = path.read_bytes()
        source_id = f"DOC-{digest(path.name.encode() + bytes([0]) + data)[:12]}"
        suffix = path.suffix.lower()
        if suffix in {".md", ".txt"}:
            parsed = _markdown(source_id, data.decode("utf-8-sig", errors="replace"))
        elif suffix == ".csv":
            parsed = _csv_sections(source_id, data)
        elif suffix == ".docx":
            parsed = _docx_sections(source_id, data)
        else:
            parsed = _pdf_sections(source_id, data)
        sources.append(
            {
                "id": source_id,
                "type": "document",
                "name": path.name,
                "sha256": digest(data),
                "sections": len(parsed),
            }
        )
        sections.extend(parsed)
    if len(sections) > MAX_SECTIONS:
        raise ValueError(f"Source set exceeds {MAX_SECTIONS} parsed sections")
    return {"sources": sources, "sections": sections, "warnings": []}


def adf_text(node: object) -> str:
    """Flatten Jira ADF into reviewable text without interpreting it as instructions."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_text(item) for item in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    content = adf_text(node.get("content") or [])
    if node.get("type") in {"paragraph", "heading", "listItem", "tableRow", "hardBreak"}:
        content += "\n"
    return content


def jira_issue(issue: dict) -> tuple[dict, list[dict]]:
    key = str(issue.get("key") or "")
    if not JIRA_KEY.fullmatch(key):
        raise ValueError("Jira response has an invalid issue key")
    fields = issue.get("fields") or {}
    summary = str(fields.get("summary") or "")
    description = adf_text(fields.get("description") or "").strip()
    text = f"{summary}\n{description}".strip()
    updated = str(fields.get("updated") or "")
    raw = text.encode("utf-8")
    source = {
        "id": f"JIRA-{key}",
        "type": "jira",
        "key": key,
        "name": key,
        "updated": updated,
        "sha256": digest(raw),
        "sections": 1,
    }
    return source, [_section(source["id"], "description", summary or key, text)]


def ingest_jira_issues(payload: object) -> dict:
    issues = payload.get("issues") if isinstance(payload, dict) and "issues" in payload else payload
    if isinstance(issues, dict):
        issues = [issues]
    if not isinstance(issues, list) or not issues or len(issues) > MAX_ISSUES:
        raise ValueError("Jira import needs 1 to 200 issue objects")
    sources, sections, seen = [], [], set()
    for issue in issues:
        if not isinstance(issue, dict):
            raise ValueError("Jira issue must be an object")
        source, found = jira_issue(issue)
        if source["id"] not in seen:
            sources.append(source)
            sections.extend(found)
            seen.add(source["id"])
    return {"sources": sources, "sections": sections, "warnings": []}


def _jira_request(method: str, url: str, headers: dict, payload: dict | None) -> dict:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            return json.loads(response.read(8_000_000))
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Jira HTTP {exc.code} while reading issues") from exc


def fetch_jira(
    base_url: str, email: str, token: str, keys: list[str], jql: str = "", request_fn=None
) -> dict:
    host = urlsplit(base_url)
    if (
        host.scheme != "https"
        or host.username
        or host.password
        or host.path not in {"", "/"}
        or host.query
        or host.fragment
        or not re.fullmatch(r"[a-z0-9-]+\.atlassian\.net", host.netloc)
    ):
        raise ValueError("Jira site must be an HTTPS *.atlassian.net URL")
    if not email or not token or not (keys or jql.strip()):
        raise ValueError("Jira site, email, API token and issue keys or JQL are required")
    if any(not JIRA_KEY.fullmatch(key) for key in keys):
        raise ValueError("Invalid Jira issue key")
    if len(keys) > MAX_ISSUES:
        raise ValueError("Jira issue selection exceeds 200 keys")
    authorization = base64.b64encode(f"{email}:{token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {authorization}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    request = request_fn or _jira_request
    base = f"https://{host.netloc}"
    issues = {}
    for key in keys:
        issues[key] = request(
            "GET", f"{base}/rest/api/3/issue/{quote(key)}?fields=summary,description,updated", headers, None
        )
    if jql.strip():
        cursor = None
        while len(issues) < MAX_ISSUES:
            payload = {
                "jql": jql.strip(),
                "maxResults": min(100, MAX_ISSUES - len(issues)),
                "fields": ["summary", "description", "updated"],
            }
            if cursor:
                payload["nextPageToken"] = cursor
            response = request("POST", f"{base}/rest/api/3/search/jql", headers, payload)
            for issue in response.get("issues") or []:
                if issue.get("key"):
                    issues[issue["key"]] = issue
            next_cursor = response.get("nextPageToken")
            if not next_cursor or next_cursor == cursor or not response.get("issues"):
                break
            cursor = next_cursor
    sources, sections = [], []
    for issue in list(issues.values())[:MAX_ISSUES]:
        source, found = jira_issue(issue)
        sources.append(source)
        sections.extend(found)
    return {
        "sources": sources,
        "sections": sections,
        "warnings": ["Jira result capped at 200 issues"] if len(issues) > MAX_ISSUES else [],
    }

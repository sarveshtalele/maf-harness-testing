"""Greenfield intake: deterministic parsing of documents and Jira Cloud stories into sections, then specs -> requirements.

No model is involved here. ``ingest_specs`` turns uploaded documents (md/txt/docx; pdf only when ``pypdf`` is installed)
and Jira Cloud issues into ``sections``; ``build_specs`` validates the model's spec drafts against those sections and
assigns stable ids; ``specs_to_requirements`` converts specs into the existing requirement model.
The Jira API token is used for one request header and is never logged, returned or stored.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import urllib.error
import urllib.request
import zipfile
from base64 import b64encode
from collections.abc import Callable
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree

from ..models import Spec, SpecSource
from ..skills.contracts import _descriptive_id

MAX_JIRA_ISSUES = 200
JIRA_TIMEOUT = 15
MAX_SECTION_CHARS = 20_000
MAX_SECTIONS = 500
MAX_DOC_BYTES = 10_000_000
JIRA_PAGE = 100
CLOUD_ID_RE = re.compile(r"[A-Za-z0-9-]{1,64}")
KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

Fetch = Callable[[str, str, dict, bytes | None, int], tuple[int, dict]]


# ---- sections ---------------------------------------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "section"


def _sections_from_blocks(doc: str, blocks: list[tuple[str, str]]) -> list[dict]:
    """``blocks`` are (heading-or-"", text) pairs in order; builds unique anchors, drops empty ones."""
    sections, used = [], set()
    for title, text in blocks:
        text = text.strip()
        if not text:
            continue
        base = _slug(title or "preamble")
        anchor, n = base, 2
        while anchor in used:
            anchor, n = f"{base}-{n}", n + 1
        used.add(anchor)
        sections.append(
            {
                "anchor": anchor,
                "title": title or "Preamble",
                "text": text[:MAX_SECTION_CHARS],
                "source": {"doc": doc, "section_anchor": anchor},
            }
        )
    return sections


def parse_markdown(doc: str, text: str) -> list[dict]:
    blocks: list[tuple[str, list[str]]] = [("", [])]
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = None if fenced else HEADING_RE.match(line)
        if match:
            blocks.append((match.group(2).strip(), []))
        else:
            blocks[-1][1].append(line)
    return _sections_from_blocks(doc, [(t, "\n".join(lines)) for t, lines in blocks])


def parse_docx(doc: str, data: bytes) -> list[dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOC_BYTES:
                raise ValueError("document.xml is too large")
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("not a valid .docx file") from exc
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise ValueError("docx contains a DTD or entity declaration")
    root = ElementTree.fromstring(xml)
    blocks: list[tuple[str, list[str]]] = [("", [])]
    for paragraph in root.iter(f"{W}p"):
        style = paragraph.find(f"{W}pPr/{W}pStyle")
        style_id = style.get(f"{W}val", "") if style is not None else ""
        text = "".join(
            "\t" if node.tag == f"{W}tab" else "\n" if node.tag == f"{W}br" else (node.text or "")
            for node in paragraph.iter()
            if node.tag in (f"{W}t", f"{W}tab", f"{W}br")
        ).strip()
        if not text:
            continue
        if re.fullmatch(r"(?i)heading\s*\d|title", style_id):
            blocks.append((text, []))
        else:
            blocks[-1][1].append(text)
    return _sections_from_blocks(doc, [(t, "\n".join(lines)) for t, lines in blocks])


def parse_pdf(doc: str, data: bytes) -> list[dict]:
    try:
        from pypdf import PdfReader  # optional: not a declared dependency yet
    except ImportError as exc:
        raise ValueError("PDF parsing needs pypdf (not installed); upload md, txt or docx instead") from exc
    pages = (page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    return parse_markdown(doc, "\n\n".join(pages))


# ---- Jira Cloud -------------------------------------------------------------------------------------------------------


def adf_to_text(node) -> str:
    """Atlassian Document Format (or a plain string) to text; headings become markdown ``#`` lines."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    out: list[str] = []

    def walk(n, prefix=""):
        kind = n.get("type")
        children = n.get("content") or []
        if kind == "text":
            out.append(n.get("text", ""))
        elif kind == "hardBreak":
            out.append("\n")
        elif kind == "mention":
            out.append((n.get("attrs") or {}).get("text", "@user"))
        elif kind in ("bulletList", "orderedList"):
            for i, item in enumerate(children, 1):
                walk(item, f"{i}. " if kind == "orderedList" else "- ")
        elif kind == "listItem":
            out.append(prefix)
            for child in children:
                walk(child)
        elif kind == "tableRow":
            for j, cell in enumerate(children):
                out.append(" | " if j else "")
                walk(cell)
            out.append("\n")
        else:
            if kind == "heading":
                out.append("#" * int((n.get("attrs") or {}).get("level", 2)) + " ")
            for child in children:
                walk(child)
            if kind in ("paragraph", "heading", "codeBlock", "blockquote", "tableCell", "tableHeader"):
                out.append("\n")

    if isinstance(node, dict):
        walk(node)
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def _ac_lines(text: str) -> list[str]:
    return [re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", ln).strip() for ln in text.splitlines() if ln.strip()]


def _acceptance_criteria(description: str, custom: list[str]) -> tuple[list[str], str]:
    """AC lines from a custom field plus an 'Acceptance Criteria' section of the description (which is cut out)."""
    found, kept, inside = list(custom), [], False
    for line in description.splitlines():
        match = HEADING_RE.match(line)
        title = match.group(2) if match else None
        if title is None and re.fullmatch(r"\s*\**acceptance criteria\**:?\s*", line, re.I):
            title = "acceptance criteria"
        if title is not None:
            inside = title.lower().startswith("acceptance criteria")
            if inside:
                continue
        if inside:
            found.extend(_ac_lines(line))
        else:
            kept.append(line)
    return found, "\n".join(kept).strip()


def _default_fetch(
    method: str, url: str, headers: dict, body: bytes | None, timeout: int
) -> tuple[int, dict]:
    class NoRedirect(urllib.request.HTTPRedirectHandler):  # never forward credentials to another host
        def redirect_request(self, *args, **kwargs):
            return None

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read(8_000_000) or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def _jira_base(url: str) -> str:
    parts = urlsplit(str(url or "").strip())
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or not re.fullmatch(r"[a-z0-9-]+\.atlassian\.net", host.lower())
        or parts.path not in ("", "/")
    ):
        raise ValueError("Jira base_url must be https://<site>.atlassian.net")
    return f"https://{host.lower()}"


def _issue_section(issue: dict, names: dict) -> dict:
    fields = issue.get("fields") or {}
    key = str(issue.get("key", ""))
    description = adf_to_text(fields.get("description"))
    custom = [
        line
        for field_id, label in names.items()
        if re.search(r"acceptance criteria", str(label), re.I) and fields.get(field_id)
        for line in _ac_lines(adf_to_text(fields[field_id]))
    ]
    criteria, description = _acceptance_criteria(description, custom)
    summary = str(fields.get("summary") or "").strip()
    head = [
        f"Type: {(fields.get('issuetype') or {}).get('name', '')}",
        f"Status: {(fields.get('status') or {}).get('name', '')}",
        f"Labels: {', '.join(fields.get('labels') or [])}",
    ]
    parts = [
        "\n".join(head),
        description,
        "Acceptance Criteria:\n" + "\n".join(f"- {c}" for c in criteria) if criteria else "",
    ]
    return {
        "anchor": key,
        "title": f"{key}: {summary}",
        "text": "\n\n".join(p for p in parts if p)[:MAX_SECTION_CHARS],
        "acceptance_criteria": criteria,
        "source": {"jira_key": key},
    }


def fetch_jira(jira: dict, fetch: Fetch | None = None) -> tuple[list[dict], dict, list[str]]:
    base = _jira_base(jira.get("base_url"))
    email, token = str(jira.get("email") or ""), str(jira.get("api_token") or "")
    if not email or not token:
        raise ValueError("Jira email and api_token are required")
    keys = [str(k).strip().upper() for k in jira.get("issue_keys") or []]
    if any(not KEY_RE.match(k) for k in keys):
        raise ValueError("issue_keys must look like PROJ-123")
    jql = str(jira.get("jql") or "").strip() or (f"key in ({','.join(keys)}) ORDER BY key" if keys else "")
    if not jql:
        raise ValueError("Provide jql or issue_keys")
    headers = {
        "Authorization": "Basic " + b64encode(f"{email}:{token}".encode()).decode(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    fetch = fetch or _default_fetch
    sections, warnings, page_token, names = [], [], None, {}
    while True:
        payload = {
            "jql": jql,
            "fields": ["*all"],
            "expand": "names",
            "maxResults": min(JIRA_PAGE, MAX_JIRA_ISSUES - len(sections)),
            **({"nextPageToken": page_token} if page_token else {}),
        }
        try:
            status, body = fetch(
                "POST", f"{base}/rest/api/3/search/jql", headers, json.dumps(payload).encode(), JIRA_TIMEOUT
            )
        except Exception as exc:
            raise ValueError(f"Jira request failed ({type(exc).__name__})") from None
        if status in (401, 403):
            raise ValueError(f"Jira rejected the credentials or permissions (HTTP {status})")
        if status != 200:
            raise ValueError(f"Jira request failed (HTTP {status})")
        names.update(body.get("names") or {})
        sections += [_issue_section(i, names) for i in body.get("issues") or []]
        page_token = body.get("nextPageToken")
        if len(sections) >= MAX_JIRA_ISSUES:
            if page_token:
                warnings.append(f"Jira results capped at {MAX_JIRA_ISSUES} issues")
            break
        if not page_token or body.get("isLast") or not body.get("issues"):
            break
    sections = sections[:MAX_JIRA_ISSUES]
    source = {"kind": "jira", "base_url": base, "query": jql, "issues": len(sections)}
    return sections, source, warnings


def _mcp_call(server: dict, tool_name: str, arguments: dict) -> str:
    """Call one tool on a configured MCP server (own event loop: ingest runs in a worker thread)."""
    from .. import mcp_servers

    async def run() -> str:
        tool = mcp_servers.build_tool(server)
        try:
            await mcp_servers.connect(tool)
            out = await tool.call_tool(tool_name, **arguments)
        finally:
            await tool.close()
        return out if isinstance(out, str) else "\n".join(getattr(c, "text", "") or "" for c in out)

    return asyncio.run(run())


def fetch_jira_via_mcp(spec: dict, call: Callable[[dict, str, dict], str] | None = None):
    """Jira stories through a configured MCP server: ``{server, jql | issue_keys, tool?, cloud_id?}``.

    Only a tool on the server's auto-approved (read-only) list may be used here, because this call does not go through
    the model's approval gate. The result must be Jira search JSON (``{"issues": [...]}``) or a list of issues."""
    from .. import mcp_servers

    server = mcp_servers.get(str(spec.get("server") or ""))
    if not server["enabled"]:
        raise ValueError("That MCP server is disabled.")
    tool_name = str(spec.get("tool") or "searchJiraIssuesUsingJql")
    if tool_name not in server["allowed_tools"]:
        raise ValueError(f"MCP tool {tool_name!r} is not in the server's auto-approved (read-only) list.")
    keys = [str(k).strip().upper() for k in spec.get("issue_keys") or []]
    if any(not KEY_RE.match(k) for k in keys):
        raise ValueError("issue_keys must look like PROJ-123")
    jql = str(spec.get("jql") or "").strip() or (f"key in ({','.join(keys)}) ORDER BY key" if keys else "")
    if not jql:
        raise ValueError("Provide jql or issue_keys")
    arguments: dict = {"jql": jql}  # free-form arguments are not accepted
    cloud_id = spec.get("cloud_id")
    if cloud_id:
        if not isinstance(cloud_id, str) or not CLOUD_ID_RE.fullmatch(cloud_id):
            raise ValueError("cloud_id must be 1-64 letters, digits or '-'.")
        arguments["cloudId"] = cloud_id
    text = (call or _mcp_call)(server, tool_name, arguments)
    try:
        body = json.loads(text)
    except ValueError:
        raise ValueError("The MCP server did not return JSON issues.") from None
    issues = body.get("issues") if isinstance(body, dict) else body
    if not isinstance(issues, list):
        raise ValueError("The MCP server did not return a list of issues.")
    names = body.get("names") if isinstance(body, dict) else {}
    sections = [_issue_section(i, names or {}) for i in issues[:MAX_JIRA_ISSUES] if isinstance(i, dict)]
    warnings = [f"Jira results capped at {MAX_JIRA_ISSUES} issues"] if len(issues) > MAX_JIRA_ISSUES else []
    source = {"kind": "jira", "via": f"mcp:{server['name']}", "query": jql, "issues": len(sections)}
    return sections, source, warnings


# ---- public API -------------------------------------------------------------------------------------------------------


def ingest_specs(
    documents: list[dict] | None,
    jira: dict | None = None,
    fetch: Fetch | None = None,
    jira_via_mcp: dict | None = None,
    mcp_call=None,
) -> dict:
    """Parse documents ``[{name, content: bytes}]`` and/or Jira issues into ``{"sources", "sections", "warnings"}``."""
    sources, sections, warnings = [], [], []
    for item in documents or []:
        name = PurePosixPath(str(item.get("name") or "").replace("\\", "/")).name
        data = item.get("content") or b""
        if isinstance(data, str):
            data = data.encode("utf-8")
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        try:
            if len(data) > MAX_DOC_BYTES:
                raise ValueError("file is larger than 10 MB")
            if extension in ("md", "markdown", "txt"):
                found = parse_markdown(
                    name, data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
                )
            elif extension == "docx":
                found = parse_docx(name, data)
            elif extension == "pdf":
                found = parse_pdf(name, data)
            else:
                raise ValueError("unsupported file type (use md, txt, docx or pdf)")
        except (ValueError, ElementTree.ParseError) as exc:
            warnings.append(f"{name or 'document'}: {exc}")
            continue
        if not found:
            warnings.append(f"{name}: no text sections found")
        sources.append(
            {
                "kind": "document",
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "sections": len(found),
            }
        )
        sections += found
    if jira:
        found, source, notes = fetch_jira(jira, fetch)
        sources.append(source)
        sections += found
        warnings += notes
    if jira_via_mcp:
        found, source, notes = fetch_jira_via_mcp(jira_via_mcp, mcp_call)
        sources.append(source)
        sections += found
        warnings += notes
    if len(sections) > MAX_SECTIONS:
        warnings.append(f"Only the first {MAX_SECTIONS} sections were kept")
        sections = sections[:MAX_SECTIONS]
    return {"sources": sources, "sections": sections, "warnings": warnings}


def section_ref(section: dict) -> str:
    source = section["source"]
    return source["jira_key"] if source.get("jira_key") else f"{source['doc']}#{source['section_anchor']}"


def source_key(source: dict) -> str:
    return (
        f"jira:{source['jira_key']}"
        if source.get("jira_key")
        else f"doc:{source['doc']}#{source['section_anchor']}"
    )


def build_specs(drafts: list[dict], sections: list[dict], start: dict[str, int] | None = None) -> list[dict]:
    """Validate drafts against the supplied sections (no invented sources) and assign stable ids.
    ``start`` carries per-source counters across batches so ``n`` stays unique."""
    by_ref = {section_ref(s): s for s in sections}
    counters = start if start is not None else {}
    specs = []
    for draft in drafts:
        section = by_ref.get(draft["source_ref"])
        if section is None:
            raise ValueError(
                f"Spec {draft['title']!r} cites source_ref {draft['source_ref']!r}, which is not a supplied section; "
                f"use one of: {sorted(by_ref)[:20]}"
            )
        source = section["source"]
        key = source_key(source)
        counters[key] = counters.get(key, 0) + 1
        specs.append(
            Spec(
                id=f"SPEC-{hashlib.sha256(key.encode()).hexdigest()[:8]}-{counters[key]}",
                title=draft["title"].strip(),
                narrative=draft["narrative"].strip(),
                acceptance_criteria=[c.strip() for c in draft["acceptance_criteria"]],
                business_rules=[c.strip() for c in draft["business_rules"]],
                data_examples=[c.strip() for c in draft["data_examples"]],
                source=SpecSource(**source),
            ).model_dump(exclude_none=True)
        )
    return specs


_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "is",
        "are",
        "be",
        "been",
        "of",
        "to",
        "for",
        "on",
        "in",
        "at",
        "by",
        "with",
        "and",
        "or",
        "any",
        "each",
        "it",
        "its",
        "this",
        "that",
        "than",
        "then",
        "get",
        "gets",
        "every",
        "all",
        "must",
        "should",
        "shall",
        "will",
        "can",
    ]
)


def _words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9.%]*", text.lower())
    return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words if w not in _STOP}


def _restates(text: str, others: list[str], threshold: float = 0.5) -> bool:
    """True when ``text`` mostly repeats one of ``others`` (Jaccard over content words)."""
    mine = _words(text)
    for other in others:
        theirs = _words(other)
        if mine and theirs and len(mine & theirs) / len(mine | theirs) >= threshold:
            return True
    return False


def specs_to_requirements(specs: list[dict], app_name: str, summary: str = "") -> dict:
    """Existing requirement-model artifact: one requirement per acceptance criterion and business rule (or one
    from the narrative when a spec has neither), each carrying ``traceability: {spec_id, source}``."""
    requirements = []
    for spec in specs:
        # Authors often restate an acceptance criterion as a business rule; one requirement per behaviour.
        kept: list[str] = list(spec["acceptance_criteria"])
        rules = []
        for rule in spec["business_rules"]:
            if not _restates(rule, kept):
                rules.append(rule)
                kept.append(rule)
        items = [(t, "functional") for t in spec["acceptance_criteria"]] + [(t, "validation") for t in rules]
        items = items or [(spec["narrative"] or spec["title"], "functional")]
        for text, category in items:
            requirements.append(
                {
                    "id": _descriptive_id("REQ", text, f"spec:{spec['id']}:{category}:{text}"),
                    "text": text,
                    "evidence": f"{spec['id']} {spec['title']}: {spec['narrative'][:300]}".strip(),
                    "source_files": [],
                    "category": category,
                    "actor": "User",
                    "traceability": {"spec_id": spec["id"], "source": spec["source"]},
                }
            )
    return {
        "app_name": app_name,
        "summary": summary or f"{len(specs)} specification(s) provided by the business analyst.",
        "actors": [
            {
                "name": "User",
                "description": "A user of the system described by the specifications.",
                "source_files": [],
            }
        ],
        "requirement_model": {
            "requirements": requirements,
            "validation_rules": [],
            "acceptance_criteria": [],
        },
        "api_contracts": [],
        "environment_needs": [],
        "exclusions": [],
        "assumptions": [],
        "gaps": [],
    }

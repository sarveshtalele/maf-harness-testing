"""Run options: mode (brownfield/greenfield), test types (unit/integration), greenfield spec sources.

Credentials: ``jira.api_token`` is accepted only to be handed to the intake call in memory; it is never part of the
sanitized ``spec_sources`` that goes into run state (the desktop keychain will supply it later).
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import PurePosixPath
from typing import Any

RUN_MODES = ("brownfield", "greenfield")
TEST_TYPES = ("unit", "integration")
DOC_EXTENSIONS = {".md", ".txt", ".docx", ".pdf"}
MAX_DOCS = 10
MAX_DOC_BYTES = 10 * 1024 * 1024
_JIRA_BASE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.atlassian\.net/?")
_JIRA_KEY = re.compile(r"[A-Z][A-Z0-9_]+-\d+")
_NON_BOUNDARY = {"ui", "e2e", "accessibility", "performance", "security", "cli"}


def parse_run_options(body: dict) -> dict[str, Any]:
    """Validate the mode/test-type/spec-source fields of POST /runs.

    Returns run_mode, test_types, spec_sources (sanitized, no secrets), documents [(name, bytes)], jira_token.
    """
    mode = body.get("run_mode", "brownfield")
    if mode not in RUN_MODES:
        raise ValueError("run_mode must be 'brownfield' or 'greenfield'.")
    types = body.get("test_types", list(TEST_TYPES))
    if not isinstance(types, list) or not types or any(t not in TEST_TYPES for t in types):
        raise ValueError("test_types must be a non-empty list drawn from 'unit' and 'integration'.")
    test_types = [t for t in TEST_TYPES if t in types]
    out: dict[str, Any] = {
        "run_mode": mode,
        "test_types": test_types,
        "spec_sources": None,
        "documents": [],
        "jira_token": None,
    }
    sources = body.get("spec_sources")
    if mode == "brownfield":
        if sources:
            raise ValueError("spec_sources are only valid for greenfield runs.")
        return out
    if not isinstance(sources, dict):
        raise ValueError("Greenfield runs need spec_sources (documents and/or jira).")
    docs = sources.get("documents") or []
    if not isinstance(docs, list) or len(docs) > MAX_DOCS:
        raise ValueError(f"At most {MAX_DOCS} specification documents are allowed.")
    total, parsed, names = 0, [], set()
    for doc in docs:
        if not isinstance(doc, dict) or not isinstance(doc.get("name"), str):
            raise ValueError("Each document needs a name and content_base64.")
        name = PurePosixPath(doc["name"].replace("\\", "/")).name
        if not name or name.startswith(".") or PurePosixPath(name).suffix.lower() not in DOC_EXTENSIONS:
            raise ValueError("Documents must be .md, .txt, .docx or .pdf files.")
        if name in names:
            raise ValueError(f"Duplicate document name: {name}")
        names.add(name)
        try:
            data = base64.b64decode(doc.get("content_base64") or "", validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ValueError(f"Document {name} is not valid base64.") from exc
        total += len(data)
        if total > MAX_DOC_BYTES:
            raise ValueError("Specification documents may total at most 10 MB.")
        parsed.append((name, data))
    out["documents"] = parsed
    jira = sources.get("jira")
    clean_jira = None
    if jira:
        if not isinstance(jira, dict):
            raise ValueError("jira must be an object.")
        base = jira.get("base_url")
        if not isinstance(base, str) or not _JIRA_BASE.fullmatch(base.strip()):
            raise ValueError("Jira base_url must be https://<site>.atlassian.net.")
        keys = jira.get("issue_keys") or []
        jql = jira.get("jql") or ""
        if (
            not isinstance(jql, str)
            or not isinstance(keys, list)
            or any(not isinstance(k, str) or not _JIRA_KEY.fullmatch(k) for k in keys)
        ):
            raise ValueError("Jira jql must be text and issue_keys must look like PROJ-123.")
        if not jql.strip() and not keys:
            raise ValueError("Jira needs a JQL query or issue keys.")
        email = jira.get("email")
        if not isinstance(email, str) or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
            raise ValueError("Jira email is required.")
        token = jira.get("api_token")
        if token is not None and not isinstance(token, str):
            raise ValueError("Jira api_token must be text.")
        out["jira_token"] = token or None
        jql = jql.strip()
        clean_jira = {
            "base_url": base.strip().rstrip("/"),
            "jql": jql,
            "issue_keys": keys,
            "email": email.strip(),
        }
    clean_mcp = None
    mcp = sources.get("jira_mcp")  # Jira through a configured MCP server (docs/MCP.md); no secrets here
    if mcp:
        keys = mcp.get("issue_keys") or [] if isinstance(mcp, dict) else None
        jql = mcp.get("jql") or "" if isinstance(mcp, dict) else None
        if (
            keys is None
            or not isinstance(jql, str)
            or not isinstance(keys, list)
            or any(not isinstance(k, str) or not _JIRA_KEY.fullmatch(k) for k in keys)
            or not isinstance(mcp.get("server"), str)
            or not (jql.strip() or keys)
        ):
            raise ValueError("jira_mcp needs a server name and a JQL query or issue keys.")
        clean_mcp = {"server": mcp["server"], "jql": jql.strip(), "issue_keys": keys}
        if mcp.get("tool"):
            clean_mcp["tool"] = str(mcp["tool"])
    if not parsed and not clean_jira and not clean_mcp:
        raise ValueError("Greenfield runs need at least one document or a Jira source.")
    out["spec_sources"] = {
        "documents": [{"name": n, "size": len(d)} for n, d in parsed],
        "jira": clean_jira,
        **({"jira_mcp": clean_mcp} if clean_mcp else {}),
    }
    return out


def restrict_levels(draft: dict, test_types) -> list[str]:
    """Host-side allowlist on a design draft (in place): api -> integration; other levels become integration when the
    target is a service/API boundary, else they are dropped; levels outside ``test_types`` are dropped. Returns gap notes."""
    allowed = set(test_types)
    notes: list[str] = []
    for key, cases in list(draft.items()):
        if not key.endswith("_tests") or not isinstance(cases, list):
            continue
        kept = []
        for case in cases:
            level = case.get("level") if isinstance(case, dict) else None
            new = level
            if level == "api":
                new = "integration"
            elif level in _NON_BOUNDARY:
                target = str(case.get("target", ""))
                boundary = bool(re.match(r"(?:[A-Z]+\s+)?/|https?://", target.strip()))
                new = "integration" if boundary else None
            if new in allowed:
                if new != level:
                    case["level"] = new
                kept.append(case)
            else:
                notes.append(f"level {level} excluded by run test types: dropped '{case.get('title')}'")
        draft[key] = kept
    return notes

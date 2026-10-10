"""User-configured MCP servers (docs/MCP.md): config on disk, secrets only in memory/keychain, MAF tool construction.

``<user-data>/mcp_servers.json`` holds names, commands, URLs, tool lists and the NAMES of secret variables, never their
values. Values live in a process-local store (set through the API) or arrive as ``HARNESS_MCP_SECRET_<SERVER>_<KEY>``
environment variables (the desktop app reads them from the OS keychain). They are resolved at spawn and never logged.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
from typing import Any
from urllib.parse import urlsplit

from . import paths
from .storage import atomic_json

STAGES = (
    "requirements",
    "test_planning",
    "test_design",
    "test_generation",
    "environment_setup",
    "test_execution",
)
SECRET_ENV_PREFIX = "HARNESS_MCP_SECRET_"
BEARER_KEY = "BEARER_TOKEN"  # reserved secret name of an http server's Authorization value
MAX_SERVERS = 20
MAX_LISTED_TOOLS = (
    8  # per server: every tool description is resent with every model call (stage budget is 20)
)
MAX_MCP_TOOLS_PER_STAGE = 8
CONNECT_TIMEOUT_SECONDS = 25  # below the UI proxy's 30 s limit
_SLUG = re.compile(r"[a-z][a-z0-9-]{0,31}")
_ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
# Variables that change how a process loads code, resolves hosts or trusts certificates; a secret field must never set them.
_FORBIDDEN_PREFIXES = ("DYLD_", "LD_", "NODE_", "PYTHON", "NPM_CONFIG_")
_FORBIDDEN_ENV = {
    "PATH",
    "PATHEXT",
    "HOME",
    "COMSPEC",
    "BASH_ENV",
    "ENV",
    "PERL5LIB",
    "RUBYOPT",
    "JAVA_TOOL_OPTIONS",
    "SSL_CERT_FILE",
}


def forbidden_env(key: str) -> bool:
    k = key.upper()
    return k in _FORBIDDEN_ENV or k.startswith(_FORBIDDEN_PREFIXES) or k.endswith("_PROXY")


PRESETS: dict[str, dict[str, Any]] = {
    "atlassian-jira-cloud": {
        "label": "Atlassian Jira Cloud (Rovo MCP, remote)",
        "note": (
            "Streamable HTTP endpoint recommended by Atlassian (v2). OAuth is interactive and not supported here: an "
            "org admin must enable API token auth (Atlassian Administration > Rovo MCP server); paste a service "
            "account API key (Bearer) or 'Basic <base64(email:token)>' as the token. VERIFY the endpoint and the "
            "tool names with Test: v2 may expose discovery tools instead (append ?tools=all for a flat list)."
        ),
        "transport": "http",
        "url": "https://mcp.atlassian.com/v2/mcp",
        "stages": ["requirements"],
        "allowed_tools": ["searchJiraIssuesUsingJql", "getJiraIssue", "getVisibleJiraProjects"],
        "approval_required_tools": ["createJiraIssue", "editJiraIssue", "addCommentToJiraIssue"],
    },
    "playwright": {
        "label": "Playwright MCP (local browser, stdio)",
        "note": (
            "Runs `npx @playwright/mcp@latest` (needs Node.js; downloads the package on first use). Only "
            "snapshot/console reads are auto-approved. Navigating or clicking is a side effect on live sites, so "
            "those tools require human approval."
        ),
        "transport": "stdio",
        "command": "npx",
        "args": ["@playwright/mcp@latest"],
        "stages": [],
        "allowed_tools": ["browser_snapshot", "browser_console_messages", "browser_network_requests"],
        "approval_required_tools": [
            "browser_navigate",
            "browser_click",
            "browser_type",
            "browser_take_screenshot",
        ],
    },
}


# ---- validation -------------------------------------------------------------------------------------------------
def _str_list(value: Any, field: str, pattern: re.Pattern | None = None, limit: int = 50) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{field} must be a list of at most {limit} strings.")
    out = list(dict.fromkeys(v.strip() for v in value if v.strip()))
    if pattern and not all(pattern.fullmatch(v) for v in out):
        raise ValueError(f"{field} contains an invalid entry.")
    return out


def validate_url(url: str) -> str:
    parts = urlsplit(url.strip())
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme not in {"https", "http"} or not parts.hostname or (parts.scheme == "http" and not local):
        raise ValueError("MCP URL must use https (http is allowed only for localhost).")
    if parts.username or parts.password:
        raise ValueError("MCP URL must not contain credentials; use the token field.")
    return url.strip()


def validate(data: dict) -> dict:
    """Normalise one server definition; raises ValueError. Unknown keys (e.g. a pasted secret) are rejected."""
    if not isinstance(data, dict):
        raise ValueError("Server must be an object.")
    unknown = set(data) - {
        "name",
        "transport",
        "command",
        "args",
        "env_keys",
        "url",
        "enabled",
        "stages",
        "allowed_tools",
        "approval_required_tools",
        "preset",
    }
    if unknown:
        raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}. Secrets are set separately.")
    name = str(data.get("name", "")).strip()
    if not _SLUG.fullmatch(name):
        raise ValueError("Name must be a lowercase slug (a-z, 0-9, '-'; max 32; starts with a letter).")
    transport = data.get("transport")
    if transport not in {"stdio", "http"}:
        raise ValueError("Transport must be 'stdio' or 'http'.")
    preset = data.get("preset") or ""
    if preset and preset not in PRESETS:
        raise ValueError("Unknown preset.")
    out: dict[str, Any] = {
        "name": name,
        "transport": transport,
        "command": "",
        "args": [],
        "env_keys": _str_list(data.get("env_keys"), "env_keys", _ENV_KEY, 20),
        "url": "",
        "enabled": data.get("enabled") is True,
        "stages": _str_list(data.get("stages"), "stages", limit=len(STAGES)),
        "allowed_tools": _str_list(data.get("allowed_tools"), "allowed_tools", _TOOL_NAME, MAX_LISTED_TOOLS),
        "approval_required_tools": _str_list(
            data.get("approval_required_tools"), "approval_required_tools", _TOOL_NAME, MAX_LISTED_TOOLS
        ),
        "preset": preset,
    }
    if set(out["stages"]) - set(STAGES):
        raise ValueError("Unknown stage in stages.")
    if len(set(out["allowed_tools"]) | set(out["approval_required_tools"])) > MAX_LISTED_TOOLS:
        raise ValueError(
            f"At most {MAX_LISTED_TOOLS} tools per server (each tool is resent with every model call)."
        )
    if set(out["allowed_tools"]) & set(out["approval_required_tools"]):
        raise ValueError("A tool cannot be both auto-approved and approval-required.")
    if any(forbidden_env(k) for k in out["env_keys"]):
        raise ValueError("That environment variable cannot be set for an MCP server.")
    if transport == "stdio":
        command = str(data.get("command", "")).strip()
        if not command or len(command) > 300 or "\n" in command or "\x00" in command:
            raise ValueError("A stdio server needs a command (executable name or path).")
        args = _str_list(data.get("args"), "args", limit=50)
        if any(len(a) > 1000 or "\x00" in a for a in args):
            raise ValueError("Invalid argument.")
        out["command"], out["args"] = command, args
    else:
        out["url"] = validate_url(str(data.get("url", "")))
        if out["env_keys"]:
            raise ValueError("env_keys apply to stdio servers; an http server uses the token field.")
    return out


# ---- storage ----------------------------------------------------------------------------------------------------
_file_lock = threading.Lock()


def config_path():
    return paths.user_data_root() / "mcp_servers.json"


def load() -> list[dict]:
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
        servers = raw["servers"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    out = []
    for item in servers if isinstance(servers, list) else []:
        try:
            out.append(validate(item))
        except ValueError:
            continue  # a hand-edited bad entry is ignored, never trusted
    return out


def _save(servers: list[dict]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, {"servers": servers})


def get(name: str) -> dict:
    for server in load():
        if server["name"] == name:
            return server
    raise FileNotFoundError("MCP server not found.")


def upsert(data: dict) -> dict:
    server = validate(data)
    with _file_lock:
        old = next((s for s in load() if s["name"] == server["name"]), None)
        if old and any(old[k] != server[k] for k in ("url", "transport", "command")):
            clear_secrets(server["name"])  # a secret never follows a server to a different target
        servers = [s for s in load() if s["name"] != server["name"]]
        if len(servers) >= MAX_SERVERS:
            raise ValueError(f"At most {MAX_SERVERS} MCP servers.")
        servers.append(server)
        _save(sorted(servers, key=lambda s: s["name"]))
    return server


def remove(name: str) -> None:
    with _file_lock:
        servers = load()
        if not any(s["name"] == name for s in servers):
            raise FileNotFoundError("MCP server not found.")
        _save([s for s in servers if s["name"] != name])
    clear_secrets(name)


def preset_server(preset: str, name: str | None = None) -> dict:
    """A validated, disabled server built from a preset (the caller saves it)."""
    if preset not in PRESETS:
        raise ValueError("Unknown preset.")
    template = {k: v for k, v in PRESETS[preset].items() if k not in {"label", "note"}}
    return validate({**template, "name": name or preset.removeprefix("atlassian-"), "preset": preset})


def for_stage(stage: str) -> list[dict]:
    return [s for s in load() if s["enabled"] and stage in s["stages"]]


# ---- secrets ----------------------------------------------------------------------------------------------------
_secrets: dict[str, dict[str, str]] = {}
_revoked: set[str] = (
    set()
)  # servers whose HARNESS_MCP_SECRET_* env values are ignored until new secrets are set
_secret_lock = threading.Lock()


def secret_env_name(server: str, key: str) -> str:
    # '-' becomes '__' so "a-b"+"C" and "a"+"B_C" cannot collide; same encoding in desktop/src/keystore.ts
    return f"{SECRET_ENV_PREFIX}{server.upper().replace('-', '__')}_{key}"


def secret_keys(server: dict) -> list[str]:
    return [BEARER_KEY] if server["transport"] == "http" else list(server["env_keys"])


def set_secrets(name: str, values: dict) -> None:
    server = get(name)
    allowed = set(secret_keys(server))
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError("Unknown secret name for this server.")
    clean = {}
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > 4000 or "\n" in value or "\r" in value:
            raise ValueError("A secret must be a single-line string of at most 4000 characters.")
        clean[key] = value.strip()
    with _secret_lock:
        if any(clean.values()):
            _revoked.discard(name)  # env values are trusted again only once new secrets are entered
        store = _secrets.setdefault(name, {})
        for key, value in clean.items():  # empty string clears
            if value:
                store[key] = value
            else:
                store.pop(key, None)


def clear_secrets(name: str) -> None:
    with _secret_lock:
        _secrets.pop(name, None)
        _revoked.add(name)


def get_secret(name: str, key: str) -> str:
    with _secret_lock:
        value = _secrets.get(name, {}).get(key, "")
        revoked = name in _revoked
    return value or ("" if revoked else os.environ.get(secret_env_name(name, key), "").strip())


def secret_status(server: dict) -> dict[str, bool]:
    return {key: bool(get_secret(server["name"], key)) for key in secret_keys(server)}


def secret_values() -> list[str]:
    """Every secret value known to this process (for redaction); only values of 8+ characters."""
    with _secret_lock:
        values = [v for store in _secrets.values() for v in store.values()]
    values += [v for k, v in os.environ.items() if k.startswith(SECRET_ENV_PREFIX)]
    return [v for v in values if len(v) >= 8]


# ---- MAF tools --------------------------------------------------------------------------------------------------
def resolve_command(command: str) -> str:
    """``npx`` is ``npx.cmd`` on Windows; ``which`` also finds absolute/relative paths."""
    return shutil.which(command) or command


def function_prefix(name: str) -> str:
    return "mcp_" + name.replace("-", "_")


def approval_mode(server: dict) -> dict[str, list[str]]:
    """MAF per-tool approval: allow-listed tools run, everything else that is exposed needs a human."""
    return {
        "never_require_approval": list(server["allowed_tools"]),
        "always_require_approval": list(server["approval_required_tools"]),
    }


def build_tool(server: dict, *, expose_all: bool = False, with_approval: bool = True):
    """An unconnected MAF ``MCPStdioTool`` / ``MCPStreamableHTTPTool``.

    Only listed tools are exposed (``allowed_tools``): a tool the server adds later, or one in neither list, is
    invisible to the model. ``expose_all`` is for the Test button only (list names, call nothing).
    """
    from agent_framework import MCPStdioTool, MCPStreamableHTTPTool

    # Without an approver an approval-required tool could only be refused: do not show it to the model at all.
    gated = server["approval_required_tools"] if with_approval else []
    listed = None if expose_all else [*server["allowed_tools"], *gated]
    common: dict[str, Any] = {
        "tool_name_prefix": function_prefix(server["name"]),
        "load_prompts": False,
        "request_timeout": 60,
        "allowed_tools": listed,
        "approval_mode": "always_require"
        if expose_all
        else {**approval_mode(server), "always_require_approval": list(gated)},
    }
    if server["transport"] == "stdio":
        missing = [k for k in server["env_keys"] if not get_secret(server["name"], k)]
        if missing:
            raise ValueError(f"Missing secret(s): {', '.join(missing)}")
        env = {k: get_secret(server["name"], k) for k in server["env_keys"]}
        return MCPStdioTool(
            server["name"],
            resolve_command(server["command"]),
            args=server["args"],
            env=env or None,
            **common,
        )
    token = get_secret(server["name"], BEARER_KEY)
    header = {} if not token else {"Authorization": token if " " in token else f"Bearer {token}"}
    return MCPStreamableHTTPTool(
        server["name"],
        server["url"],
        header_provider=(lambda _kwargs: dict(header)) if header else None,
        **common,
    )


def enforce_approvals(tool, server: dict) -> list[str]:
    """After connecting: anything exposed that is not explicitly auto-approved requires approval (defence in depth
    over MAF's default for an unlisted name). Returns the exposed function names."""
    allowed = set(server["allowed_tools"])
    names = []
    for fn in tool.functions:
        remote = (fn.additional_properties or {}).get("_mcp_remote_name") or fn.name
        if remote not in allowed:
            fn.approval_mode = "always_require"
        names.append(fn.name)
    return names


async def connect(tool) -> None:
    await asyncio.wait_for(tool.connect(), CONNECT_TIMEOUT_SECONDS)


async def probe(server: dict) -> dict:
    """Connect, list every tool the server offers (names only), disconnect. Never returns secrets."""
    tool = build_tool(server, expose_all=True)
    try:
        await connect(tool)
        names = sorted(f.additional_properties.get("_mcp_remote_name") or f.name for f in tool.functions)
    finally:
        try:
            await tool.close()
        except Exception:
            pass
    return {"tools": names, "count": len(names)}

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
import shlex
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
# Governance cap: tools a server may list (approved + approval-required). Listing is not sending: only
# MAX_MCP_TOOLS_PER_STAGE of them reach a stage, and a server over that budget is skipped in that stage.
MAX_LISTED_TOOLS = 200
MAX_MCP_TOOLS_PER_STAGE = (
    8  # runtime budget (agent/runtime.py); keep the stage tool budget math in tests true
)
MAX_DISCOVERED_TOOLS = 200  # catalogue stored from the last Test (names and descriptions only)
MAX_IMPORT_BYTES = 100_000
_PLACEHOLDER = re.compile(r"\$\{(input|env):[^}]*\}")  # VS Code variables: never a secret value
_SECRET_FLAG = re.compile(
    r"(?i)^--?(api[-_]?key|token|password|passwd|secret|authorization)$"
)  # value follows
_SECRET_QUERY = re.compile(
    r"(?i)(^|&)(api[_-]?key|access[_-]?token|token|key|secret|password|sig|signature)="
)
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\|(?:^|\s)\\\\[^\\\s]")  # drive letter or UNC path
CONNECT_TIMEOUT_SECONDS = 25  # below the UI proxy's 30 s limit
_SLUG = re.compile(r"[a-z][a-z0-9-]{0,31}")
# Route segments of the UI (/marketplace/mcp/new): a server with one of these names would have no page of its own.
RESERVED_NAMES = frozenset({"new"})
_ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
# Variables that change how a process loads code, resolves hosts or trusts certificates; a secret field must never set them.
_FORBIDDEN_PREFIXES = ("DYLD_", "LD_", "NODE_", "PYTHON", "NPM_CONFIG_", "GIT_", "_JAVA")
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
    "JDK_JAVA_OPTIONS",
    "CLASSPATH",
    "PERL5OPT",
    "RUBYLIB",
    "DOCKER_HOST",
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
    if name in RESERVED_NAMES:
        raise ValueError(f"'{name}' is reserved; choose another name.")
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
        raise ValueError(f"At most {MAX_LISTED_TOOLS} listed tools per server.")
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


_SECRET_MESSAGE = (
    "Arguments or the URL contain a secret-looking value. Move it to an environment variable "
    "(KEY=value before the command, or -e KEY for docker) or to the token field."
)


def _reject_inline_secrets(server: dict) -> None:
    """Client writes only (upsert, import): a stored server is loaded with a problem instead, never dropped."""
    if _inline_secret(server["args"], server["url"]):
        raise ValueError(_SECRET_MESSAGE)


def _looks_secret(text: str) -> bool:
    from .ui.presentation import public  # local: ui.presentation -> hooks.builtin -> this module (circular)

    return public(text) != text  # redaction changed it: it holds a credential-looking value


def _flag_secret(args: list[str]) -> bool:
    """--api-key/--token/--password/--secret/--authorization with a value, or --header carrying Authorization."""
    for i, arg in enumerate(args):
        name, eq, inline = arg.partition("=")
        value = inline if eq else (args[i + 1] if i + 1 < len(args) else "")
        if _SECRET_FLAG.match(name) and value:
            return True
        if name.lower() == "--header" and "authorization" in value.lower():
            return True
    return False


def _inline_secret(args: list[str], url: str) -> bool:
    if _flag_secret(args) or any(_looks_secret(a) for a in args):
        return True
    if url:
        query = urlsplit(url).query
        return bool((query and _SECRET_QUERY.search(query)) or _looks_secret(url))
    return False


# ---- storage ----------------------------------------------------------------------------------------------------
_file_lock = threading.Lock()


def config_path():
    return paths.user_data_root() / "mcp_servers.json"


def _tool_catalog(items: Any) -> list[dict]:
    """Stored tool catalogue: names matching the tool-name pattern, descriptions at most 300 characters, and the
    server's MCP hints (readOnlyHint, destructiveHint) when it sent them. Entries from older files lack the hints."""
    out: list[dict] = []
    for item in items if isinstance(items, list) else []:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and _TOOL_NAME.fullmatch(name):
            desc = item.get("description")
            entry: dict[str, Any] = {"name": name, "description": desc[:300] if isinstance(desc, str) else ""}
            for hint in ("read_only", "destructive"):
                if isinstance(item.get(hint), bool):
                    entry[hint] = item[hint]
            out.append(entry)
        if len(out) == MAX_DISCOVERED_TOOLS:
            break
    return out


def load() -> list[dict]:
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
        servers = raw["servers"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    out = []
    for item in servers if isinstance(servers, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            server = validate({k: v for k, v in item.items() if k not in _STORED_ONLY})
        except ValueError:
            continue  # a hand-edited bad entry is ignored, never trusted
        problems = _stored_problems(server)
        out.append(
            {
                **server,
                "enabled": server["enabled"] and not problems,  # a flagged server stays off until fixed
                "discovered_tools": _tool_catalog(item.get("discovered_tools")),  # absent in old files
                "problems": problems,
            }
        )
    return out


def _stored_problems(server: dict) -> list[str]:
    """Read-only notes for a stored server; the save check enforces the same rule on any new value."""
    return (
        ["Move the token in the arguments to a secret before enabling."]
        if _inline_secret(server["args"], server["url"])
        else []
    )


def _save(servers: list[dict]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(
        path, {"servers": [{k: v for k, v in s.items() if k != "problems"} for s in servers]}
    )  # a view field


def get(name: str) -> dict:
    for server in load():
        if server["name"] == name:
            return server
    raise FileNotFoundError("MCP server not found.")


def _save_servers(incoming: list[dict]) -> list[dict]:
    """Add or replace validated servers in one write. Stored tools survive unless the target changed. Caller holds _file_lock."""
    current = {s["name"]: s for s in load()}
    if len(current.keys() | {s["name"] for s in incoming}) > MAX_SERVERS:
        raise ValueError(f"At most {MAX_SERVERS} MCP servers.")
    for server in incoming:
        old = current.get(server["name"])
        changed = old is not None and any(old[k] != server[k] for k in ("url", "transport", "command"))
        if changed:
            clear_secrets(server["name"])  # a secret never follows a server to a different target
        stale = old is not None and any(
            old[k] != server[k] for k in ("url", "transport", "command", "args", "env_keys")
        )
        server["discovered_tools"] = old["discovered_tools"] if old and not stale else []
        current[server["name"]] = server
    _save(sorted(current.values(), key=lambda s: s["name"]))
    return incoming


def upsert(data: dict) -> dict:
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k != "discovered_tools"}  # written only by Test
    server = validate(data)
    _reject_inline_secrets(server)
    with _file_lock:
        return _save_servers([server])[0]


def save_many(servers: list[dict]) -> list[dict]:
    """Import: all or nothing, one file write."""
    with _file_lock:
        return _save_servers(servers)


def set_discovered(name: str, catalog: list[dict], probed: dict | None = None) -> bool:
    """Persist the tool catalogue of a successful Test. With ``probed``, only if the stored definition still matches
    what was probed (a catalogue of an old target is dropped). Returns whether it was stored."""
    with _file_lock:
        servers = load()
        target = [s for s in servers if s["name"] == name]
        if not target or (probed and any(target[0][k] != probed[k] for k in _TARGET_KEYS)):
            return False
        target[0]["discovered_tools"] = _tool_catalog(catalog)
        _save(servers)
        return True


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


def stage_budget(servers: list[dict]) -> dict[str, dict]:
    """Stages whose enabled servers list more MCP tools than one stage may use, in the order the runtime takes them.
    Counts approved + approval-required tools (an upper bound: without an approver the runtime exposes fewer)."""
    out: dict[str, dict] = {}
    for stage in STAGES:
        demand, used, skipped = 0, 0, []
        for s in servers:
            if not (s["enabled"] and stage in s["stages"]):
                continue
            n = len(set(s["allowed_tools"]) | set(s["approval_required_tools"]))
            demand += n
            if used + n > MAX_MCP_TOOLS_PER_STAGE:
                skipped.append(s["name"])
            else:
                used += n
        if skipped:
            out[stage] = {"tools": demand, "max": MAX_MCP_TOOLS_PER_STAGE, "skipped": skipped}
    return out


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


def _catalog_entry(fn) -> dict:
    from .ui.presentation import public  # local: ui.presentation -> hooks.builtin -> this module (circular)

    remote = (fn.additional_properties or {}).get("_mcp_remote_name") or fn.name
    return {
        "name": remote,
        "description": public(str(getattr(fn, "description", "") or ""))[:300],
    }  # redact, then cap


async def _hints(session: Any) -> dict[str, dict]:
    """readOnlyHint and destructiveHint per tool name, from the server's own tools/list (the framework drops them).
    Only booleans are kept; a hint the server did not send is absent, never guessed."""
    from mcp import types

    out: dict[str, dict] = {}
    cursor = None
    for _ in range(50):  # pages: a server listing more than this is truncated, as the catalogue is
        page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
        for item in page.tools:
            annotations = item.annotations
            flags = {}
            if annotations is not None:
                if isinstance(annotations.readOnlyHint, bool):
                    flags["read_only"] = annotations.readOnlyHint
                if isinstance(annotations.destructiveHint, bool):
                    flags["destructive"] = annotations.destructiveHint
            if flags:
                out[item.name] = flags
        cursor = page.nextCursor
        if not cursor:
            break
    return out


async def probe(server: dict) -> dict:
    """Connect, list every tool the server offers (names, redacted descriptions and MCP hints), disconnect.
    Never returns secrets."""
    tool = build_tool(server, expose_all=True)
    try:
        await connect(tool)
        session = getattr(tool, "session", None)
        try:
            hints = await _hints(session) if session is not None else {}
        except Exception:  # hints are optional: a failed tools/list page must not fail the whole Test
            hints = {}
        entries = [_catalog_entry(f) for f in tool.functions]
        catalog = sorted(
            ({**e, **hints.get(e["name"], {})} for e in entries),
            key=lambda t: t["name"],
        )
    finally:
        try:
            await tool.close()
        except Exception:
            pass
    return {"tools": [t["name"] for t in catalog], "count": len(catalog), "catalog": catalog}


# ---- import: a pasted command or MCP JSON config --------------------------------------------------------------------
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.S)
_STORED_ONLY = {
    "discovered_tools",
    "problems",
}  # kept in the file or the view, never validated as definition fields
_TARGET_KEYS = ("transport", "command", "args", "url")  # a Test catalogue belongs to this target only
_IMPORT_FIELDS = {"command", "args", "env", "type", "transport", "url", "headers", "disabled"}


def _slug(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")
    if not slug or not slug[0].isalpha():
        slug = f"mcp-{slug}"
    return slug[:32].rstrip("-")


def _unique(name: str, taken: set[str]) -> str:
    candidate, n = name, 2
    while candidate in taken or candidate in RESERVED_NAMES:
        suffix = f"-{n}"
        candidate = f"{name[: 32 - len(suffix)].rstrip('-')}{suffix}"
        n += 1
    taken.add(candidate)
    return candidate


def _package_name(command: str, args: list[str]) -> str:
    """The package a launcher runs (`@scope/server-x@latest` -> `server-x`, `mcp/fetch` -> `fetch`); else the program name."""
    for token in args:
        if token.startswith("-") or token.lower() in {"run", "x", "dlx", "exec", "tool"}:
            continue
        if "\\" in token or token.startswith((".", "/")):
            break  # a path argument is not a package
        return re.split(r"[@:]", re.split(r"/", token)[-1])[0]
    return re.sub(r"\.(cmd|exe)$", "", re.split(r"[\\/]", command)[-1], flags=re.IGNORECASE)


def _command_entry(text: str) -> dict:
    text = re.sub(
        r"(?:\\|\^|`)[ \t]*\r?\n", " ", text
    )  # line continuations: \ (POSIX), ^ (cmd), ` (PowerShell)
    try:
        if _WINDOWS_PATH.search(text):  # keep backslashes; quotes are stripped per token below
            tokens = [
                t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t
                for t in shlex.split(text, posix=False)
            ]
        elif "\\" in text and '"' not in text and "'" not in text:
            tokens = text.split()
        else:
            tokens = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValueError("The command has unbalanced quotes.") from exc
    env: dict[str, str] = {}
    while tokens and _ENV_ASSIGNMENT.fullmatch(tokens[0]):
        key, _, value = tokens.pop(0).partition("=")
        env[key] = value  # a leading KEY=value is a secret: its value is never stored or returned
    if not tokens:
        raise ValueError("Paste a command such as npx, uvx or docker, or an MCP JSON config.")
    return {"command": tokens[0], "args": tokens[1:], "env": env}


def _docker_env(command: str, args: list[str], env: dict[str, str]) -> list[str]:
    """docker/podman ``-e KEY=value``, ``--env KEY=value`` or ``--env=KEY=value``: the value becomes the secret KEY and
    the argument becomes ``-e KEY``, so docker passes the value through from the environment."""
    if re.split(r"[\\/]", command)[-1].lower().removesuffix(".exe") not in {"docker", "podman"}:
        return args
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in {"-e", "--env"} and i + 1 < len(args) and "=" in args[i + 1]:
            key, _, value = args[i + 1].partition("=")
            env[key] = value
            out += ["-e", key]
            i += 2
            continue
        if a.startswith("--env=") and "=" in a[6:]:
            key, _, value = a[6:].partition("=")
            env[key] = value
            out += ["-e", key]
        else:
            out.append(a)
        i += 1
    return out


def _is_server(data: Any) -> bool:
    return isinstance(data, dict) and any(k in data for k in ("command", "url", "type"))


def _json_entries(text: str) -> list[tuple[str, dict]]:
    try:
        data = json.loads(text)  # the error text names a position only, never the content
    except ValueError:
        raise ValueError("That is not valid JSON. Paste the MCP config as your client shows it.") from None
    if not isinstance(data, dict):
        raise ValueError("The JSON must be an object of MCP server entries.")
    if "mcpServers" in data:
        block = data["mcpServers"]
    elif isinstance(data.get("servers"), dict) and not _is_server(data["servers"]):  # VS Code
        block = data["servers"]
    elif _is_server(data):
        return [("", data)]
    else:
        block = {k: v for k, v in data.items() if k != "inputs"}
    if not isinstance(block, dict) or not block:
        raise ValueError("No MCP servers found in the JSON.")
    for key, entry in block.items():
        if not isinstance(entry, dict):
            raise ValueError(f"Server '{key}' must be an object.")
    return [(str(key), entry) for key, entry in block.items()]


def _from_entry(source_key: str, entry: dict, taken: set[str]) -> dict:
    shown = source_key or "This server"
    unknown = set(entry) - _IMPORT_FIELDS
    if unknown:
        raise ValueError(f"{shown}: unsupported field {', '.join(sorted(unknown))}.")
    kind = str(
        entry.get("type") or entry.get("transport") or ("stdio" if "command" in entry else "http")
    ).lower()
    if kind == "streamable-http":
        kind = "http"
    if kind == "sse":
        raise ValueError(f"{shown}: SSE servers are not supported; only stdio and streamable HTTP are.")
    if kind not in {"stdio", "http"}:
        raise ValueError(f"{shown}: unsupported server type; only stdio and streamable HTTP are.")
    headers = entry.get("headers") or {}
    if not isinstance(headers, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
    ):
        raise ValueError(f"{shown}: headers must map names to text values.")
    if kind == "stdio":
        command, args, env = entry.get("command"), entry.get("args", []), dict(entry.get("env") or {})
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"{shown}: a stdio server needs a command.")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError(f"{shown}: args must be a list of text values.")
        if not isinstance(entry.get("env") or {}, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in (entry.get("env") or {}).items()
        ):
            raise ValueError(f"{shown}: env must map names to text values.")
        if headers:
            raise ValueError(f"{shown}: headers apply to HTTP servers only.")
        command = command.strip()
        args = _docker_env(command, args, env)
        fields = {"transport": "stdio", "command": command, "args": args, "env_keys": list(env)}
        # VS Code placeholders (${input:x}, ${env:x}) are not values: the key stays listed, unset (needs secrets)
        secret_keys = [k for k, v in env.items() if not _PLACEHOLDER.fullmatch(v.strip())]
        package = _package_name(command, args)
    else:
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"{shown}: an HTTP server needs a url.")
        if "env" in entry:
            raise ValueError(f"{shown}: env applies to stdio servers only.")
        if any(k.lower() != "authorization" for k in headers):
            raise ValueError(f"{shown}: only an Authorization header is supported (it becomes the token).")
        auth = next((v for k, v in headers.items() if k.lower() == "authorization"), None)
        fields = {"transport": "http", "url": url.strip()}
        secret_keys = [BEARER_KEY] if auth is not None and not _PLACEHOLDER.fullmatch(auth.strip()) else []
        package = urlsplit(url.strip()).hostname or "mcp"
    base = _slug(source_key) if source_key else _slug(package)
    try:
        server = validate(
            {
                "name": _unique(base, taken),
                "enabled": False,
                "stages": [],
                "allowed_tools": [],
                "approval_required_tools": [],
                "preset": "",
                **fields,
            }
        )
        _reject_inline_secrets(server)
    except ValueError as exc:
        raise ValueError(f"{shown}: {exc}") from None
    return {"source_key": source_key, "server": server, "secret_keys": secret_keys}


def parse_import(text: str) -> list[dict]:
    """Validated, disabled servers for a pasted command or MCP JSON. Secret values are never returned: each result
    lists only the secret NAMES (env keys; BEARER_TOKEN for an Authorization header)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Paste an npx, uvx or docker command, or an MCP JSON config.")
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ValueError("The pasted text is larger than 100 kB.")
    body = text.strip()
    entries = _json_entries(body) if body[0] in "{[" else [("", _command_entry(body))]
    if len(entries) > MAX_SERVERS:
        raise ValueError(f"At most {MAX_SERVERS} servers per import.")
    taken = {s["name"] for s in load()}
    return [_from_entry(key, entry, taken) for key, entry in entries]

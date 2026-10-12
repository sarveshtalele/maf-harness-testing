"""MCP servers: config validation, presets, secrets, runtime wiring (MAF tool classes mocked), API."""

import asyncio
import json
import threading
from pathlib import Path

import pytest

from maf_test_harness import mcp_servers
from maf_test_harness.agent.runtime import MAFHarnessRuntime
from maf_test_harness.hooks.builtin import redact
from maf_test_harness.monitoring.trace import tool_category
from maf_test_harness.ui.api import LocalAPI
from maf_test_harness.workflow import STAGES

SECRET = "tok-0123456789-abcdef"


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "data"))
    for name in list(__import__("os").environ):
        if name.startswith(mcp_servers.SECRET_ENV_PREFIX):
            monkeypatch.delenv(name)
    mcp_servers._secrets.clear()
    yield
    mcp_servers._secrets.clear()


def srv(**kw):
    base = {"name": "pw", "transport": "stdio", "command": "npx", "args": ["x"], "enabled": True}
    return {**base, **kw}


def test_stages_match_workflow():
    assert set(mcp_servers.STAGES) <= set(STAGES)  # repo_analysis is host-only (no model, no tools)


@pytest.mark.parametrize(
    "bad",
    [
        srv(name="Bad Name"),
        srv(name="1abc"),
        srv(transport="ws"),
        srv(command=""),
        srv(stages=["nope"]),
        srv(token=SECRET),  # a pasted secret is rejected, not stored
        srv(env_keys=["lower"]),
        srv(env_keys=["PATH"]),
        srv(env_keys=["NODE_OPTIONS"]),
        srv(env_keys=["LD_PRELOAD"]),
        srv(env_keys=["DYLD_INSERT_LIBRARIES"]),
        srv(env_keys=["PYTHONPATH"]),
        srv(env_keys=["NPM_CONFIG_REGISTRY"]),
        srv(env_keys=["HTTPS_PROXY"]),
        srv(env_keys=["BASH_ENV"]),
        srv(env_keys=["SSL_CERT_FILE"]),
        srv(env_keys=["JAVA_TOOL_OPTIONS"]),
        srv(allowed_tools=["a"], approval_required_tools=["a"]),
        srv(allowed_tools=[f"t{i}" for i in range(201)]),
        {"name": "h", "transport": "http", "url": "http://example.com/mcp"},
        {"name": "h", "transport": "http", "url": "https://u:p@example.com/mcp"},
        {"name": "h", "transport": "http", "url": "ftp://example.com"},
    ],
)
def test_validation_rejects(bad):
    with pytest.raises(ValueError):
        mcp_servers.validate(bad)


def test_http_localhost_allowed_and_defaults():
    s = mcp_servers.validate({"name": "loc", "transport": "http", "url": "http://localhost:8080/mcp"})
    assert s["enabled"] is False and s["stages"] == [] and s["allowed_tools"] == []


def test_no_secrets_in_json_file():
    mcp_servers.upsert(srv(env_keys=["API_TOKEN"]))
    mcp_servers.set_secrets("pw", {"API_TOKEN": SECRET})
    assert SECRET not in mcp_servers.config_path().read_text()
    assert mcp_servers.secret_status(mcp_servers.get("pw")) == {"API_TOKEN": True}
    with pytest.raises(ValueError):
        mcp_servers.set_secrets("pw", {"OTHER": "x"})
    mcp_servers.remove("pw")
    assert mcp_servers.secret_values() == []


def test_secret_from_env_matches_desktop_naming(monkeypatch):
    mcp_servers.upsert(
        {"name": "atlassian-jira", "transport": "http", "url": "https://mcp.atlassian.com/v2/mcp"}
    )
    monkeypatch.setenv("HARNESS_MCP_SECRET_ATLASSIAN__JIRA_BEARER_TOKEN", SECRET)
    assert mcp_servers.get_secret("atlassian-jira", "BEARER_TOKEN") == SECRET


@pytest.mark.parametrize("key", list(mcp_servers.PRESETS))
def test_presets_expand_and_are_safe(key):
    s = mcp_servers.preset_server(key)
    assert s["enabled"] is False and s["preset"] == key
    assert not set(s["allowed_tools"]) & set(s["approval_required_tools"])


def test_playwright_navigation_requires_approval():
    s = mcp_servers.preset_server("playwright")
    assert s["command"] == "npx" and s["args"] == ["@playwright/mcp@latest"]
    assert "browser_navigate" in s["approval_required_tools"] and "browser_navigate" not in s["allowed_tools"]
    assert "browser_snapshot" in s["allowed_tools"]


def test_jira_preset_is_https_and_reads_auto_writes_gated():
    s = mcp_servers.preset_server("atlassian-jira-cloud")
    assert s["transport"] == "http" and s["url"].startswith("https://")
    assert "createJiraIssue" in s["approval_required_tools"]


def test_build_tool_approval_mapping_and_allowlist():
    s = mcp_servers.validate(
        srv(allowed_tools=["browser_snapshot"], approval_required_tools=["browser_navigate"])
    )
    tool = mcp_servers.build_tool(s)
    assert type(tool).__name__ == "MCPStdioTool"
    assert set(tool.allowed_tools) == {"browser_snapshot", "browser_navigate"}
    assert tool.approval_mode == {
        "never_require_approval": ["browser_snapshot"],
        "always_require_approval": ["browser_navigate"],
    }
    assert tool.name == "pw" and tool.tool_name_prefix == "mcp_pw"
    # nothing listed -> nothing exposed
    assert mcp_servers.build_tool(mcp_servers.validate(srv())).allowed_tools == []


def test_build_tool_secrets_become_env_only_and_missing_is_error():
    s = mcp_servers.validate(srv(env_keys=["API_TOKEN"]))
    with pytest.raises(ValueError, match="Missing secret"):
        mcp_servers.build_tool(s)
    mcp_servers.upsert(s)
    mcp_servers.set_secrets("pw", {"API_TOKEN": SECRET})
    assert mcp_servers.build_tool(s).env == {"API_TOKEN": SECRET}


def test_http_tool_bearer_header():
    mcp_servers.upsert({"name": "j", "transport": "http", "url": "https://mcp.example.com/mcp"})
    mcp_servers.set_secrets("j", {"BEARER_TOKEN": SECRET})
    tool = mcp_servers.build_tool(mcp_servers.get("j"))
    assert type(tool).__name__ == "MCPStreamableHTTPTool"
    assert tool._header_provider({}) == {"Authorization": f"Bearer {SECRET}"}
    mcp_servers.set_secrets("j", {"BEARER_TOKEN": "Basic abcdefghijkl"})
    assert mcp_servers.build_tool(mcp_servers.get("j"))._header_provider({}) == {
        "Authorization": "Basic abcdefghijkl"
    }


def test_redaction_covers_mcp_secrets(monkeypatch):
    mcp_servers.upsert(srv(env_keys=["API_TOKEN"]))
    mcp_servers.set_secrets("pw", {"API_TOKEN": SECRET})
    assert SECRET not in redact(f"failed with {SECRET}")[0]
    monkeypatch.setenv("HARNESS_MCP_SECRET_X_Y", "env-secret-98765432")
    assert "env-secret-98765432" not in redact("x env-secret-98765432")[0]


# ---- runtime ----------------------------------------------------------------------------------------------------
class FakeFn:
    def __init__(self, name):
        self.name, self.additional_properties, self.approval_mode = (
            f"mcp_x_{name}",
            {"_mcp_remote_name": name},
            None,
        )


class FakeTool:
    def __init__(self, server, names, fail=False):
        self.server, self.fail, self.closed = server, fail, False
        self.functions = [FakeFn(n) for n in names]

    async def connect(self):
        if self.fail:
            raise ConnectionError(f"cannot reach with {SECRET}")

    async def close(self):
        self.closed = True


@pytest.fixture
def runtime(monkeypatch):
    rt = MAFHarnessRuntime(api_key="k", model="m", skills_root=Path("skills"))
    return rt


def fake_builder(monkeypatch, names=("a", "b"), fail_for=()):
    built = []

    def build(server, **_):
        t = FakeTool(server, names, fail=server["name"] in fail_for)
        built.append(t)
        return t

    monkeypatch.setattr(mcp_servers, "build_tool", build)
    return built


def test_runtime_only_enabled_servers_for_matching_stage(runtime, monkeypatch):
    mcp_servers.upsert(srv(name="on", stages=["requirements"], allowed_tools=["a"]))
    mcp_servers.upsert(srv(name="off", enabled=False, stages=["requirements"], allowed_tools=["a"]))
    mcp_servers.upsert(srv(name="other", stages=["test_design"], allowed_tools=["a"]))
    built = fake_builder(monkeypatch)
    state: dict = {}
    tools = asyncio.run(runtime._mcp_tools("requirements", state))
    assert [t.server["name"] for t in tools] == ["on"] and len(built) == 1
    assert asyncio.run(runtime._mcp_tools("test_planning", state)) == []
    assert runtime._mcp_names == {"mcp_x_a", "mcp_x_b"}
    assert tool_category("mcp_x_a", ("find_symbol", *runtime._mcp_names)) == "mcp"
    asyncio.run(runtime.close())
    assert built[0].closed


def test_unlisted_exposed_tool_is_forced_to_require_approval(runtime, monkeypatch):
    mcp_servers.upsert(srv(stages=["requirements"], allowed_tools=["a"], approval_required_tools=["b"]))
    runtime.mcp_approver = lambda stage, calls: [False] * len(calls)
    fake_builder(monkeypatch)
    (tool,) = asyncio.run(runtime._mcp_tools("requirements", {}))
    modes = {f.additional_properties["_mcp_remote_name"]: f.approval_mode for f in tool.functions}
    assert modes == {"a": None, "b": "always_require"}  # allow-listed stays as MAF set it; others are gated


def test_connection_failure_is_tolerated_and_noted_redacted(runtime, monkeypatch):
    mcp_servers.upsert(srv(name="good", stages=["requirements"], allowed_tools=["a"]))
    mcp_servers.upsert(srv(name="bad", stages=["requirements"], allowed_tools=["a"], env_keys=["API_TOKEN"]))
    mcp_servers.set_secrets("bad", {"API_TOKEN": SECRET})
    built = fake_builder(monkeypatch, fail_for=("bad",))
    state: dict = {}
    tools = asyncio.run(runtime._mcp_tools("requirements", state))
    assert [t.server["name"] for t in tools] == ["good"]
    (note,) = state["mcp_unavailable"]
    assert note["server"] == "bad" and "ConnectionError" in note["error"] and SECRET not in json.dumps(state)
    assert next(t for t in built if t.fail).closed


def test_missing_secret_skips_server(runtime):
    mcp_servers.upsert(srv(stages=["requirements"], allowed_tools=["a"], env_keys=["API_TOKEN"]))
    state: dict = {}
    assert asyncio.run(runtime._mcp_tools("requirements", state)) == []
    assert "Missing secret" in state["mcp_unavailable"][0]["error"]


def test_stage_tool_budget_caps_mcp_tools(runtime, monkeypatch):
    mcp_servers.upsert(srv(name="s1", stages=["requirements"], allowed_tools=["a"]))
    mcp_servers.upsert(srv(name="s2", stages=["requirements"], allowed_tools=["a"]))
    fake_builder(monkeypatch, names=[f"t{i}" for i in range(5)])
    state: dict = {}
    tools = asyncio.run(runtime._mcp_tools("requirements", state))
    assert len(tools) == 1 and "budget" in state["mcp_unavailable"][0]["error"]
    from maf_test_harness.tools.agent_tools import STAGE_TOOLS

    worst = max(len(v) for v in STAGE_TOOLS.values())
    assert (
        worst + mcp_servers.MAX_MCP_TOOLS_PER_STAGE + 3 <= 20
    )  # + load_skill/read_skill_resource/run_skill_script


class _Req:
    def __init__(self, name, arguments='{"url": "https://x"}'):
        self.function_call = type("C", (), {"name": name, "arguments": arguments})()
        self.answers = []

    def to_function_approval_response(self, ok):
        self.answers.append(ok)
        return f"resp:{ok}"


class _Result:
    def __init__(self, reqs):
        self.user_input_requests = reqs


def _rt_with_replies(runtime):
    sent = []

    async def fake_run(agent, prompt, **kw):
        sent.append(prompt)
        return _Result([])

    runtime._run_agent = fake_run
    runtime._mcp_names = {"mcp_pw_browser_navigate"}
    return sent


def test_no_approver_answers_refusal_so_the_model_continues(runtime):
    sent = _rt_with_replies(runtime)
    req = _Req("mcp_pw_browser_navigate")
    out = asyncio.run(runtime._resolve_mcp_approvals("requirements", None, None, _Result([req]), {}, {}))
    assert out.user_input_requests == [] and req.answers == [False] and len(sent) == 1
    foreign = _Req("run_skill_script")  # never auto-approved, but also answered with a refusal
    asyncio.run(runtime._resolve_mcp_approvals("requirements", None, None, _Result([foreign]), {}, {}))
    assert foreign.answers == [False]


def test_approver_decides_and_malformed_arguments_are_refused(runtime):
    _rt_with_replies(runtime)
    seen = []
    runtime.mcp_approver = lambda stage, calls: seen.append(calls) or [True]
    ok = _Req("mcp_pw_browser_navigate")
    asyncio.run(runtime._resolve_mcp_approvals("requirements", None, None, _Result([ok]), {}, {}))
    assert ok.answers == [True] and seen[0][0]["tool"] == "mcp_pw_browser_navigate"
    for bad in ("{not json", "[1, 2]"):
        req = _Req("mcp_pw_browser_navigate", bad)
        asyncio.run(runtime._resolve_mcp_approvals("requirements", None, None, _Result([req]), {}, {}))
        assert req.answers == [False]
    assert len(seen) == 1  # the approver was never asked about the malformed ones


def test_approval_required_tools_hidden_without_approver(runtime, monkeypatch):
    mcp_servers.upsert(srv(stages=["requirements"], allowed_tools=["a"], approval_required_tools=["b"]))
    seen = []
    real_build = mcp_servers.build_tool
    monkeypatch.setattr(
        mcp_servers, "build_tool", lambda server, **kw: seen.append(kw) or FakeTool(server, ["a"])
    )
    state: dict = {}
    asyncio.run(runtime._mcp_tools("requirements", state))
    assert seen == [{"with_approval": False}] and state["mcp_approval_hidden"][0]["tools"] == ["b"]
    monkeypatch.setattr(mcp_servers, "build_tool", real_build)
    tool = mcp_servers.build_tool(mcp_servers.get("pw"), with_approval=False)
    assert tool.allowed_tools == ["a"] and tool.approval_mode["always_require_approval"] == []
    tool = mcp_servers.build_tool(mcp_servers.get("pw"), with_approval=True)
    assert set(tool.allowed_tools) == {"a", "b"}


def test_cancel_during_connect_closes_tool_and_close_stage_disconnects(runtime, monkeypatch):
    mcp_servers.upsert(srv(stages=["requirements"], allowed_tools=["a"]))
    built = []

    class Hang(FakeTool):
        async def connect(self):
            await asyncio.sleep(60)

    monkeypatch.setattr(
        mcp_servers, "build_tool", lambda server, **kw: built.append(Hang(server, ["a"])) or built[-1]
    )

    async def go():
        task = asyncio.create_task(runtime._mcp_tools("requirements", {}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert built[0].closed
    fake_builder(monkeypatch)
    asyncio.run(runtime._mcp_tools("requirements", {}))
    assert runtime._mcp["requirements"]
    asyncio.run(runtime.close_stage("requirements"))
    assert "requirements" not in runtime._mcp


def test_secrets_revoked_on_delete_and_target_change(monkeypatch):
    mcp_servers.upsert({"name": "j", "transport": "http", "url": "https://a.example.com/mcp"})
    monkeypatch.setenv("HARNESS_MCP_SECRET_J_BEARER_TOKEN", SECRET)
    assert mcp_servers.get_secret("j", "BEARER_TOKEN") == SECRET
    mcp_servers.set_secrets("j", {"BEARER_TOKEN": "memory-secret-1234"})
    mcp_servers.upsert(
        {"name": "j", "transport": "http", "url": "https://a.example.com/mcp", "enabled": True}
    )
    assert mcp_servers.get_secret("j", "BEARER_TOKEN") == "memory-secret-1234"  # same target: kept
    mcp_servers.upsert({"name": "j", "transport": "http", "url": "https://evil.example.com/mcp"})
    assert mcp_servers.get_secret("j", "BEARER_TOKEN") == ""  # new target: memory dropped, env ignored
    mcp_servers.set_secrets("j", {"BEARER_TOKEN": "fresh-secret-12345"})
    assert mcp_servers.get_secret("j", "BEARER_TOKEN") == "fresh-secret-12345"
    mcp_servers.remove("j")
    mcp_servers.upsert({"name": "j", "transport": "http", "url": "https://evil.example.com/mcp"})
    assert mcp_servers.get_secret("j", "BEARER_TOKEN") == ""  # a deleted server's env value stays ignored


def test_secret_env_names_cannot_collide():
    assert mcp_servers.secret_env_name("a-b", "C") == "HARNESS_MCP_SECRET_A__B_C"
    assert mcp_servers.secret_env_name("a", "B_C") == "HARNESS_MCP_SECRET_A_B_C"


# ---- API --------------------------------------------------------------------------------------------------------
@pytest.fixture
def api(tmp_path):
    a = LocalAPI(tmp_path / "runs")
    yield a
    a.close()


def test_api_roundtrip_never_returns_secrets(api):
    out = api.mcp_save(srv(env_keys=["API_TOKEN"]))
    assert out["secrets"] == {"API_TOKEN": False} and out["ready"] is False
    out = api.mcp_set_secrets("pw", {"API_TOKEN": SECRET})
    assert out["ready"] is True
    listing = api.mcp_list()
    assert SECRET not in json.dumps(listing) and set(listing["presets"]) == set(mcp_servers.PRESETS)
    assert api.mcp_list()["servers"][0]["name"] == "pw"
    assert api.mcp_delete("pw") == {"deleted": "pw"}
    with pytest.raises(FileNotFoundError):
        api.mcp_delete("pw")


def test_api_mutations_conflict_while_busy(api, monkeypatch):
    api.mcp_save(srv(env_keys=["API_TOKEN"]))
    monkeypatch.setattr(api, "busy", lambda: True)
    for call in (
        lambda: api.mcp_test("pw"),
        lambda: api.mcp_save(srv(name="other")),
        lambda: api.mcp_delete("pw"),
        lambda: api.mcp_set_secrets("pw", {"API_TOKEN": SECRET}),
    ):
        with pytest.raises(RuntimeError):
            call()


def test_api_test_endpoint_lists_tools_without_secrets(api):
    api.mcp_save(srv())

    async def ok(server):
        return {"tools": ["browser_snapshot", "browser_click"], "count": 2}

    api.mcp_probe = ok
    assert api.mcp_test("pw") == {"ok": True, "tools": ["browser_snapshot", "browser_click"], "count": 2}

    async def boom(server):
        raise RuntimeError(f"401 {SECRET}")

    mcp_servers.upsert(srv(env_keys=["API_TOKEN"]))
    mcp_servers.set_secrets("pw", {"API_TOKEN": SECRET})
    api.mcp_probe = boom
    result = api.mcp_test("pw")
    assert result["ok"] is False and SECRET not in json.dumps(result)


# ---- greenfield Jira via MCP ------------------------------------------------------------------------------------
def test_jira_via_mcp_maps_issues_and_requires_read_only_tool():
    from maf_test_harness.repo.specs import ingest_specs

    mcp_servers.upsert({**mcp_servers.preset_server("atlassian-jira-cloud", "jira"), "enabled": True})
    issues = {"issues": [{"key": "P-1", "fields": {"summary": "Login", "description": None}}]}
    calls = []

    def call(server, tool, args):
        calls.append((tool, args))
        return json.dumps(issues)

    out = ingest_specs(None, None, None, {"server": "jira", "issue_keys": ["P-1"]}, call)
    (section,) = out["sections"]
    assert section["source"] == {"jira_key": "P-1"} and calls[0][1]["jql"].startswith("key in (P-1)")
    assert out["sources"][0]["via"] == "mcp:jira"
    with pytest.raises(ValueError, match="auto-approved"):
        ingest_specs(None, None, None, {"server": "jira", "jql": "x", "tool": "createJiraIssue"}, call)


# ---- real MAF + MCP over stdio (local python server, no network) -----------------------------------------------
_SERVER = """
from mcp.server.fastmcp import FastMCP
m = FastMCP("fx")
@m.tool()
def read_thing(x: str) -> str:
    return "read " + x
@m.tool()
def change_thing(x: str) -> str:
    return "changed " + x
@m.tool()
def secret_extra() -> str:
    return "never exposed"
m.run("stdio")
"""


def _real_server(tmp_path, **kw):
    script = tmp_path / "fx_server.py"
    script.write_text(_SERVER)
    import sys

    return srv(name="fx", command=sys.executable, args=[str(script)], **kw)


def test_real_stdio_probe_and_stage_tools(tmp_path):
    cfg = _real_server(
        tmp_path,
        stages=["requirements"],
        allowed_tools=["read_thing"],
        approval_required_tools=["change_thing"],
    )
    mcp_servers.upsert(cfg)
    assert asyncio.run(mcp_servers.probe(mcp_servers.get("fx")))["tools"] == [
        "change_thing",
        "read_thing",
        "secret_extra",
    ]
    rt = MAFHarnessRuntime(api_key="k", model="m", skills_root=Path("skills"))
    rt.mcp_approver = lambda stage, calls: [False] * len(calls)

    async def go():
        (tool,) = await rt._mcp_tools("requirements", {})
        modes = {f.name: f.approval_mode for f in tool.functions}
        out = await tool.call_tool("read_thing", x="1")
        await rt.close()
        return modes, out

    modes, out = asyncio.run(go())
    assert modes == {"mcp_fx_read_thing": "never_require", "mcp_fx_change_thing": "always_require"}
    rt2 = MAFHarnessRuntime(
        api_key="k", model="m", skills_root=Path("skills")
    )  # no approver: gated tool hidden
    (only,) = asyncio.run(_stage_tools(rt2))
    assert [f.name for f in only.functions] == ["mcp_fx_read_thing"]
    assert "read 1" in "".join(getattr(c, "text", "") or "" for c in out)


async def _stage_tools(rt):
    tools = await rt._mcp_tools("requirements", {})
    await rt.close()
    return tools


def test_jira_cloud_id_is_validated_and_free_arguments_ignored():
    from maf_test_harness.repo.specs import ingest_specs
    from maf_test_harness.run_options import parse_run_options

    mcp_servers.upsert({**mcp_servers.preset_server("atlassian-jira-cloud", "jira"), "enabled": True})
    calls = []

    def call(server, tool, args):
        calls.append(args)
        return json.dumps({"issues": []})

    spec = {"server": "jira", "jql": "project = P", "cloud_id": "abc-123", "arguments": {"evil": 1}}
    ingest_specs(None, None, None, spec, call)
    assert calls == [{"jql": "project = P", "cloudId": "abc-123"}]
    with pytest.raises(ValueError, match="cloud_id"):
        ingest_specs(None, None, None, {**spec, "cloud_id": "x y;"}, call)
    out = parse_run_options(
        {"run_mode": "greenfield", "test_types": ["unit"], "spec_sources": {"jira_mcp": spec}}
    )
    assert out["spec_sources"]["jira_mcp"] == {
        "server": "jira",
        "jql": "project = P",
        "issue_keys": [],
        "cloud_id": "abc-123",
    }


def test_clearing_secrets_does_not_reenable_env_fallback(monkeypatch):
    mcp_servers.upsert({"name": "j", "transport": "http", "url": "https://a.example.com/mcp"})
    monkeypatch.setenv("HARNESS_MCP_SECRET_J_BEARER_TOKEN", SECRET)
    mcp_servers.clear_secrets("j")
    mcp_servers.set_secrets("j", {"BEARER_TOKEN": ""})
    assert mcp_servers.get_secret("j", "BEARER_TOKEN") == ""


def test_stuck_mcp_close_is_abandoned_after_the_bound(monkeypatch):
    import time

    from maf_test_harness.agent import runtime

    monkeypatch.setattr(runtime, "MCP_CLOSE_TIMEOUT", 0.05)

    class Stuck:
        async def close(self):
            await asyncio.sleep(30)

    started = time.monotonic()
    asyncio.run(runtime._close_bounded(Stuck()))
    assert time.monotonic() - started < 5


def test_api_test_probe_runs_without_holding_the_api_lock(api):
    api.mcp_save(srv())
    lock_was_free = []

    def other_thread_can_lock():
        if api.lock.acquire(timeout=2):
            api.lock.release()
            return True
        return False

    async def probe(server):
        lock_was_free.append(await asyncio.to_thread(other_thread_can_lock))
        return {"tools": ["browser_snapshot"], "count": 1}

    api.mcp_probe = probe
    assert api.mcp_test("pw")["ok"] is True and lock_was_free == [True]


# ---- import (paste a command or MCP JSON) and discovered tools --------------------------------------------------------
IMPORT_SECRET = "tok-import-0123456789"
HTTP_TOKEN = "test-authorization-token-32-characters"


def test_import_npx_with_windows_path_keeps_backslashes():
    [item] = mcp_servers.parse_import(r"npx -y @modelcontextprotocol/server-filesystem C:\work")
    s = item["server"]
    assert s["name"] == "server-filesystem" and s["command"] == "npx"
    assert s["args"] == ["-y", "@modelcontextprotocol/server-filesystem", "C:\\work"]
    assert s["enabled"] is False and s["stages"] == [] and s["allowed_tools"] == []
    assert item["source_key"] == "" and item["secret_keys"] == []


def test_import_uvx_and_docker_names():
    [uvx] = mcp_servers.parse_import("uvx mcp-server-git --repository .")
    assert uvx["server"]["name"] == "mcp-server-git" and uvx["server"]["args"] == [
        "mcp-server-git",
        "--repository",
        ".",
    ]
    [docker] = mcp_servers.parse_import("docker run -i --rm mcp/fetch")
    assert docker["server"]["name"] == "fetch" and docker["server"]["command"] == "docker"


def test_import_env_assignment_is_a_secret_name_only():
    [item] = mcp_servers.parse_import(f"API_TOKEN={IMPORT_SECRET} npx -y @playwright/mcp@latest")
    assert item["server"]["env_keys"] == ["API_TOKEN"] and item["secret_keys"] == ["API_TOKEN"]
    assert item["server"]["command"] == "npx" and item["server"]["args"] == ["-y", "@playwright/mcp@latest"]
    assert item["server"]["name"] == "mcp"
    assert IMPORT_SECRET not in json.dumps(item)


def test_import_json_stdio_and_http_returns_names_never_values():
    config = {
        "mcpServers": {
            "files": {"command": "npx", "args": ["-y", "server-x"], "env": {"API_TOKEN": IMPORT_SECRET}},
            "jira": {
                "type": "http",
                "url": "https://mcp.example.com/mcp",
                "headers": {"Authorization": f"Bearer {IMPORT_SECRET}"},
            },
        }
    }
    result = mcp_servers.parse_import(json.dumps(config))
    assert [(i["source_key"], i["server"]["name"], i["secret_keys"]) for i in result] == [
        ("files", "files", ["API_TOKEN"]),
        ("jira", "jira", ["BEARER_TOKEN"]),
    ]
    assert (
        result[1]["server"]["transport"] == "http"
        and result[1]["server"]["url"] == "https://mcp.example.com/mcp"
    )
    assert IMPORT_SECRET not in json.dumps(result)


def test_import_vscode_bare_map_and_single_object():
    vscode = {
        "inputs": [],
        "servers": {"pw": {"type": "stdio", "command": "npx", "args": ["@playwright/mcp@latest"]}},
    }
    assert [i["server"]["name"] for i in mcp_servers.parse_import(json.dumps(vscode))] == ["pw"]
    bare = {"pw": {"command": "uvx", "args": ["mcp-server-git"]}}
    assert [i["server"]["name"] for i in mcp_servers.parse_import(json.dumps(bare))] == ["pw"]
    [single] = mcp_servers.parse_import(json.dumps({"command": "uvx", "args": ["mcp-server-git"]}))
    assert single["server"]["name"] == "mcp-server-git" and single["source_key"] == ""


def test_import_rejects_sse_forbidden_env_and_other_headers():
    with pytest.raises(ValueError, match="only stdio and streamable HTTP"):
        mcp_servers.parse_import(
            json.dumps({"mcpServers": {"old": {"type": "sse", "url": "https://x.example.com/sse"}}})
        )
    for entry in (
        {"command": "node", "env": {"NODE_OPTIONS": "--require ./evil.js"}},
        {"command": "node", "env": {"PATH": "/tmp"}},
        {"type": "http", "url": "https://x.example.com/mcp", "headers": {"X-Api": "a"}},
    ):
        with pytest.raises(ValueError):
            mcp_servers.parse_import(json.dumps({"mcpServers": {"x": entry}}))


def test_import_duplicate_names_get_suffixes():
    mcp_servers.upsert(srv(name="fs"))
    names = [
        i["server"]["name"]
        for i in mcp_servers.parse_import(
            json.dumps({"mcpServers": {"fs": {"command": "x"}, "FS": {"command": "y"}}})
        )
    ]
    assert names == ["fs-2", "fs-3"]


def test_import_bad_json_and_oversize_input():
    with pytest.raises(ValueError, match="not valid JSON"):
        mcp_servers.parse_import('{"mcpServers": {')
    with pytest.raises(ValueError, match="100 kB"):
        mcp_servers.parse_import("npx " + "x" * 100_000)
    with pytest.raises(ValueError, match="At most 20"):
        mcp_servers.parse_import(json.dumps({"mcpServers": {f"s{i}": {"command": "x"} for i in range(21)}}))


def test_import_route_over_http_never_returns_secret_values(api):
    from urllib.request import Request, urlopen

    from maf_test_harness.ui.api import make_server

    server = make_server(api, HTTP_TOKEN, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        text = json.dumps(
            {"mcpServers": {"files": {"command": "npx", "args": ["x"], "env": {"API_TOKEN": IMPORT_SECRET}}}}
        )
        request = Request(
            f"http://127.0.0.1:{server.server_port}/mcp-servers/import",
            data=json.dumps({"text": text}).encode(),
            headers={"Authorization": f"Bearer {HTTP_TOKEN}", "Content-Type": "application/json"},
            method="POST",
        )
        raw = urlopen(request).read().decode()
    finally:
        server.shutdown()
        server.server_close()
    assert IMPORT_SECRET not in raw
    result = json.loads(raw)
    assert result["imported"] == [{"source_key": "files", "name": "files", "secret_keys": ["API_TOKEN"]}]
    assert (
        result["servers"][0]["secrets"] == {"API_TOKEN": False} and result["servers"][0]["enabled"] is False
    )


def test_probe_catalogue_is_redacted_and_capped(monkeypatch):
    class Fn:
        def __init__(self, name, description):
            self.name, self.description, self.additional_properties = name, description, {}

    class Tool:
        functions = [
            Fn("browser_snapshot", "Snapshot. Authorization: Bearer abcdefghijklmnopqrstuvwxyz " + "x" * 400)
        ]

        async def connect(self):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(mcp_servers, "build_tool", lambda server, **_: Tool())
    result = asyncio.run(mcp_servers.probe(srv()))
    [entry] = result["catalog"]
    assert result["tools"] == ["browser_snapshot"] and entry["name"] == "browser_snapshot"
    assert "abcdefghijklmnopqrstuvwxyz" not in entry["description"] and len(entry["description"]) <= 300


def test_discovered_tools_persist_after_test_and_survive_save(api):
    api.mcp_save(srv())

    async def probe(server):
        return {
            "tools": ["browser_snapshot", "browser_click"],
            "count": 2,
            "catalog": [
                {"name": "browser_snapshot", "description": "Read the page"},
                {"name": "browser_click", "description": "Click"},
            ],
        }

    api.mcp_probe = probe
    assert api.mcp_test("pw") == {"ok": True, "tools": ["browser_snapshot", "browser_click"], "count": 2}
    stored = mcp_servers.get("pw")["discovered_tools"]
    assert stored == [
        {"name": "browser_snapshot", "description": "Read the page"},
        {"name": "browser_click", "description": "Click"},
    ]
    view = api.mcp_save(srv(allowed_tools=["browser_snapshot"], discovered_tools=[{"name": "forged"}]))
    assert view["discovered_tools"] == stored  # a client-sent catalogue is ignored, the stored one survives
    assert api.mcp_list()["servers"][0]["discovered_tools"] == stored


def test_old_config_without_discovered_tools_loads_and_catalogue_is_capped():
    mcp_servers.upsert(srv())
    path = mcp_servers.config_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    for item in raw["servers"]:
        item.pop("discovered_tools", None)
    path.write_text(json.dumps(raw), encoding="utf-8")
    [server] = mcp_servers.load()
    assert server["name"] == "pw" and server["discovered_tools"] == []
    mcp_servers.set_discovered("pw", [{"name": f"t{i}", "description": ""} for i in range(250)])
    assert len(mcp_servers.get("pw")["discovered_tools"]) == mcp_servers.MAX_DISCOVERED_TOOLS == 200
    mcp_servers.validate(srv(allowed_tools=[f"t{i}" for i in range(200)]))  # the listed cap is 200
    assert mcp_servers.MAX_MCP_TOOLS_PER_STAGE == 8


# ---- review fixes: inline secrets, placeholders, command parsing, stale catalogue, stage budget -----------------------
@pytest.mark.parametrize(
    "text",
    [
        f"docker run -i --rm -e API_TOKEN={IMPORT_SECRET} mcp/fetch",
        f"docker run -i --rm --env API_TOKEN={IMPORT_SECRET} mcp/fetch",
        f"docker run -i --rm --env=API_TOKEN={IMPORT_SECRET} mcp/fetch",
    ],
)
def test_docker_env_flags_become_secret_names_and_pass_through(text):
    [item] = mcp_servers.parse_import(text)
    assert item["server"]["args"] == ["run", "-i", "--rm", "-e", "API_TOKEN", "mcp/fetch"]
    assert item["server"]["env_keys"] == ["API_TOKEN"] and item["secret_keys"] == ["API_TOKEN"]
    assert IMPORT_SECRET not in json.dumps(item)


@pytest.mark.parametrize(
    "text",
    [
        "npx -y server --api-key abcdef123456",
        "npx -y server --token="
        + "gh"
        + "p_abcdefghijklmnopqrstuvwx",  # split so the CI secret scan does not match
        'npx -y server --header "Authorization: Bearer abcdefghijklmnopqrstuvwx"',
    ],
)
def test_secret_looking_arguments_are_rejected(text):
    with pytest.raises(ValueError, match="secret-looking value"):
        mcp_servers.parse_import(text)


def test_secret_in_url_query_is_rejected_and_manual_save_too():
    text = json.dumps(
        {"mcpServers": {"h": {"type": "http", "url": "https://x.example.com/mcp?token=abcdefgh1234"}}}
    )
    with pytest.raises(ValueError, match="secret-looking value"):
        mcp_servers.parse_import(text)
    with pytest.raises(ValueError, match="secret-looking value"):
        mcp_servers.upsert(srv(args=["--api-key", "abcdef123456"]))
    with pytest.raises(ValueError, match="secret-looking value"):
        mcp_servers.upsert(
            {"name": "h", "transport": "http", "url": "https://x.example.com/mcp?access_token=abc123"}
        )


def test_vscode_placeholders_are_not_stored_as_secrets():
    config = {
        "mcpServers": {
            "local": {"command": "node", "env": {"API_TOKEN": "${input:api_token}"}},
            "remote": {
                "type": "http",
                "url": "https://x.example.com/mcp",
                "headers": {"Authorization": "${env:TOKEN}"},
            },
        }
    }
    local, remote = mcp_servers.parse_import(json.dumps(config))
    assert local["server"]["env_keys"] == ["API_TOKEN"] and local["secret_keys"] == []
    assert remote["secret_keys"] == []


def test_command_continuations_unc_and_drive_paths():
    [item] = mcp_servers.parse_import("npx -y server-x \\\n  C:\\work")
    assert item["server"]["args"] == ["-y", "server-x", "C:\\work"]
    [item] = mcp_servers.parse_import('npx x \\\\server\\share "a b"')
    assert item["server"]["args"] == ["x", "\\\\server\\share", "a b"]
    [item] = mcp_servers.parse_import("npx -y server-x `\n  --flag")
    assert item["server"]["args"] == ["-y", "server-x", "--flag"]


def test_stale_catalogue_is_dropped_and_args_or_env_changes_clear_it():
    mcp_servers.upsert(srv())
    probed = mcp_servers.get("pw")
    mcp_servers.upsert(srv(args=["changed"]))  # target changed while Test was running
    assert mcp_servers.set_discovered("pw", [{"name": "t", "description": ""}], probed=probed) is False
    assert mcp_servers.get("pw")["discovered_tools"] == []
    current = mcp_servers.get("pw")
    assert mcp_servers.set_discovered("pw", [{"name": "t", "description": ""}], probed=current) is True
    mcp_servers.upsert(srv(args=["other"]))
    assert mcp_servers.get("pw")["discovered_tools"] == []  # args changed: catalogue cleared


def test_stage_budget_counts_enabled_servers_in_runtime_order():
    def server(name, n, enabled=True):
        return {
            "name": name,
            "enabled": enabled,
            "stages": ["requirements"],
            "allowed_tools": [f"{name}{i}" for i in range(n)],
            "approval_required_tools": [],
        }

    budget = mcp_servers.stage_budget([server("a", 5), server("b", 5), server("c", 2, enabled=False)])
    assert budget == {"requirements": {"tools": 10, "max": 8, "skipped": ["b"]}}
    assert mcp_servers.stage_budget([server("a", 5)]) == {}


# ---- stored secret-bearing servers are kept (loaded disabled), checks run on client writes only --------------------
def test_stored_secret_arg_loads_disabled_with_problem_and_survives_another_save():
    stored = {
        "name": "pw",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "server-x", "--api-key", "abcdef123456"],
        "env_keys": [],
        "url": "",
        "enabled": True,
        "stages": ["requirements"],
        "allowed_tools": [],
        "approval_required_tools": [],
        "preset": "",
    }
    mcp_servers.config_path().parent.mkdir(parents=True, exist_ok=True)
    mcp_servers.config_path().write_text(json.dumps({"servers": [stored]}), encoding="utf-8")
    [server] = mcp_servers.load()
    assert server["enabled"] is False
    assert server["problems"] == ["Move the token in the arguments to a secret before enabling."]
    assert mcp_servers.for_stage("requirements") == []
    mcp_servers.upsert(
        srv(name="other")
    )  # an unrelated save rewrites the file: the stored server is still there
    names = {s["name"]: s for s in mcp_servers.load()}
    assert set(names) == {"pw", "other"} and names["pw"]["args"] == stored["args"]
    assert "problems" not in json.loads(mcp_servers.config_path().read_text(encoding="utf-8"))["servers"][0]
    with pytest.raises(
        ValueError, match="secret-looking value"
    ):  # enabling by saving the same args is refused
        mcp_servers.upsert({**stored, "enabled": True})


@pytest.mark.parametrize(
    "args",
    [
        ["--repository", "."],
        ["/Users/a/b"],
        ["C:\\work"],
        ["@scope/pkg@1.2.3"],
        ["--port", "8080"],
        ["3f786850e387550fdab836ed7e6dc881de23001b"],  # a git commit sha
        ["--headless"],
        ["-y"],
        ["--header", "X-Request-Id: 12345"],
    ],
)
def test_legitimate_arguments_are_accepted(args):
    assert mcp_servers.upsert(srv(name="ok", args=args))["args"] == args


def test_legitimate_http_url_without_query_is_accepted():
    server = mcp_servers.upsert({"name": "h", "transport": "http", "url": "https://example.com/mcp"})
    assert server["url"] == "https://example.com/mcp"


def test_fingerprint_follows_the_target_and_holds_no_secret(tmp_path):
    before = mcp_servers.fingerprint(mcp_servers.upsert(srv(env_keys=["API_TOKEN"])))
    mcp_servers.set_secrets("pw", {"API_TOKEN": SECRET})
    assert mcp_servers.fingerprint(mcp_servers.get("pw")) == before  # secrets are not part of the target
    assert SECRET not in before
    for changed in (
        srv(env_keys=["API_TOKEN"], command="uvx"),
        srv(env_keys=["API_TOKEN"], args=["y"]),
        srv(env_keys=["OTHER"]),
    ):
        assert mcp_servers.fingerprint(mcp_servers.validate(changed)) != before
    listed = LocalAPI(tmp_path / "runs", requirements_mode="deterministic").mcp_list()
    assert listed["servers"][0]["fingerprint"] == before
    assert SECRET not in json.dumps(listed)


@pytest.mark.parametrize(
    "changed",
    [
        {"args": ["changed"]},  # args only: the case that used to keep the secret
        {"command": "uvx"},
        {"env_keys": ["OTHER"]},
    ],
)
def test_any_target_change_clears_the_secret(changed):
    mcp_servers.upsert(srv(env_keys=["API_TOKEN"]))
    mcp_servers.set_secrets("pw", {"API_TOKEN": SECRET})
    assert mcp_servers.secret_status(mcp_servers.get("pw")) == {"API_TOKEN": True}
    mcp_servers.upsert(srv(**{"env_keys": ["API_TOKEN"], **changed}))
    assert (
        mcp_servers.get_secret("pw", "API_TOKEN") == ""
    )  # the value is gone, whatever the new key list says


def test_unchanged_target_keeps_the_secret():
    mcp_servers.upsert(srv(env_keys=["API_TOKEN"]))
    mcp_servers.set_secrets("pw", {"API_TOKEN": SECRET})
    mcp_servers.upsert(srv(env_keys=["API_TOKEN"], enabled=False))  # enabled is not part of the target
    assert mcp_servers.secret_status(mcp_servers.get("pw")) == {"API_TOKEN": True}


# ---- MCP hints (readOnlyHint, destructiveHint) and reserved names -----------------------------------------------
_HINT_SERVER = """
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
m = FastMCP("hx")
@m.tool(annotations=ToolAnnotations(readOnlyHint=True))
def fetch_page(x: str) -> str:
    return x
@m.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
def drop_rows(x: str) -> str:
    return x
@m.tool()
def mystery(x: str) -> str:
    return x
m.run("stdio")
"""


def test_real_stdio_probe_passes_mcp_hints_through(tmp_path):
    script = tmp_path / "hint_server.py"
    script.write_text(_HINT_SERVER)
    import sys

    mcp_servers.upsert(srv(name="hx", command=sys.executable, args=[str(script)]))
    catalog = {t["name"]: t for t in asyncio.run(mcp_servers.probe(mcp_servers.get("hx")))["catalog"]}
    assert catalog["fetch_page"] == {"name": "fetch_page", "description": "", "read_only": True}
    assert catalog["drop_rows"] == {
        "name": "drop_rows",
        "description": "",
        "destructive": True,
        "read_only": False,
    }
    assert (
        "read_only" not in catalog["mystery"] and "destructive" not in catalog["mystery"]
    )  # not sent: not guessed


def test_stored_catalogue_keeps_only_boolean_hints_and_old_entries_have_none():
    kept = mcp_servers._tool_catalog(
        [
            {"name": "a", "description": "x", "read_only": True, "destructive": "yes"},
            {"name": "b", "description": "y"},  # written before hints existed
        ]
    )
    assert kept == [{"name": "a", "description": "x", "read_only": True}, {"name": "b", "description": "y"}]


def test_server_named_new_is_reserved_and_import_never_picks_it():
    with pytest.raises(ValueError, match="reserved"):
        mcp_servers.validate(srv(name="new"))
    [item] = mcp_servers.parse_import("npx -y new")
    assert item["server"]["name"] == "new-2"

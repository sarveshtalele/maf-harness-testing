"""MCP servers: config validation, presets, secrets, runtime wiring (MAF tool classes mocked), API."""

import asyncio
import json
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
        srv(allowed_tools=[f"t{i}" for i in range(9)]),
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


def test_secret_env_names_cannot_collide_and_match_desktop():
    assert mcp_servers.secret_env_name("a-b", "C") == "HARNESS_MCP_SECRET_A__B_C"
    assert mcp_servers.secret_env_name("a", "B_C") == "HARNESS_MCP_SECRET_A_B_C"
    ts = (Path(__file__).parents[1] / "desktop" / "src" / "keystore.ts").read_text()
    assert ".toUpperCase().replace(/-/g, '__')" in ts  # keep the TypeScript encoding identical


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

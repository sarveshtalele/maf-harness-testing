"""Typed tool registry: envelope, schema validation, scoped file tools, diagnosis tools, hook integration."""

import asyncio
import json
import shutil
from types import SimpleNamespace

import pytest

from maf_test_harness.hooks import HookConfig, build_engine
from maf_test_harness.tools import env_tools, file_tools
from maf_test_harness.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec, object_schema
from maf_test_harness.tools.typed import TYPED_STAGE_TOOLS, default_registry

# credential-shaped values are assembled at run time so the repository secrets scan (CI) can stay strict
FAKE_KEY = "sk" + "-" + "abcdefghijklmnop1234"
FAKE_TOKEN = "ghp" + "_" + "abcdefghijklmnopqrstuvwx"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("line one\nline two\nline three\n", encoding="utf-8")
    (root / ".env").write_text(
        "OPENAI_API_KEY=" + "sk" + "-should-never-be-read-1234567890\n", encoding="utf-8"
    )
    (root / "notes.txt").write_text("token: abcdefgh12345678\n", encoding="utf-8")
    return root


@pytest.fixture
def ctx(repo, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ToolContext(repo_root=repo, workspace=ws, writable_roots=[ws], ecosystem="python")


# --- envelope -----------------------------------------------------------------------------------------------


def test_envelope_round_trips_through_json():
    result = ToolResult(False, "ENV-PY-001", "environment", "no module", {"a": 1}, ["install it"], ["retry"])
    assert ToolResult.from_json(result.to_json()) == result
    assert set(result.to_dict()) == {
        "ok",
        "code",
        "category",
        "evidence",
        "data",
        "suggested_fixes",
        "next_actions",
    }


def test_oversized_result_stays_valid_json():
    huge = ToolResult(True, data={"blob": "x" * 50_000}, evidence="e" * 5000)
    text = huge.to_json(limit=2000)
    assert len(text) <= 2000
    assert json.loads(text)["data"] == {"truncated": True}


def test_failure_helper_truncates_evidence():
    assert len(ToolResult.failure("X", "y" * 5000).evidence) == 600


# --- registry -----------------------------------------------------------------------------------------------


def make_registry(ctx, **kwargs):
    echo = ToolSpec(
        "echo",
        "Echo the text.",
        lambda c, a: ToolResult(True, data={"text": a["text"]}),
        object_schema(["text"], text={"type": "string"}, times={"type": "integer", "minimum": 1}),
    )
    boom = ToolSpec("boom", "Always fails.", lambda c, a: 1 / 0)
    return ToolRegistry(ctx, stage_tools={"s": ("echo", "missing")}, **kwargs).register(echo, boom)


def test_unknown_tool_lists_the_alternatives(ctx):
    result = make_registry(ctx).call("nope")
    assert not result.ok and result.code == "TOOL-UNKNOWN" and "echo" in result.suggested_fixes[0]


@pytest.mark.parametrize(
    "arguments",
    [{}, {"text": 5}, {"text": "a", "extra": 1}, {"text": "a", "times": 0}],
)
def test_arguments_are_validated_against_the_schema(ctx, arguments):
    result = make_registry(ctx).call("echo", arguments)
    assert not result.ok and result.code == "TOOL-ARGS"


def test_valid_call_returns_the_handlers_envelope(ctx):
    assert make_registry(ctx).call("echo", {"text": "hi"}).data == {"text": "hi"}


def test_a_crashing_handler_becomes_an_error_envelope(ctx):
    result = make_registry(ctx).call("boom")
    assert not result.ok and result.code == "TOOL-ERROR" and "ZeroDivisionError" in result.evidence


def test_duplicate_and_invalid_registrations_are_rejected(ctx):
    registry = make_registry(ctx)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(ToolSpec("echo", "again", lambda c, a: ToolResult(True)))
    with pytest.raises(Exception, match="type"):
        registry.register(ToolSpec("bad", "d", lambda c, a: ToolResult(True), {"type": "nonsense"}))


def test_stage_selection_only_lists_registered_tools(ctx):
    registry = make_registry(ctx)
    assert registry.names("s") == ["echo"] and registry.names("other") == [] and "boom" in registry.names()


def test_every_call_is_audited(ctx):
    log = []
    registry = make_registry(ctx, audit=lambda event, **f: log.append((event, f)))
    registry.call("echo", {"text": "x"})
    registry.call("echo", {})
    registry.call("ghost")
    assert [f["tool"] for _, f in log] == [
        "echo"
    ]  # unknown tools and bad arguments are refused before any audit
    assert log[0][1]["ok"] is True


def test_default_registry_has_every_tool_the_stage_maps_name(ctx):
    registry = default_registry(ctx)
    for stage, names in TYPED_STAGE_TOOLS.items():
        assert registry.names(stage) == list(names), stage


# --- file tools --------------------------------------------------------------------------------------------------


def test_read_file_numbers_lines_and_pages(ctx):
    result = default_registry(ctx).call("read_file", {"path": "src/app.py", "start": 2, "end": 3})
    assert result.ok and result.data["lines"] == ["2: line two", "3: line three"]
    assert result.data["total_lines"] == 3


def test_read_file_pages_through_long_files(ctx, repo):
    (repo / "long.txt").write_text("\n".join(f"l{i}" for i in range(1, 501)), encoding="utf-8")
    first = default_registry(ctx).call("read_file", {"path": "long.txt"})
    assert len(first.data["lines"]) == file_tools.MAX_LINES
    assert first.next_actions == ["call read_file with start=201 for more"]


@pytest.mark.parametrize(
    "path", ["../secret.txt", "src/../../secret.txt", "C:/Windows/win.ini", "/etc/passwd"]
)
def test_read_file_refuses_paths_outside_the_repository(ctx, path):
    result = default_registry(ctx).call("read_file", {"path": path})
    assert not result.ok and result.code in {"FILE-SCOPE", "FILE-NOT-FOUND"}
    assert "win.ini" not in json.dumps(result.data)


def test_read_file_refuses_secret_files(ctx):
    result = default_registry(ctx).call("read_file", {"path": ".env"})
    assert result.code == "FILE-SENSITIVE" and "sk-should" not in result.to_json()


def test_read_file_missing_file(ctx):
    assert default_registry(ctx).call("read_file", {"path": "nope.py"}).code == "FILE-NOT-FOUND"


def test_list_dir_hides_secret_files_and_marks_directories(ctx, repo):
    shutil.copytree(repo, ctx.workspace, dirs_exist_ok=True)  # the workspace is a copy of the repository
    result = default_registry(ctx).call("list_dir", {})
    assert result.ok and result.data["entries"][0] == "src/" and ".env" not in result.data["entries"]
    assert "notes.txt" in result.data["entries"]


def test_list_dir_outside_the_repository(ctx):
    assert default_registry(ctx).call("list_dir", {"path": ".."}).code == "FILE-SCOPE"


def test_write_test_file_stays_inside_the_writable_root(ctx):
    registry = default_registry(ctx)
    written = registry.call("write_test_file", {"path": "tests/test_a.py", "content": "def test_a(): pass\n"})
    assert (
        written.ok and (ctx.writable_roots[0] / "tests" / "test_a.py").read_text() == "def test_a(): pass\n"
    )
    for escape in ("../evil.py", "tests/../../evil.py"):
        assert registry.call("write_test_file", {"path": escape, "content": "x"}).code == "FILE-SCOPE"
    assert not (ctx.writable_roots[0].parent / "evil.py").exists()


def test_write_test_file_without_a_writable_root(repo):
    result = default_registry(ToolContext(repo_root=repo)).call(
        "write_test_file", {"path": "a.py", "content": "x"}
    )
    assert result.code == "FILE-SCOPE" and "no writable" in result.evidence


def test_write_test_file_size_limit(ctx):
    big = "x" * (file_tools.MAX_WRITE_BYTES + 1)
    assert (
        default_registry(ctx).call("write_test_file", {"path": "a.py", "content": big}).code
        == "FILE-TOO-LARGE"
    )


def test_delete_file_only_inside_the_writable_root(ctx, repo):
    registry = default_registry(ctx)
    registry.call("write_test_file", {"path": "tests/t.py", "content": "x"})
    assert registry.call("delete_file", {"path": "tests/t.py"}).ok
    assert registry.call("delete_file", {"path": "tests/t.py"}).code == "FILE-NOT-FOUND"
    assert registry.call("delete_file", {"path": str(repo / "src" / "app.py")}).code == "FILE-SCOPE"
    assert (repo / "src" / "app.py").exists()


# --- hooks around the tools --------------------------------------------------------------------------------


def hooked_registry(ctx):
    engine = build_engine(HookConfig(builtins={"audit": False}))
    return default_registry(ctx, hooks=engine), engine


def test_hooks_deny_before_the_handler_runs(ctx):
    registry, _ = hooked_registry(ctx)
    ctx.writable_roots = []  # nothing writable: path_scope denies before the tool could
    result = registry.call("write_test_file", {"path": "a.py", "content": "x"})
    assert result.code == "HOOK-DENIED" and "builtin:path_scope" in result.evidence


def test_secrets_in_arguments_are_redacted_before_the_tool_sees_them(ctx):
    registry, _ = hooked_registry(ctx)
    content = f"KEY = '{FAKE_KEY}'\n"
    assert registry.call("write_test_file", {"path": "a.py", "content": content}).ok
    written = (ctx.writable_roots[0] / "a.py").read_text()
    assert "sk-abcdef" not in written and "[REDACTED]" in written


def test_secrets_in_results_are_redacted_and_the_envelope_stays_valid(ctx):
    registry, _ = hooked_registry(ctx)
    result = registry.call("read_file", {"path": "notes.txt"})
    assert result.ok and "abcdefgh12345678" not in result.to_json() and "[REDACTED]" in result.to_json()


def test_the_hook_context_follows_the_tool_context(ctx, monkeypatch):
    provisioned = SimpleNamespace(events=[{"kind": "jdk", "action": "provisioned"}])
    monkeypatch.setattr(env_tools.toolchains, "ensure", lambda *a, **k: provisioned)
    registry, engine = hooked_registry(ctx)
    denied = registry.call("provision_toolchain", {"kind": "jdk", "version": "17"})
    assert denied.code == "HOOK-DENIED" and "approval required" in denied.evidence
    ctx.allow_downloads = True  # the environment approval
    allowed = registry.call("provision_toolchain", {"kind": "jdk", "version": "17"})
    assert allowed.ok and engine.context["allow_downloads"] is True


def test_the_handler_refuses_downloads_even_without_hooks(ctx):
    result = default_registry(ctx).call("provision_toolchain", {"kind": "jdk", "version": "17"})
    assert result.code == "CONSENT-REQUIRED"


def test_a_failed_download_is_an_environment_problem(ctx, monkeypatch):
    def fail(*a, **k):
        raise env_tools.toolchains.ToolchainError("network unreachable")

    monkeypatch.setattr(env_tools.toolchains, "ensure", fail)
    ctx.allow_downloads = True
    result = default_registry(ctx).call("provision_toolchain", {"kind": "maven", "version": "3.9.9"})
    assert result.code == "TOOLCHAIN-FAILED" and result.category == "environment"


def test_provision_rejects_an_unknown_kind(ctx):
    assert (
        default_registry(ctx).call("provision_toolchain", {"kind": "ruby", "version": "3"}).code
        == "TOOL-ARGS"
    )


# --- env / diagnosis tools ----------------------------------------------------------------------------------


def test_diagnose_env_error_names_the_recipe_and_autonomy(ctx):
    log = "Error: The JAVA_HOME environment variable is not defined correctly"
    result = default_registry(ctx).call("diagnose_env_error", {"log": log, "ecosystem": "java"})
    assert result.ok and result.code == "ENV-JAVA-002" and result.category == "environment"
    assert result.data["autonomy"] == "ask" and result.data["environmental"] is True
    assert "provision-jdk" in json.dumps(result.data["diagnoses"])
    assert result.next_actions[0].startswith("ask the human") and len(result.suggested_fixes) >= 2


def test_diagnose_exec_error_separates_an_application_defect(ctx):
    result = default_registry(ctx).call(
        "diagnose_exec_error", {"log": "E   AssertionError: assert 404 == 200"}
    )
    assert (
        result.code == "EXEC-103"
        and result.category == "app_defect"
        and result.data["environmental"] is False
    )
    assert result.data["autonomy"] == "manual" and "explain" in result.next_actions[0]


def test_diagnose_env_error_ignores_non_environment_findings(ctx):
    result = default_registry(ctx).call(
        "diagnose_env_error", {"log": "E   AssertionError: assert 404 == 200"}
    )
    assert result.ok and result.code == "UNKNOWN"


def test_diagnose_unknown_text(ctx):
    result = default_registry(ctx).call("diagnose_exec_error", {"log": "all good, nothing to see"})
    assert result.ok and result.code == "UNKNOWN" and result.next_actions


def test_diagnose_treats_a_declared_but_uninstalled_import_as_environmental(ctx, monkeypatch):
    monkeypatch.setattr(env_tools.env_check, "missing_packages", lambda ws: ["fastapi"])
    log = "E   ModuleNotFoundError: No module named 'fastapi'"
    result = default_registry(ctx).call("diagnose_exec_error", {"log": log, "ecosystem": "python"})
    assert result.category == "environment" and result.data["environmental"] is True


def test_list_missing_packages(ctx, monkeypatch):
    monkeypatch.setattr(env_tools.env_check, "missing_packages", lambda ws: ["chromadb"])
    result = default_registry(ctx).call("list_missing_packages", {})
    assert result.data == {"missing": ["chromadb"]} and result.suggested_fixes
    assert default_registry(ToolContext()).call("list_missing_packages", {}).code == "NO-WORKSPACE"


def test_detect_toolchains_for_a_java_repository(ctx):
    (ctx.workspace / "pom.xml").write_text(
        "<project><properties><maven.compiler.release>17</maven.compiler.release></properties></project>",
        encoding="utf-8",
    )
    result = default_registry(ctx).call("detect_toolchains", {"ecosystem": "java"})
    kinds = {row["kind"]: row for row in result.data["toolchains"]}
    assert result.ok and kinds["jdk"]["version"] == "17" and "maven" in kinds
    assert all(row["status"] in {"found", "download"} for row in kinds.values())


def test_detect_toolchains_needs_a_repository():
    assert default_registry(ToolContext()).call("detect_toolchains", {}).code == "NO-REPOSITORY"


def test_detect_stack(ctx):
    (ctx.workspace / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    result = default_registry(ctx).call("detect_stack", {})
    assert result.ok and "python" in result.data["ecosystems"]


# --- as MAF tools ------------------------------------------------------------------------------------------------


def test_as_function_tools_are_real_maf_tools_returning_the_envelope(ctx):
    registry = default_registry(ctx)
    tools = {t.name: t for t in registry.as_function_tools("test_execution")}
    assert set(tools) == set(TYPED_STAGE_TOOLS["test_execution"])
    assert tools["read_file"].parameters()["required"] == ["path"]
    content = asyncio.run(tools["read_file"].invoke(arguments={"path": "notes.txt"}))
    envelope = json.loads(content[0].text)
    assert envelope["ok"] is True and envelope["data"]["path"] == "notes.txt"


def test_maf_tool_path_does_not_run_registry_hooks_twice(ctx):
    calls = []
    engine = build_engine(HookConfig(builtins={"audit": False}))
    original = engine.before_tool
    engine.before_tool = lambda *a, **k: calls.append(a[0]) or original(*a, **k)
    registry = default_registry(ctx, hooks=engine)
    tool = next(t for t in registry.as_function_tools("test_execution") if t.name == "list_dir")
    asyncio.run(tool.invoke(arguments={}))
    assert calls == []  # the agent's hook middleware is what guards this path
    registry.call("list_dir", {})
    assert calls == ["list_dir"]


def test_plan_env_fix_previews_the_recovery_without_running_it(ctx):
    registry = default_registry(ctx)
    result = registry.call(
        "plan_env_fix",
        {
            "argv": ["mvn", "test-compile"],
            "log": "Could not resolve dependencies for project x",
            "ecosystem": "java",
        },
    )
    assert result.ok and result.code == "ENV-JAVA-003" and result.category == "infra"
    assert result.data["would_apply"]["recipe"] == "retry-update" and result.data["would_apply"]["argv"][
        :2
    ] == ["mvn", "-U"]
    assert "applies 'retry-update' on its own" in result.next_actions[0]


def test_plan_env_fix_says_when_only_a_human_can_help(ctx):
    result = default_registry(ctx).call(
        "plan_env_fix", {"argv": ["pip", "install", "x"], "log": "No space left on device"}
    )
    assert (
        result.ok
        and result.data["would_apply"] is None
        and "nothing can be applied automatically" in result.next_actions[0]
    )
    unknown = default_registry(ctx).call("plan_env_fix", {"argv": ["x"], "log": "all fine"})
    assert unknown.code == "UNKNOWN"


def test_plan_env_fix_needs_an_argv(ctx):
    assert default_registry(ctx).call("plan_env_fix", {"log": "x"}).code == "TOOL-ARGS"

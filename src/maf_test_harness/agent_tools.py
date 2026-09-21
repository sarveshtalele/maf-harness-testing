"""The central harness tools, exposed to any stage agent as MAF function tools.

Thin wrappers over ``code_intel`` / ``build_gate``: the same deterministic answers the host uses for its own checks.
All tools are read-only or scratch-only and bounded; none of them can reach the network, the model credentials or any
path outside the repository. Which tools a stage gets is a deliberate, small selection (``STAGE_TOOLS``): every tool
description is resent with every model call, and every tool call is another round trip.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Annotated, Any

from agent_framework import FunctionTool, tool

from . import app_launcher, code_intel, env_check

CENTRAL_TOOL_NAMES = (
    "resolve_import",
    "module_api",
    "find_symbol",
    "read_source",
    "check_test",
    "list_routes",
    "installed_packages",
    "probe_tools",
    "try_build",
    "environment_gaps",
    "app_launch_plan",
)
# Requirements/design read code; generation additionally validates and builds what it writes.
STAGE_TOOLS: dict[str, tuple[str, ...]] = {
    "requirements": ("find_symbol", "module_api", "read_source", "list_routes", "installed_packages"),
    "test_design": ("find_symbol", "module_api", "list_routes", "app_launch_plan"),
    "test_generation": (
        "module_api",
        "find_symbol",
        "read_source",
        "resolve_import",
        "check_test",
        "list_routes",
        "try_build",
        "environment_gaps",
    ),
}
MAX_RESULT_CHARS = 6_000

ContextGetter = Callable[[], code_intel.RepoContext]
BuildFn = Callable[[list[dict]], dict[str, Any]]


def _json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + '..."truncated"'


class HarnessToolbox:
    """Builds the FunctionTools for a stage from live repository context (routes, workspace, build function)."""

    def __init__(self, context: ContextGetter, build: BuildFn | None = None):
        self._context, self._build = context, build

    def _tools(self) -> dict[str, FunctionTool]:
        ctx = self._context

        @tool(name="resolve_import", approval_mode="never_require")
        async def resolve_import(
            module: Annotated[
                str, "Python dotted module (e.g. 'engine.ai_analysis') or Java fully qualified class"
            ],
            language: Annotated[str, "'python' or 'java'"] = "python",
        ) -> str:
            """Say where an import comes from (stdlib, repository file, installed/declared dependency) or suggest the correct one."""
            return _json(await asyncio.to_thread(code_intel.resolve_import, ctx(), module, language))

        @tool(name="module_api", approval_mode="never_require")
        async def module_api(
            path: Annotated[str, "Repository-relative source file, e.g. 'src/main/java/x/Service.java'"],
        ) -> str:
            """Real function/class/constructor signatures of a repository file, and which names exist only inside functions."""
            return _json(await asyncio.to_thread(code_intel.module_api, ctx(), path))

        @tool(name="find_symbol", approval_mode="never_require")
        async def find_symbol(
            name: Annotated[str, "Class, function or method name (identifier only)"],
        ) -> str:
            """Locate where a class/function/method is defined in the repository (path and line)."""
            return _json(await asyncio.to_thread(code_intel.find_symbol, ctx(), name))

        @tool(name="read_source", approval_mode="never_require")
        async def read_source(
            path: Annotated[str, "Repository-relative file"],
            start: Annotated[int, "First line (1-based)"] = 1,
            end: Annotated[int, "Last line; at most 160 lines are returned"] = 160,
        ) -> str:
            """Numbered lines of a repository file, for facts the API summary does not show."""
            return _json(await asyncio.to_thread(code_intel.read_source, ctx(), path, start, end))

        @tool(name="check_test", approval_mode="never_require")
        async def check_test(
            path: Annotated[str, "Path the generated test will have"],
            content: Annotated[str, "Complete source of the generated test"],
        ) -> str:
            """Run the host's static gates on a draft test (syntax, imports, undefined names, sys.exit mocks, patch targets, URLs)."""

            def run() -> dict[str, Any]:
                try:
                    code_intel.static_gate(ctx(), path, content)
                except ValueError as exc:
                    return {"ok": False, "problem": str(exc)[:1200]}
                return {"ok": True}

            return _json(await asyncio.to_thread(run))

        @tool(name="list_routes", approval_mode="never_require")
        async def list_routes() -> str:
            """HTTP routes the application defines, and the BASE_URL (which already contains the context path)."""
            return _json(code_intel.list_routes(ctx()))

        @tool(name="installed_packages", approval_mode="never_require")
        async def installed_packages() -> str:
            """Packages really present in the isolated environment (not merely declared)."""
            return _json(await asyncio.to_thread(code_intel.installed_packages, ctx()))

        @tool(name="probe_tools", approval_mode="never_require")
        async def probe_tools() -> str:
            """Versions of python/java/mvn/node/npm on this host."""
            return _json(await asyncio.to_thread(code_intel.probe_tools))

        @tool(name="try_build", approval_mode="never_require")
        async def try_build(
            path: Annotated[str, "Path the generated test will have"],
            content: Annotated[str, "Complete source of the generated test"],
        ) -> str:
            """Compile/collect a draft test in the prepared workspace (pytest --collect-only or mvn test-compile); never runs test bodies."""
            if self._build is None:
                return _json({"status": "skipped", "reason": "isolated environment not installed"})
            report = await asyncio.to_thread(self._build, [{"path": path, "content": content}])
            return _json({k: report.get(k) for k in ("status", "tool", "errors", "reason", "seconds")})

        @tool(name="environment_gaps", approval_mode="never_require")
        async def environment_gaps() -> str:
            """Declared dependencies that are NOT installed in the isolated environment: do not import them in tests, stub them."""
            workspace = ctx().workspace
            if workspace is None:
                return _json({"missing": [], "note": "environment not created yet"})
            return _json({"missing": await asyncio.to_thread(env_check.missing_packages, workspace)})

        @tool(name="app_launch_plan", approval_mode="never_require")
        async def app_launch_plan() -> str:
            """How the harness would start the application for ui/e2e cases (kind, base URL), or why it cannot."""
            workspace = ctx().workspace or ctx().root
            spec = await asyncio.to_thread(app_launcher.detect_launch, workspace, 0)
            return _json(
                {"launchable": bool(spec), "kind": spec and spec["kind"], "base_url": ctx().app_url}
                if spec
                else {"launchable": False, "base_url": ctx().app_url}
            )

        return {
            t.name: t
            for t in (
                resolve_import,
                module_api,
                find_symbol,
                read_source,
                check_test,
                list_routes,
                installed_packages,
                probe_tools,
                try_build,
                environment_gaps,
                app_launch_plan,
            )
        }

    def tools_for(self, stage: str) -> list[FunctionTool]:
        available = self._tools()
        return [available[name] for name in STAGE_TOOLS.get(stage, ())]


def tool_instructions(stage: str) -> str:
    """Prompt text that tells the agent which harness tools exist and how to use them sparingly."""
    names = STAGE_TOOLS.get(stage, ())
    if not names:
        return ""
    generation = (
        " Before you finalize a file, call check_test on it once (and try_build for Java) and fix what it reports; "
        "call module_api for a class you instantiate instead of guessing its constructor."
        if stage == "test_generation"
        else ""
    )
    return (
        " In addition to the skill tools you may call these deterministic harness tools: "
        + ", ".join(names)
        + ". They return exact facts from the repository; prefer them to guessing. Use them sparingly (each call is "
        "a round trip)." + generation
    )

"""The default typed-tool registry and which stages get which tools.

These are the tools of the agent loops in Phase 5. Until then the registry is used directly (and by tests): the stage
agents still use the central tools in ``agent_tools``. Every stage's set is small on purpose, because each tool's schema is
resent with every model call.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .env_tools import ENV_TOOLS
from .file_tools import FILE_TOOLS
from .registry import ToolContext, ToolRegistry

TYPED_STAGE_TOOLS: dict[str, tuple[str, ...]] = {
    "environment_setup": (
        "detect_stack",
        "detect_toolchains",
        "provision_toolchain",
        "list_missing_packages",
        "diagnose_env_error",
        "read_file",
        "list_dir",
    ),
    "test_execution": (
        "diagnose_exec_error",
        "diagnose_env_error",
        "list_missing_packages",
        "read_file",
        "list_dir",
    ),
}


def default_registry(
    context: ToolContext, hooks: Any = None, audit: Callable[..., Any] | None = None
) -> ToolRegistry:
    return ToolRegistry(context, hooks, audit, TYPED_STAGE_TOOLS).register(*ENV_TOOLS, *FILE_TOOLS)


__all__ = ["TYPED_STAGE_TOOLS", "default_registry"]

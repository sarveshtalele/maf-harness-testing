""".NET environments: the SDK a repository needs (global.json, TargetFramework)."""

from __future__ import annotations

from pathlib import Path

from .spec import EnvSpec, detect_spec


def spec(workspace: Path) -> EnvSpec:
    return detect_spec(workspace, "dotnet")

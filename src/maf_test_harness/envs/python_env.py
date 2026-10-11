"""Python environments: which interpreter builds the venv, and what the project asks for."""

from __future__ import annotations

from pathlib import Path

from .spec import EnvSpec, detect_spec


def spec(workspace: Path) -> EnvSpec:
    return detect_spec(workspace, "python")

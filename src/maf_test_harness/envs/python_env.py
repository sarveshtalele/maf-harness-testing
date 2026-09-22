"""Python environments: which interpreter builds the venv, and what the project asks for."""

from __future__ import annotations

import sys
from pathlib import Path

from .spec import EnvSpec, detect_spec, specifier_allows


def spec(workspace: Path) -> EnvSpec:
    return detect_spec(workspace, "python")


def current_interpreter_satisfies(spec_: EnvSpec) -> bool:
    """True when the interpreter running the harness already meets the project's Python requirement (or it has none)."""
    if not spec_.requirements:
        return True
    want = spec_.requirements[0].version
    have = f"{sys.version_info.major}.{sys.version_info.minor}"
    if any(op in spec_.requirements[0].reason for op in ("<", ">", "=", "~")):  # a full specifier was given
        specifier = spec_.requirements[0].reason.split("(", 1)[-1].rstrip(")")
        return specifier_allows(specifier, have)
    return tuple(map(int, have.split("."))) >= tuple(map(int, want.split(".")))

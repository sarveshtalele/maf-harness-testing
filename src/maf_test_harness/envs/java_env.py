"""Java environments (Maven and Gradle): JDK release, build tool and wrapper."""

from __future__ import annotations

from pathlib import Path

from .spec import EnvSpec, detect_spec


def spec(workspace: Path, ecosystem: str = "maven") -> EnvSpec:
    return detect_spec(workspace, ecosystem)

"""Per-language environment adapters and toolchain provisioning (see docs/ENVIRONMENTS.md)."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import toolchains
from .spec import EnvSpec, ToolRequirement, detect_spec
from .toolchains import Provisioned, Toolchain, ToolchainError


def plan_toolchains(workspace: Path, ecosystem: str, root: Path | None = None) -> list[dict]:
    """What the repository needs and whether the machine has it, without downloading anything.

    Each row: kind, version, reason, status (``found`` / ``download``), where it is, or where it would come from.
    """
    spec = detect_spec(workspace, ecosystem)
    rows = []
    for req in spec.requirements:
        have = toolchains.find(req.kind, req.version, root)
        row = {"kind": req.kind, "version": req.version, "reason": req.reason}
        if have:
            row.update(status="found", found=have.version, source=have.source, home=str(have.home))
        else:
            row.update(
                status="download",
                source=toolchains.describe(req.kind, req.version)["source"],
                can_provision=req.kind != "python" or bool(shutil.which("uv")),
            )
        rows.append(row)
    return rows


def provision_for(
    workspace: Path, ecosystem: str, *, allow_download: bool, root: Path | None = None, **kwargs
) -> Provisioned:
    """Make the repository's toolchains available (downloads only when ``allow_download``)."""
    return toolchains.ensure(
        detect_spec(workspace, ecosystem).requirements, allow_download=allow_download, root=root, **kwargs
    )


__all__ = [
    "EnvSpec",
    "Provisioned",
    "ToolRequirement",
    "Toolchain",
    "ToolchainError",
    "detect_spec",
    "plan_toolchains",
    "provision_for",
    "toolchains",
]

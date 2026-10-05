"""Reviewable manual setup instructions; never executes pasted repository knowledge."""

import os
import shlex
from pathlib import Path


def setup_guide(plan: dict, workspace: Path, knowledge: str = "", *, source_root: Path | None = None) -> dict:
    """Use the detected plan's exact commands, with host-native quoting and explicit gaps."""

    def execution_path(value):
        return (
            value.replace(str(source_root), str(workspace))
            if source_root is not None and isinstance(value, str)
            else value
        )

    def quote(argv):
        if os.name == "nt":
            return "& " + " ".join("'" + str(arg).replace("'", "''") + "'" for arg in argv)
        return shlex.join(argv)

    steps = [
        {
            "title": "Open the execution workspace",
            "command": f"Set-Location -LiteralPath '{str(workspace).replace(chr(39), chr(39) * 2)}'"
            if os.name == "nt"
            else "cd " + shlex.quote(str(workspace)),
        }
    ]
    for row in plan.get("prerequisites", []):
        if not row.get("present") and not row.get("provisionable"):
            steps.append(
                {
                    "title": f"Install missing prerequisite: {row['tool']}",
                    "instructions": row.get("install_hint", "Use the repository's documented version."),
                }
            )
    for row in plan.get("steps", []):
        steps.append(
            {
                "title": row["name"],
                "working_directory": str(workspace / row.get("cwd", ".")),
                "command": quote([execution_path(arg) for arg in row["argv"]]),
                "environment": {key: execution_path(value) for key, value in row.get("env", {}).items()},
            }
        )
    if not plan.get("steps"):
        steps.append(
            {
                "title": "Automatic setup unavailable",
                "instructions": "No supported installation commands were detected. Review the repository README and the pasted notes below; supply the missing manifests, tool versions, install/start commands and readiness URL before retrying. No commands have been invented or approved.",
            }
        )
    steps.append(
        {
            "title": "Verify and retry",
            "instructions": "Open a new terminal after installing prerequisites. Use Retry/Re-check environment to regenerate the plan and approve its commands. Manually installed dependencies are rechecked; they are not automatically reported as ready.",
        }
    )
    return {
        "platform": "Windows PowerShell" if os.name == "nt" else "macOS/Linux shell",
        "status": "manual_action_required"
        if plan.get("missing_prerequisites") or not plan.get("steps")
        else "planned",
        "steps": steps,
        "repository_knowledge": knowledge,
        "knowledge_notice": "User-provided repository context is unverified. Pasted commands are not executed automatically.",
    }

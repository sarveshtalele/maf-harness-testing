"""Environment and diagnosis tools: what the repository needs, what is missing, what an error means.

They wrap the deterministic pieces the harness already has (``envs`` for requirements and toolchains, ``errors`` for
classification) behind the typed envelope, so an agent asks a question and gets a checked, structured answer. Only
``provision_toolchain`` changes the machine, and only after the environment approval has set ``allow_downloads``.
Applying a fix recipe (``apply_env_fix``) arrives with the environment agent in Phase 5.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..envs import env_check, plan_toolchains, toolchains
from ..envs.spec import ToolRequirement
from ..errors import ENVIRONMENT, INFRA, RECIPES, Context, classify, is_environmental
from ..repo.stack import detect_stack
from .registry import ToolContext, ToolResult, ToolSpec, object_schema

KINDS = ["python", "jdk", "maven", "gradle", "dotnet"]
ECOSYSTEMS = ["python", "java", "dotnet", "node", "go"]


def _root(ctx: ToolContext):
    return ctx.workspace or ctx.repo_root


def detect_stack_tool(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    root = _root(ctx)
    if root is None:
        return ToolResult.failure("NO-REPOSITORY", "the repository has not been cloned yet")
    profile = detect_stack(root)
    return ToolResult(True, evidence=f"primary ecosystem: {profile.primary}", data=asdict(profile))


def detect_toolchains(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    root = _root(ctx)
    ecosystem = args.get("ecosystem") or ctx.ecosystem
    if root is None or not ecosystem:
        return ToolResult.failure("NO-REPOSITORY", "repository or ecosystem is not known yet")
    rows = plan_toolchains(root, ecosystem)
    missing = [r for r in rows if r["status"] == "download"]
    return ToolResult(
        True,
        evidence=f"{len(rows)} toolchain(s) needed, {len(missing)} not installed",
        data={"toolchains": rows},
        next_actions=[f"provision_toolchain kind={r['kind']} version={r['version']}" for r in missing],
    )


def provision_toolchain(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if not ctx.allow_downloads:
        return ToolResult.failure(
            "CONSENT-REQUIRED",
            "downloading a toolchain needs the human's approval of the environment plan",
            fixes=["ask the human to approve the environment plan that lists this download"],
        )
    requirement = ToolRequirement(args["kind"], args["version"], "requested by the agent")
    try:
        provisioned = toolchains.ensure([requirement], allow_download=True)
    except toolchains.ToolchainError as exc:
        return ToolResult.failure(
            "TOOLCHAIN-FAILED",
            str(exc),
            category=ENVIRONMENT,
            fixes=["check network/proxy access", "set HARNESS_TOOLCHAIN_MIRROR for an offline mirror"],
        )
    return ToolResult(
        True, evidence="; ".join(str(e) for e in provisioned.events), data={"events": provisioned.events}
    )


def list_missing_packages(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.workspace is None:
        return ToolResult.failure("NO-WORKSPACE", "the isolated environment has not been created yet")
    missing = env_check.missing_packages(ctx.workspace)
    return ToolResult(
        True,
        evidence=f"{len(missing)} declared package(s) not installed"
        if missing
        else "every declared package is installed",
        data={"missing": missing},
        suggested_fixes=["stub these packages in the tests instead of importing them"] if missing else [],
    )


def plan_env_fix(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """What automatic recovery would do for a failed command, without doing it."""
    from ..envs import recovery

    plan = recovery.preview(
        args["argv"], args["log"], args.get("ecosystem") or ctx.ecosystem or None, ctx.workspace
    )
    if plan["diagnosis"] is None:
        return ToolResult(True, code="UNKNOWN", evidence="no known error pattern matched", data=plan)
    would = plan["would_apply"]
    return ToolResult(
        True,
        code=plan["diagnosis"]["code"],
        category=plan["diagnosis"]["category"],
        evidence=plan["diagnosis"]["evidence"],
        data=plan,
        next_actions=[f"the harness applies '{would['recipe']}' on its own: {would['note']}"]
        if would
        else [
            "nothing can be applied automatically: "
            + "; ".join(f"{c['recipe'] or 'no recipe'} ({c['status']})" for c in plan["considered"])
        ],
    )


def _diagnose(ctx: ToolContext, args: dict[str, Any], environment_only: bool) -> ToolResult:
    missing = env_check.missing_packages(ctx.workspace) if ctx.workspace is not None else []
    found = classify(args["log"], args.get("ecosystem") or ctx.ecosystem or None, Context(missing))
    if environment_only:
        found = [d for d in found if d.category in {ENVIRONMENT, INFRA}]
    if not found:
        return ToolResult(
            True,
            code="UNKNOWN",
            evidence="no known error pattern matched",
            next_actions=["read the log tail yourself", "report it to the human if it blocks progress"],
        )
    top = found[0]
    fixes = [top.hint, *(RECIPES[r].description for r in top.recipes if r in RECIPES)]
    actions = {
        "auto": [f"apply recipe {top.recipes[0]}"] if top.recipes else [],
        "auto_once": [f"apply recipe {top.recipes[0]} once"] if top.recipes else [],
        "ask": [
            "ask the human to approve: " + (RECIPES[top.recipes[0]].description if top.recipes else top.title)
        ],
        "manual": ["explain to the human what to change; the harness cannot do it"],
    }[top.autonomy]
    return ToolResult(
        True,
        code=top.code,
        category=top.category,
        evidence=top.evidence,
        data={
            "title": top.title,
            "autonomy": top.autonomy,
            "max_attempts": top.max_attempts,
            "environmental": is_environmental(found),
            "diagnoses": [d.to_dict() for d in found[:3]],
        },
        suggested_fixes=fixes,
        next_actions=actions,
    )


def diagnose_env_error(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    return _diagnose(ctx, args, environment_only=True)


def diagnose_exec_error(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    return _diagnose(ctx, args, environment_only=False)


_LOG = {"type": "string", "maxLength": 60_000, "description": "The failing command's output (tail is enough)"}
_ECOSYSTEM = {"type": "string", "enum": ECOSYSTEMS}
ENV_TOOLS = (
    ToolSpec("detect_stack", "Ecosystems, build files and test runner of the repository.", detect_stack_tool),
    ToolSpec(
        "detect_toolchains",
        "Toolchains (Python/JDK/Maven/Gradle/.NET) the repository needs and whether each is installed or would be downloaded.",
        detect_toolchains,
        object_schema(ecosystem=_ECOSYSTEM),
    ),
    ToolSpec(
        "provision_toolchain",
        "Download and install one toolchain into the harness cache. Refused unless the human approved the environment plan.",
        provision_toolchain,
        object_schema(
            ["kind", "version"],
            kind={"type": "string", "enum": KINDS},
            version={"type": "string", "minLength": 1},
        ),
        writes=True,
        downloads=True,
    ),
    ToolSpec(
        "list_missing_packages",
        "Declared dependencies that are not installed in the isolated environment.",
        list_missing_packages,
    ),
    ToolSpec(
        "plan_env_fix",
        "Preview which allow-listed recovery the harness would apply automatically to a failed setup command (nothing is executed).",
        plan_env_fix,
        object_schema(
            ["argv", "log"],
            argv={"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 40},
            log=_LOG,
            ecosystem=_ECOSYSTEM,
        ),
    ),
    ToolSpec(
        "diagnose_env_error",
        "Classify an install/build log: which environment problem it is, the recipe that fixes it and how far you may go alone.",
        diagnose_env_error,
        object_schema(["log"], log=_LOG, ecosystem=_ECOSYSTEM),
    ),
    ToolSpec(
        "diagnose_exec_error",
        "Classify a test-run log: environment problem, test defect (regenerate) or application defect (report, never weaken the test).",
        diagnose_exec_error,
        object_schema(["log"], log=_LOG, ecosystem=_ECOSYSTEM),
    ),
)

__all__ = [
    "ENV_TOOLS",
    "detect_stack_tool",
    "detect_toolchains",
    "diagnose_env_error",
    "diagnose_exec_error",
    "list_missing_packages",
    "plan_env_fix",
    "provision_toolchain",
]

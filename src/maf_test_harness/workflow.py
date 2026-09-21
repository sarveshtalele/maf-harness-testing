"""Checkpointed autonomous MAF workflow with one human review gate per stage."""

# NOTE: no `from __future__ import annotations` here; MAF inspects handler annotations at runtime.
import asyncio
import hashlib
import json
import os
import re
import shlex
import traceback
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_framework import (
    Executor,
    FileCheckpointStorage,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)
from filelock import FileLock

from .agent.personas import PERSONAS, persona_instructions
from .envs import Provisioned, ToolchainError, app_launcher, env_check, provision_for
from .envs.environment import build_environment_plan, steps_from_plan
from .envs.execution import copy_repo, write_generated
from .envs.runners import RunPlan, Step, build_plan, execute_plan, execute_setup
from .envs.toolchains import apply_toolchains
from .hooks import (
    ON_ENV_ERROR,
    ON_EXEC_ERROR,
    SESSION_START,
    STAGE_GATE,
    STOP,
    HookEngine,
    build_engine,
)
from .models import GeneratedFilesDraft, RequirementsDraft, TestDesignDraft, TestPlan
from .monitoring.telemetry import configure_telemetry
from .monitoring.trace import TraceRecorder
from .qa.packaging import build_deliverable
from .qa.report import build_qa_report, design_cases, render_markdown
from .qa.triage import remedy_for
from .repo.analyzer import (
    analyze_repository,
    build_repository_evidence,
    clone_repository,
    collaborator_signatures,
    declared_dependencies,
    extract_target_sources,
    validate_github_url,
)
from .repo.routes import extract_routes
from .repo.stack import detect_stack
from .settings import (
    AGENT_TOOLS,
    AUTOFIX,
    BUILD_GATE,
    EXECUTION_TIMEOUT_SECONDS,
    GENERATION_BATCH_SIZE,
    GENERATION_MAX_ATTEMPTS,
    HOOKS,
)
from .skills.config import skills_for
from .skills.contracts import (
    ensure_generated_case_markers,
    expand_generated_lines,
    extra_import_roots,
    finalize_design,
    finalize_discovered_requirements,
    flatten_nested_with,
    resolve_id,
    validate_generated_files,
)
from .skills.reports import run_skill_checks
from .tools.agent_tools import HarnessToolbox
from .tools.autofix import autofix_files
from .tools.build_gate import try_build
from .tools.code_intel import RepoContext, static_gate

CORE_STAGES = ["repo_analysis", "requirements", "test_planning", "test_design", "test_generation"]
ENV_STAGE = "environment_setup"
EXECUTION_STAGE = "test_execution"
STAGES = [
    "repo_analysis",
    "requirements",
    ENV_STAGE,
    "test_planning",
    "test_design",
    "test_generation",
    EXECUTION_STAGE,
]
TEST_GENERATION_BATCH_SIZE = GENERATION_BATCH_SIZE
TEST_GENERATION_MAX_ATTEMPTS = GENERATION_MAX_ATTEMPTS
MAX_HEAL_ROUNDS = 3
MODEL_REPAIR_ATTEMPTS = 3  # host-validation repair loop for requirements/planning/design
# Levels that need a running application; without one the design stage may only use in-process levels.
LIVE_APP_LEVELS = {"ui", "e2e", "accessibility"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def atomic_json(path: Path, data: dict):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def _canonicalize_case_ids(candidate: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """Rewrite garbled case IDs (see skill_contracts.resolve_id) to the approved ones before validation."""
    for item in candidate.get("files", []):
        item["case_ids"] = [resolve_id(cid, known) or cid for cid in item.get("case_ids", [])]
    return candidate


def _compact_repo(analysis: dict[str, Any], preferred: list[str]) -> dict[str, Any]:
    """Repository facts a generation call needs; the full inventory is not resent on every call."""
    sources = analysis.get("source_files", [])
    wanted = set(preferred)
    ordered = [f for f in sources if f in wanted] + [f for f in sources if f not in wanted]
    keys = (
        "repo_name",
        "commit_sha",
        "languages",
        "frameworks",
        "test_frameworks",
        "likely_test_directories",
        "has_tests",
    )
    return {
        **{k: analysis.get(k) for k in keys},
        "package_files": (analysis.get("package_files") or [])[:30],
        "source_files": ordered[:120],
        "source_files_total": len(sources),
        "existing_test_files": (analysis.get("test_files") or [])[:30],
    }


def _requirements_for(requirements: dict[str, Any], linked_ids: set[str]) -> dict[str, Any]:
    """Only the requirements, criteria, rules and contracts the batch's cases trace to (plus the actors)."""
    compact = _compact_requirements(requirements)
    if not linked_ids:
        return compact
    reqs = {r["id"] for r in compact["requirements"] if r["id"] in linked_ids}
    compact["requirements"] = [r for r in compact["requirements"] if r["id"] in reqs]
    for key in ("acceptance_criteria", "validation_rules"):
        compact[key] = [c for c in compact[key] if c["id"] in linked_ids or c.get("requirement_id") in reqs]
    compact["api_contracts"] = [c for c in compact["api_contracts"] if c["id"] in linked_ids]
    return compact


def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {k: plan.get(k) for k in ("objective", "recommended_frameworks", "setup_actions") if k in plan}


def _allowed_levels(state: dict[str, Any]) -> set[str]:
    base = {"unit", "integration", "api", "cli", "security", "performance"}
    return base | (LIVE_APP_LEVELS if state.get("app_url") else set())


def _compact_requirements(source: dict[str, Any]) -> dict[str, Any]:
    model = source.get("requirement_model", {})
    return {
        "app_name": source.get("app_name"),
        "summary": source.get("summary", ""),
        "actors": source.get("actors", []),
        "requirements": model.get("requirements", []),
        "validation_rules": model.get("validation_rules", []),
        "acceptance_criteria": model.get("acceptance_criteria", []),
        "api_contracts": source.get("api_contracts", []),
        "assumptions": source.get("assumptions", []),
        "gaps": source.get("gaps", []),
    }


def _compact_design(design: dict[str, Any]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for section in ("positive", "negative", "boundary", "validation", "contract"):
        for case in design.get(f"{section}_tests", []) or []:
            cases.append(
                {
                    "id": case.get("id"),
                    "type": section,
                    "title": case.get("title"),
                    "priority": case.get("priority"),
                    "level": case.get("level"),
                    "actor": case.get("actor"),
                    "target": case.get("target"),
                    "description": case.get("description", ""),
                    "preconditions": case.get("preconditions", []),
                    "data_notes": case.get("data_notes", ""),
                    "steps": case.get("steps", []),
                    "expected_result": case.get("expected_result"),
                    "traceability_links": case.get("traceability_links", []),
                }
            )
    return {"cases": cases, "gaps_report": design.get("gaps_report", {})}


_COMMON_LIBRARIES = {
    "com.jayway.restassured / io.restassured": "restassured",
    "org.jboss.arquillian": "arquillian",
    "org.mockito": "mockito",
    "org.assertj": "assertj",
    "org.hamcrest": "hamcrest",
    "org.testng": "testng",
}


def _undeclared_libraries(declared: list[str]) -> list[str]:
    """Popular test libraries the build files do NOT declare: a model reaches for them by habit."""
    text = " ".join(declared).lower().replace("-", "")
    return [name for name, token in _COMMON_LIBRARIES.items() if token not in text]


def _generation_policy(repo: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    languages = repo.get("languages") or {}
    dominant = max(languages, key=languages.get) if languages else "Unknown"
    state = state or {}
    stack = state.get("stack") or {}
    return {
        "dominant_language": dominant,
        "languages": languages,
        "application_frameworks": repo.get("frameworks", []),
        "detected_test_frameworks": repo.get("test_frameworks", []),
        "existing_test_files": (repo.get("test_files") or [])[:30],
        "likely_test_directories": repo.get("likely_test_directories", []),
        "package_files": (repo.get("package_files") or [])[:30],
        "python_import_roots": extra_import_roots(repo.get("source_files") or []),
        "declared_dependencies": (state.get("declared_dependencies") or [])[:120],
        "forbidden_imports": _undeclared_libraries(state.get("declared_dependencies") or []),
        "dependency_rule": (
            "Use only the JDK/standard library and the declared_dependencies. Any other import is rejected by the "
            "host before you are asked again, which wastes a call."
        ),
        "stack": {
            k: stack.get(k) for k in ("primary", "test_runner", "web_ui", "http_service") if k in stack
        },
        "live_app": {
            "available": bool(state.get("app_url")),
            "base_url_env_var": "BASE_URL",
            "base_url": state.get("app_url"),
            "routes": (state.get("routes") or [])[:60],
            "rule": (
                "UI/E2E/API tests against the running application read the target from the BASE_URL environment "
                "variable; never hard-code a host. BASE_URL ALREADY INCLUDES the application's context path: append "
                "only a route from `routes` (BASE_URL + '/rest/items'), never a guessed prefix such as '/petstore'. "
                "Use only the HTTP methods listed for a route and send bodies in the format the source shows."
            ),
        },
        "level_rules": {
            "unit": "Isolated, no network; mock boundaries but never the behavior under test.",
            "integration": "Real collaborators in-process; temp dirs/in-memory DBs only.",
            "api": "Use the framework's in-process test client when one exists, else BASE_URL.",
            "cli": "Invoke the real entry point via subprocess or the framework runner; assert exit code and output.",
            "jvm": (
                "`mvn test` runs without an application server: no Arquillian container, no CDI/EJB injection, no Bean "
                "Validation provider unless declared. Test plain classes with plain JUnit; for the live application use "
                'java.net.http.HttpClient with System.getenv("BASE_URL"). Call only constructors and methods that '
                "appear verbatim in target_source or collaborator_signatures; a class you cannot see is a class you must not "
                "instantiate. If a case cannot be tested without a container, test the nearest plain-Java behavior instead."
            ),
            "python": "Never nest more than 5 `with` blocks (CPython allows 20): use @patch decorators or contextlib.ExitStack.",
            "ui/e2e/accessibility": "Only with live_app.available; existing browser framework first, else Playwright.",
            "security": "Assert rejection/sanitization paths that the source shows.",
        },
        "framework_selection_rule": "Prefer detected test framework; otherwise choose a mainstream native framework compatible with the dominant stack.",
        "execution_policy": "Tests are executed by the host after human approval; write them to run unattended and deterministically.",
    }


def friendly_error(exc: BaseException) -> str:
    """A readable message for provider failures; everything else keeps its own text."""
    text = str(exc)
    status = re.search(r"Error code: (\d{3})", text)
    detail = re.search(r"'message': '([^']+)'", text)
    if status and status.group(1) == "429":
        return (
            f"The model gateway refused the request (HTTP 429): {detail.group(1) if detail else 'rate or quota limit'}. "
            "This is the provider's limit, not the harness's daily cap; retrying will not help until it resets."
        )
    if status:
        return (
            f"The model gateway returned HTTP {status.group(1)}: {detail.group(1) if detail else text[:200]}"
        )
    return text


class StageExecutor(Executor):
    def __init__(self, stage: str, host):
        super().__init__(id=stage)
        self.stage, self.host = stage, host

    @handler
    async def start(self, state: dict, ctx: WorkflowContext[dict, dict]):
        try:
            await self.host.enter(self.stage, state, ctx)
        except Exception as exc:
            self.host.audit(
                "stage_error", stage=self.stage, error=str(exc), trace=traceback.format_exc()[-1800:]
            )
            await self.host.ask(
                self.stage,
                "error",
                state,
                ctx,
                title="Stage failed; approved artifacts retained",
                error=friendly_error(exc),
            )

    @response_handler
    async def answer(self, original_request: dict, response: dict, ctx: WorkflowContext[dict, dict]):
        await self.host.answer(original_request.get("stage", self.stage), original_request, response, ctx)


class Harness:
    """Checkpointed autonomous MAF workflow with one human review gate per stage."""

    def __init__(
        self,
        run_dir: Path,
        *,
        skills_root: Path,
        runtime=None,
        execute: bool = True,
        cache_dir: Path | None = None,
        hooks: HookEngine | None = None,
    ):
        self.stages = list(STAGES if execute else CORE_STAGES)
        self.cache_dir = (cache_dir or Path(run_dir).resolve().parent / ".harness_cache").resolve()
        self.run_dir = run_dir.resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.skills_root = skills_root.resolve()
        self.runtime = runtime
        if AGENT_TOOLS and runtime is not None and getattr(runtime, "tool_provider", None) is None:
            runtime.tool_provider = HarnessToolbox(self._repo_context, self._build_files).tools_for
        if runtime is not None and getattr(runtime, "recorder", None) is None:
            runtime.recorder = TraceRecorder(self.run_dir / "trace.jsonl")
        self.view_path = self.run_dir / "run.json"
        self.view = (
            json.loads(self.view_path.read_text())
            if self.view_path.exists()
            else {
                "status": "new",
                "pending": None,
                "consumed": [],
                "checkpoint_id": None,
            }
        )
        self.state = self.view.get("state", {})
        # Hooks: built-ins + .harness/hooks.json (see docs/HOOKS.md). A malformed config stops the run before it starts.
        self.hooks = hooks or build_engine(
            context={"writable_roots": [str(self.workspace)], "allow_downloads": False},
            audit=self.audit,
            setting=HOOKS,
        )
        if runtime is not None and getattr(runtime, "hooks", None) is None:
            runtime.hooks = self.hooks
        self.hooks.notify(SESSION_START, run=self.run_dir.name, resumed=bool(self.state))
        self.storage = FileCheckpointStorage(self.run_dir / "checkpoints")
        executors = [StageExecutor(stage, self) for stage in self.stages]
        builder = WorkflowBuilder(
            start_executor=executors[0], name="maf-test-harness", checkpoint_storage=self.storage
        )
        for current, following in zip(executors, executors[1:], strict=False):
            builder.add_edge(current, following)
        if EXECUTION_STAGE in self.stages:  # human-approved heal loop: failing tests go back to generation
            builder.add_edge(executors[-1], executors[self.stages.index("test_generation")])
        self.workflow = builder.build()
        self._restored = False

    def save(self):
        self.view["state"] = self.state
        atomic_json(self.view_path, self.view)

    def audit(self, event: str, **details):
        with (self.run_dir / "audit.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "event": event,
                        "at": __import__("datetime")
                        .datetime.now(__import__("datetime").timezone.utc)
                        .isoformat(),
                        **details,
                    },
                    default=str,
                )
                + "\n"
            )

    async def _collect(self, result):
        requests = result.get_request_info_events()
        checkpoint = await self.storage.get_latest(workflow_name=self.workflow.name)
        if checkpoint:
            self.view["checkpoint_id"] = checkpoint.checkpoint_id
        if requests:
            event = requests[0]
            self.view["pending"] = event.data
            self.state = event.data["state"]
            self.view["status"] = "waiting"
            self.view.pop("error", None)
        else:
            self.view["pending"] = None
            if self.view["status"] != "stopped":
                self.view["status"] = "complete"
                self.hooks.notify(STOP, reason="complete")
        self.save()
        return self.view

    async def start(self, repo_url: str, app_url: str | None = None, start_app: bool = False):
        if self.view["status"] != "new":
            raise ValueError("Run already started. Resume its pending request.")
        _, _, _, ref = validate_github_url(repo_url, with_ref=True)
        if app_url is not None and not re.fullmatch(
            r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/\S*)?", app_url.strip()
        ):
            raise ValueError(
                "app_url must be a local http(s) URL such as http://localhost:8080 (tests run on this host)."
            )
        self.state = {
            "repo_url": repo_url.strip(),
            "repo_ref": ref,
            "app_url": app_url.strip() if app_url else None,
            "launch_app": bool(start_app and not app_url),
            "stages": self.stages,
            "artifacts": {},
            "revisions": {},
            "sessions": {},
            "model_requests": {},
            "feedback": {},
            "approved": {},
            "telemetry": configure_telemetry(),
            "budget": {
                "charged": 0,
                "calls": 0,
                "reported_tokens": 0,
            },
        }
        if self.state["launch_app"]:  # the harness starts the app itself; UI/E2E levels become available
            self.state["app_url"] = f"http://127.0.0.1:{app_launcher.free_port()}"
        result = await self.workflow.run(self.state)
        self._restored = True
        return await self._collect(result)

    async def respond(self, request_id: str, response: dict):
        with FileLock(str(self.run_dir / "run.lock")):
            fresh = json.loads(self.view_path.read_text())
            pending = fresh.get("pending")
            if not pending or pending["id"] != request_id or request_id in fresh.get("consumed", []):
                raise ValueError("Stale or already consumed approval request.")
            gate = await asyncio.to_thread(
                self.hooks.notify,
                STAGE_GATE,
                pending["stage"],
                kind=pending["kind"],
                decision=response.get("decision"),
                request_id=request_id,
            )
            if gate.blocked:  # the approval stays pending; the human sees why and can change course
                raise PermissionError(f"Blocked by hook ({gate.source}): {gate.reason}")
            self.view = fresh
            self.state = pending["state"]
            self.view["consumed"].append(request_id)
            self.save()
            self.audit(
                "human_response",
                request_id=request_id,
                stage=pending["stage"],
                decision=response.get("decision"),
                request_hash=pending["hash"],
            )
            try:
                result = await self.workflow.run(
                    responses={request_id: response},
                    checkpoint_id=None if self._restored else self.view["checkpoint_id"],
                )
                self._restored = True
                return await self._collect(result)
            except Exception as exc:
                self.view["status"] = "interrupted"
                self.view["error"] = str(exc)
                self.save()
                raise

    async def ask(self, stage, kind, state, ctx, *, title="", details=None, error=None):
        self.state = state
        request_id = uuid4().hex
        request = {
            "id": request_id,
            "stage": stage,
            "kind": kind,
            "title": title,
            "details": details or {},
            "error": error,
            "state": state,
        }
        request["hash"] = digest(request)
        await ctx.request_info(request, dict, request_id=request_id)
        self.audit(
            "requested",
            request_id=request_id,
            stage=stage,
            kind=kind,
            details=details,
            request_hash=request["hash"],
        )

    async def enter(self, stage, state, ctx):
        if stage == "repo_analysis":
            await self.ask(
                stage,
                "tool",
                state,
                ctx,
                title="Approve repository clone and static inspection",
                details={
                    "tool": "clone_repository",
                    "url": state["repo_url"],
                    "ref": state.get("repo_ref") or "default branch",
                    "destination": str(self.run_dir / "repo"),
                    "effects": "Network clone and local read only. Repository code is never executed.",
                },
            )
            return
        state["active_stage"] = stage
        self.state = state
        self.save()
        if stage == ENV_STAGE:
            await self.plan_environment(state, ctx)
            return
        if stage == EXECUTION_STAGE:
            await self.plan_execution(state, ctx)
            return
        await self.model(stage, state, ctx)

    async def review(self, stage, artifact, state, ctx):
        revision = state["revisions"].get(stage, 0) + 1
        state["revisions"][stage] = revision
        state["artifacts"][stage] = artifact
        atomic_json(self.run_dir / f"{stage}-{revision}.json", artifact)
        persona = PERSONAS[stage]
        await self.ask(
            stage,
            "review",
            state,
            ctx,
            title=f"{persona.name} plan · revision {revision}",
            details={
                "artifact": artifact,
                "revision": revision,
                "artifact_hash": digest(artifact),
                "persona": persona.name,
                "role": persona.role,
                "goal": persona.goal,
            },
        )

    async def answer(self, stage, request, response, ctx):
        state = request["state"]
        self.state = state
        decision = response.get("decision")
        kind = request["kind"]
        if decision == "stop":
            self.view["status"] = "stopped"
            await ctx.yield_output({"status": "stopped", "stage": stage})
            return
        try:
            if kind == "review":
                if decision == "revise" and stage == EXECUTION_STAGE:
                    await self.heal(state, str(response.get("feedback", "")).strip(), ctx)
                    return
                if decision == "revise" and stage == ENV_STAGE:  # e.g. after installing a missing system tool
                    state["artifacts"].pop(stage, None)
                    await self.plan_environment(state, ctx)
                    return
                if decision == "revise":
                    feedback = str(response.get("feedback", "")).strip()
                    if not feedback:
                        raise ValueError("Revision instructions are required.")
                    state.setdefault("feedback", {})[stage] = feedback
                    if stage == "test_generation":
                        state.pop("accepted_generation_batches", None)
                        state.pop("heal", None)
                    for downstream in self.stages[self.stages.index(stage) + 1 :]:
                        state["artifacts"].pop(downstream, None)
                        state.get("approved", {}).pop(downstream, None)
                        state["sessions"].pop(downstream, None)
                    state["sessions"].pop(stage, None)
                    if stage == "repo_analysis":
                        revised = dict(state["artifacts"][stage])
                        notes = list(revised.get("human_review_notes") or [])
                        notes.append(feedback)
                        revised["human_review_notes"] = notes
                        await self.review(stage, revised, state, ctx)
                    else:
                        await self.model(stage, state, ctx)
                    return
                if decision != "approve":
                    raise ValueError("Choose approve, revise, or stop.")
                if digest(state["artifacts"][stage]) != request["details"]["artifact_hash"]:
                    raise ValueError("Artifact changed since review.")
                state.setdefault("approved", {})[stage] = request["details"]["artifact_hash"]
                self.audit(
                    "artifact_approved",
                    stage=stage,
                    revision=state["revisions"][stage],
                    artifact_hash=state["approved"][stage],
                )
                if stage == self.stages[-1]:
                    state["completed_reason"] = self._completion_reason(state, stage)
                    await self._finish(state, ctx)
                else:
                    await ctx.send_message(state)
                return

            if kind == "error":
                if decision != "retry":
                    raise ValueError("Choose retry or stop.")
                state["sessions"].pop(stage, None)
                state["model_requests"].pop(stage, None)
                await self.enter(stage, state, ctx)
                return

            if kind != "tool" or decision not in {"approve", "deny", "skip"}:
                raise ValueError("Explicit tool approval is required.")
            if decision == "skip" and stage == EXECUTION_STAGE:
                state["artifacts"][stage] = self._execution_artifact(state, None)
                state["completed_reason"] = (
                    "Test execution was skipped by the reviewer; generated tests were not run."
                )
                self.audit("execution_skipped", request_id=request["id"])
                await self._finish(state, ctx)
                return
            if decision == "skip" and stage == ENV_STAGE:
                self.audit("environment_skipped", request_id=request["id"])
                await self.review(
                    stage, self._environment_artifact(state, {"status": "skipped", "steps": []}), state, ctx
                )
                return
            if decision == "skip":
                raise ValueError("Only environment setup and test execution can be skipped.")
            if stage == ENV_STAGE and decision == "approve":
                await self.run_environment(state, request, ctx)
                return
            if stage == EXECUTION_STAGE and decision == "approve":
                await self.run_execution(state, request, ctx)
                return
            if decision == "deny":
                await self.ask(
                    stage,
                    "error",
                    state,
                    ctx,
                    title="Action denied",
                    error="Nothing was accessed or executed. Retry to be asked again, or stop the workflow.",
                )
                return
            if request["details"].get("tool") != "clone_repository":
                raise ValueError("This workflow only permits the repository clone tool.")
            self.audit("tool_started", tool="clone_repository", request_id=request["id"])
            _, _, repo_name = validate_github_url(state["repo_url"])
            await asyncio.to_thread(clone_repository, state["repo_url"], self.run_dir / "repo")
            artifact = await asyncio.to_thread(
                analyze_repository, self.run_dir / "repo", state["repo_url"], repo_name
            )
            state["stack"] = (
                await asyncio.to_thread(detect_stack, self.run_dir / "repo", artifact.languages)
            ).to_dict()
            state["declared_dependencies"] = await asyncio.to_thread(
                declared_dependencies, self.run_dir / "repo", artifact.package_files
            )
            state["evidence"] = build_repository_evidence(
                self.run_dir / "repo", max_files=60, max_total_chars=120_000
            )
            await self.review(stage, artifact.model_dump(), state, ctx)
            self.audit("tool_completed", tool="clone_repository", request_id=request["id"])
        except Exception as exc:
            self.audit("stage_error", stage=stage, error=str(exc), trace=traceback.format_exc()[-1800:])
            await self.ask(
                stage,
                "error",
                state,
                ctx,
                title="Stage blocked; last valid artifacts retained",
                error=str(exc),
            )

    async def _finish(self, state: dict, ctx) -> None:
        """Package the repository with all generated tests plus the evidence, then end the workflow."""
        if "test_generation" in state["artifacts"]:
            state["deliverable_zip"] = str(await asyncio.to_thread(build_deliverable, self.run_dir, state))
            self.audit("deliverable_created", path=state["deliverable_zip"])
        await ctx.yield_output(state)

    def _completion_reason(self, state: dict, stage: str) -> str:
        if stage == EXECUTION_STAGE:
            qa = state["artifacts"][EXECUTION_STAGE]["qa_report"]
            return (
                f"QA verdict {qa['verdict']} (execution {qa['execution_status']}). "
                "Report saved as qa_report.json / qa_report.md in the run directory."
            )
        return (
            "Test source generated and statically validated against every approved design case. "
            "This run was configured without the execution stage; tests were not run."
        )

    def _execution_artifact(self, state: dict, report: dict | None) -> dict:
        arts = state["artifacts"]
        skill_checks = {
            stage: trace.get("skill_loaded_via_maf")
            for stage, trace in state.get("skill_tool_trace", {}).items()
        }
        qa = build_qa_report(
            repo=arts["repo_analysis"],
            requirements=arts["requirements"],
            design=arts["test_design"],
            generation=arts["test_generation"],
            execution=report,
            stack=state.get("stack"),
            environment=state.get("environment", {}).get("plan"),
            provenance={
                "runtime": state.get("runtime_provenance"),
                "skills": state.get("skill_provenance"),
                "skill_loaded_via_maf_tool": skill_checks,
                "budget": state.get("budget"),
            },
        )
        execution = dict(report or {"status": "not_run"})
        skill_reports = run_skill_checks(
            self.skills_root, arts["requirements"], arts["test_design"], arts["test_generation"]
        )
        qa["skill_checks"] = skill_reports["checks"]
        environment = state.get("environment", {})
        qa["environment"] = {
            "status": environment.get("status", "not_planned"),
            "ecosystem": environment.get("plan", {}).get("ecosystem"),
        }
        return {
            "qa_report": qa,
            "execution": execution,
            "markdown": render_markdown(qa),
            "skill_reports": {
                "traceability": skill_reports["traceability"],
                "coverage": skill_reports["coverage"],
            },
        }

    @property
    def workspace(self) -> Path:
        return self.run_dir / "workspace"

    async def _ensure_workspace(self) -> Path:
        """One persistent workspace per run: repository copy + isolated environment + generated tests."""
        if not self.workspace.exists():
            await asyncio.to_thread(copy_repo, self.run_dir / "repo", self.workspace)
        return self.workspace

    def _environment_artifact(self, state: dict, report: dict) -> dict:
        plan = state["environment"]["plan"]
        return {
            "status": report["status"],
            "ecosystem": plan["ecosystem"],
            "isolation": plan["isolation"],
            "prerequisites": plan["prerequisites"],
            "toolchains": state.get("toolchains") or plan.get("toolchains", []),
            "notes": plan["notes"],
            "report": report,
            "installed_steps": [s["name"] for s in report.get("steps", [])],
        }

    async def plan_environment(self, state: dict, ctx) -> None:
        """Show what must be installed and how (isolated); nothing is installed before approval."""
        await self._ensure_workspace()
        stack = await asyncio.to_thread(detect_stack, self.workspace)
        plan = await asyncio.to_thread(
            build_environment_plan,
            stack,
            self.workspace,
            self.cache_dir,
            EXECUTION_TIMEOUT_SECONDS,
            state["artifacts"].get("requirements", {}).get("environment_needs"),
        )
        state["environment"] = {"plan": plan, "hash": digest(plan), "status": "planned"}
        if (
            plan["missing_prerequisites"] or not plan["steps"]
        ):  # nothing installable: report, let a human decide
            report = {"status": "blocked" if plan["missing_prerequisites"] else "not_needed", "steps": []}
            state["environment"]["status"] = report["status"]
            await self.review(ENV_STAGE, self._environment_artifact(state, report), state, ctx)
            return
        await self.ask(
            ENV_STAGE,
            "tool",
            state,
            ctx,
            title="Approve creating an isolated environment and installing dependencies",
            details={
                "tool": "install_dependencies",
                "plan_hash": state["environment"]["hash"],
                "ecosystem": plan["ecosystem"],
                "isolation": plan["isolation"],
                "workspace": str(self.workspace),
                "prerequisites": plan["prerequisites"],
                "toolchains": plan.get("toolchains", []),
                "steps": [
                    {"name": st["name"], "kind": st["kind"], "command": shlex.join(st["argv"])}
                    for st in plan["steps"]
                ],
                "effects": (
                    "Downloads and installs the repository's dependencies into the isolated location above "
                    "(network access; package installers run). "
                    + (
                        "Missing toolchains listed under 'toolchains' (status download) are fetched into the harness "
                        "toolchain cache with checksum verification; nothing system-wide changes. "
                        if plan.get("needs_download")
                        else "System tools are never installed by the harness. "
                    )
                    + "Model credentials are not passed to these commands."
                ),
            },
        )

    async def run_environment(self, state: dict, request: dict, ctx) -> None:
        info = state["environment"]
        if request["details"].get("plan_hash") != info["hash"]:
            raise ValueError("Environment plan changed after it was shown for approval.")
        attempt = state["environment_attempts"] = state.get("environment_attempts", 0) + 1
        self.hooks.context["allow_downloads"] = True  # the approved plan lists every toolchain download
        self.audit("environment_started", request_id=request["id"], plan_hash=info["hash"])
        try:  # the approval covers downloading the toolchains listed in the plan
            provisioned = await asyncio.to_thread(
                provision_for, self.workspace, info["plan"]["ecosystem"], allow_download=True
            )
        except ToolchainError as exc:
            self.audit("toolchains_failed", error=str(exc)[:600])
            provisioned = None
            report = {"status": "setup_failed", "steps": [], "warnings": [], "error": str(exc)}
        if provisioned is not None:
            state["toolchains"] = provisioned.to_dicts()
            self.audit("toolchains_ready", events=provisioned.events)
            steps = steps_from_plan(info["plan"])
            apply_toolchains(steps, provisioned)
            report = await asyncio.to_thread(
                execute_setup, steps, self.workspace, self.run_dir / "environment" / f"attempt-{attempt}"
            )
        info["status"] = report["status"]
        if report["status"] != "ready":
            self.hooks.notify(
                ON_ENV_ERROR,
                ENV_STAGE,
                status=report["status"],
                error=str(report.get("error") or "")[:600],
                diagnoses=[d for step in report.get("steps", []) for d in step.get("diagnoses", [])][:5],
                attempt=attempt,
            )
        if report["status"] == "ready":
            info["missing_packages"] = await asyncio.to_thread(env_check.missing_packages, self.workspace)
            if info["missing_packages"]:
                report.setdefault("warnings", []).append(
                    "Declared dependencies NOT installed: "
                    + ", ".join(info["missing_packages"])
                    + ". Tests importing them cannot run until they are installed (build tools may be needed)."
                )
        self.audit(
            "environment_completed",
            status=report["status"],
            warnings=report.get("warnings"),
            missing_packages=info.get("missing_packages"),
        )
        await self.review(ENV_STAGE, self._environment_artifact(state, report), state, ctx)

    async def plan_execution(self, state: dict, ctx) -> None:
        arts = state["artifacts"]
        generated = arts["test_generation"]
        attempt = state["execution_attempts"] = state.get("execution_attempts", 0) + 1
        workspace = await self._ensure_workspace()
        state["workspace_generated"] = await asyncio.to_thread(
            write_generated,
            workspace,
            generated["files"],
            self.run_dir / "repo",
            state.get("workspace_generated"),
        )
        stack = await asyncio.to_thread(detect_stack, workspace)
        env_ready = state.get("environment", {}).get("status") == "ready"
        plan = build_plan(
            stack,
            workspace,
            generated["files"],
            cache_dir=self.cache_dir,
            timeout=EXECUTION_TIMEOUT_SECONDS,
            env_ready=env_ready,
            **(
                {"assume_present": covered}
                if (covered := Provisioned.from_dicts(state.get("toolchains")).tools())
                else {}
            ),
        )
        if state.get("launch_app") and not plan.blocked_reason:
            state["app_url"] = f"http://127.0.0.1:{app_launcher.free_port()}"  # fresh port for every attempt
            port = int(state["app_url"].rsplit(":", 1)[1])
            plan.app = await asyncio.to_thread(app_launcher.detect_launch, workspace, port)
            if plan.app is None:
                plan.blocked_reason = (
                    "The harness could not tell how to start this application (no Streamlit/FastAPI/Flask entry "
                    "point or npm start script found). Start it yourself and supply its URL for UI/E2E cases."
                )
        if state.get("app_url"):
            for step in plan.steps:
                if step.kind == "test":
                    step.env["BASE_URL"] = state["app_url"]
        provisioned = Provisioned.from_dicts(state.get("toolchains"))
        apply_toolchains(plan.steps, provisioned)
        plan_dict = plan.to_dict()
        state["execution_plan"] = {
            "workspace": str(workspace),
            "plan": plan_dict,
            "hash": digest(plan_dict),
            "attempt": attempt,
        }
        if plan.blocked_reason:  # nothing to approve: record an honest inconclusive report
            report = await asyncio.to_thread(
                execute_plan, plan, workspace, self.run_dir / "executions" / f"attempt-{attempt}"
            )
            await self.review(EXECUTION_STAGE, self._execution_artifact(state, report), state, ctx)
            return
        await self.ask(
            EXECUTION_STAGE,
            "tool",
            state,
            ctx,
            title="Approve running the generated tests on this machine",
            details={
                "tool": "run_generated_tests",
                "plan_hash": state["execution_plan"]["hash"],
                "adapter": plan.adapter,
                "workspace": str(workspace),
                "app_url": state.get("app_url"),
                "environment": "ready (isolated, installed earlier)"
                if env_ready
                else "not prepared: setup steps included",
                "steps": [
                    *(
                        [{"name": "start-app", "kind": "app", "command": shlex.join(plan.app["argv"])}]
                        if plan.app
                        else []
                    ),
                    *(
                        {"name": st.name, "kind": st.kind, "command": shlex.join(st.argv)}
                        for st in plan.steps
                    ),
                ],
                "effects": (
                    (
                        "Starts the application under test on "
                        + str(state.get("app_url"))
                        + " for the duration of "
                        "the run, then stops it. "
                        if plan.app
                        else ""
                    )
                    + "Runs the generated tests as your user; repository code executes. Local process only, "
                    "NOT a security sandbox. Model credentials are not passed to these commands."
                ),
            },
        )

    async def run_execution(self, state: dict, request: dict, ctx) -> None:
        info = state["execution_plan"]
        if request["details"].get("plan_hash") != info["hash"]:
            raise ValueError("Execution plan changed after it was shown for approval.")
        plan = RunPlan(
            adapter=info["plan"]["adapter"],
            steps=[Step(**st) for st in info["plan"]["steps"]],
            notes=info["plan"].get("notes", []),
            app=info["plan"].get("app"),
        )
        out_dir = self.run_dir / "executions" / f"attempt-{info['attempt']}"
        self.audit(
            "execution_started", request_id=request["id"], adapter=plan.adapter, plan_hash=info["hash"]
        )
        report = await asyncio.to_thread(
            self._execute_with_app, plan, Path(info["workspace"]), out_dir, state, info
        )
        self.audit(
            "execution_completed",
            adapter=plan.adapter,
            status=report["status"],
            summary=report.get("summary"),
        )
        if report["status"] != "passed":
            self.hooks.notify(
                ON_EXEC_ERROR,
                EXECUTION_STAGE,
                status=report["status"],
                summary=report.get("summary"),
                diagnoses=(report.get("diagnoses") or [])[:5],
                attempt=info["attempt"],
            )
        await self.review(EXECUTION_STAGE, self._execution_artifact(state, report), state, ctx)

    def _execute_with_app(
        self, plan: RunPlan, workspace: Path, out_dir: Path, state: dict, info: dict
    ) -> dict:
        generated = state.get("workspace_generated")
        if not plan.app:
            return execute_plan(plan, workspace, out_dir, generated)
        log_dir = out_dir.with_name(out_dir.name + "-app")
        try:
            with app_launcher.running(plan.app, workspace, state["app_url"], log_dir):
                self.audit("app_started", kind=plan.app["kind"], url=state["app_url"])
                return execute_plan(plan, workspace, out_dir, generated)
        except RuntimeError as exc:  # the application never came up: honest, inconclusive report
            self.audit("app_failed", error=str(exc)[:600])
            return execute_plan(
                RunPlan(plan.adapter, [], blocked_reason=str(exc)), workspace, out_dir, generated
            )

    async def heal(self, state: dict, feedback: str, ctx) -> None:
        """Human-approved regeneration of only the failing/unexecuted tests, with failure evidence."""
        rounds = state.get("heal_rounds", 0)
        if rounds >= MAX_HEAL_ROUNDS:
            raise ValueError(f"Regeneration limit ({MAX_HEAL_ROUNDS}) reached; approve, skip or stop.")
        qa = state["artifacts"][EXECUTION_STAGE]["qa_report"]
        failed = [c for c in qa["cases"] if c["status"] in {"failed", "error", "not_executed"}]
        if not failed:
            raise ValueError("No failing or unexecuted tests to regenerate; approve or stop instead.")
        state["heal_rounds"] = rounds + 1
        evidence = "\n".join(
            f"- {c['id']} ({c['title']}): {c['status']} {c['message'][:500]}"
            + (f"\n  REMEDY: {remedy_for(c['message'])}" if remedy_for(c["message"]) else "")
            for c in failed
        )
        state.setdefault("feedback", {})["test_generation"] = (
            (feedback + "\n" if feedback else "")
            + "Fix ONLY these failing generated tests from the execution evidence. Never weaken or delete "
            "assertions to make them pass; if the application is wrong, keep the assertion.\n" + evidence
        )
        failed_ids = {c["id"] for c in failed}
        state["heal"] = {"failed_case_ids": sorted(failed_ids), "round": state["heal_rounds"]}
        for stage in ("test_generation", EXECUTION_STAGE):
            state["artifacts"].pop(stage, None)
            state.get("approved", {}).pop(stage, None)
        self.audit("heal_started", round=state["heal_rounds"], failed_case_ids=sorted(failed_ids))
        await ctx.send_message(state)  # graph edge test_execution -> test_generation

    async def model(self, stage, state, ctx):
        if self.runtime is None:
            raise ValueError("Configure API key/model before running this stage, then retry.")

        if stage == "requirements":
            response_format = RequirementsDraft
            assignment = (
                "Reverse engineer a compact set of observable testable requirements from the repository. "
                "Infer routine details from evidence; put uncertainty in assumptions/gaps rather than asking the user."
            )
            upstream: dict[str, Any] = {}
        elif stage == "test_planning":
            response_format = TestPlan
            assignment = (
                "Create the cross-language test implementation plan from the approved inferred requirements and repository facts. "
                "Select native test frameworks automatically and keep the plan compact."
            )
            upstream = {"requirements": _compact_requirements(state["artifacts"]["requirements"])}
        elif stage == "test_design":
            response_format = TestDesignDraft
            assignment = (
                "Create traceable implementation-ready test cases in the compact draft schema. Cover approved requirements "
                "with the fewest non-duplicative cases needed; do not manufacture boundaries or contracts."
            )
            upstream = {
                "requirements": _compact_requirements(state["artifacts"]["requirements"]),
                "test_plan": state["artifacts"]["test_planning"],
            }
        elif stage == "test_generation":
            artifact = await self._generate_tests_batched(state)
            await self.review(stage, artifact, state, ctx)
            return
        else:
            raise ValueError(f"Unsupported model stage: {stage}")

        schema = response_format.model_json_schema()
        evidence_paths = re.findall(r"--- FILE: (.*?) ---", state.get("evidence", ""))
        repair = ""
        for attempt in range(1, MODEL_REPAIR_ATTEMPTS + 1):
            prompt = json.dumps(
                {
                    "assignment": assignment,
                    "repository": state["artifacts"]["repo_analysis"],
                    "approved_inputs": upstream,
                    "repository_evidence": state.get("evidence", ""),
                    "evidence_file_paths": evidence_paths if stage == "requirements" else None,
                    "stack_profile": state.get("stack", {}),
                    "live_app": {
                        "available": bool(state.get("app_url")),
                        "allowed_test_levels": sorted(_allowed_levels(state)),
                    },
                    "revision_feedback": state.get("feedback", {}).get(stage),
                    "repair_feedback": repair or None,
                    "output_contract": schema,
                    "json_rules": {
                        "single_complete_object": True,
                        "markdown_fences": False,
                        "duplicate_list_items": False,
                        "respect_schema_limits": True,
                        "manual_questions": False,
                    },
                },
                separators=(",", ":"),
            )
            skillset = skills_for(stage, state)
            result = await self.runtime.run_stage(
                stage=stage,
                skill_name=skillset.primary,
                companions=skillset.companions,
                persona=persona_instructions(stage),
                prompt=prompt,
                state=state,
                persist=self.save,
                response_format=response_format,
            )
            if result["status"] == "needs_input":
                raise ValueError(
                    "The agent requested manual configuration, but this workflow is autonomous. "
                    "Retry so uncertain details are recorded as assumptions/gaps instead."
                )
            if result["status"] != "completed":
                raise ValueError(f"Unexpected model status: {result['status']}")
            state["draft"] = result["artifact"]
            state["design_attempt"] = attempt
            try:
                artifact = await self.validate(stage, state)
            except ValueError as exc:  # temperature 0 repeats itself, so the retry must carry the reason
                self.audit("stage_repair", stage=stage, attempt=attempt, error=str(exc)[:600])
                if attempt == MODEL_REPAIR_ATTEMPTS:
                    raise
                repair = (
                    f"Attempt {attempt} was rejected by host validation: {str(exc)[:900]}. Return a corrected "
                    "complete JSON object that fixes exactly this problem and keeps everything else valid."
                )
                continue
            break
        await self.review(stage, artifact, state, ctx)

    async def _generate_tests_batched(self, state: dict) -> dict:
        """Generate approved design cases in small validated batches with bounded repair retries."""

        compact = _compact_design(state["artifacts"]["test_design"])
        cases = [case for case in compact.get("cases", []) if case.get("id")]
        if not cases:
            raise ValueError("Approved test design contains no actionable case IDs.")

        self.state = state
        signature = digest(
            {
                "cases": cases,
                "repo": state["artifacts"]["repo_analysis"],
                "requirements": state["artifacts"].get("requirements"),
                "plan": state["artifacts"].get("test_planning"),
                "evidence": state.get("evidence"),
                "batch_size": TEST_GENERATION_BATCH_SIZE,
                "skills": {
                    name: (self.skills_root / name / "SKILL.md").read_text()
                    for name in skills_for("test_generation", state).all
                },
            }
        )
        cache = state.setdefault("accepted_generation_batches", {})
        if cache.get("signature") != signature:
            cache.clear()
            cache.update(signature=signature, batches={})
        state["routes"] = await asyncio.to_thread(
            extract_routes, self.run_dir / "repo", state["artifacts"]["repo_analysis"].get("source_files", [])
        )
        policy = _generation_policy(state["artifacts"]["repo_analysis"], state)
        failed_ids = {str(i) for i in (state.get("heal") or {}).get("failed_case_ids", [])}
        schema = GeneratedFilesDraft.model_json_schema()
        accepted_files: list[dict[str, Any]] = []
        accepted_paths: set[str] = set()
        covered: set[str] = set()
        languages: list[str] = []
        frameworks: list[str] = []
        rationales: list[str] = []
        raw_batches: list[str] = []

        total_batches = (len(cases) + TEST_GENERATION_BATCH_SIZE - 1) // TEST_GENERATION_BATCH_SIZE
        for batch_index, start in enumerate(range(0, len(cases), TEST_GENERATION_BATCH_SIZE), 1):
            batch = cases[start : start + TEST_GENERATION_BATCH_SIZE]
            batch_ids = {str(case["id"]) for case in batch}
            requirements = state["artifacts"].get("requirements", {})
            linked_ids = {link["id"] for case in batch for link in case.get("traceability_links", [])}
            preferred = [
                path
                for req in requirements.get("requirement_model", {}).get("requirements", [])
                if req.get("id") in linked_ids
                for path in req.get("source_files", [])
            ]
            target_source = extract_target_sources(
                self.run_dir / "repo",
                [str(case.get("target") or "") for case in batch],
                state["artifacts"]["repo_analysis"].get("source_files", []),
            )
            # The complete target code is supplied separately, so the head-truncated excerpts can be smaller.
            evidence = build_repository_evidence(
                self.run_dir / "repo",
                max_files=10 if target_source else 16,
                max_total_chars=24_000 if target_source else 40_000,
                preferred_paths=preferred,
            )
            repair_feedback = ""
            accepted = cache["batches"].get(str(batch_index))
            work, kept_files = batch, []
            if accepted is not None:
                cached_ids = {i for f in accepted["files"] for i in f["case_ids"]}
                regenerate = batch_ids & failed_ids
                if not cached_ids >= batch_ids:
                    accepted = None
                elif regenerate:
                    # Heal: only the failing cases go back to the model; passing files are kept byte for byte.
                    kept_files = [f for f in accepted["files"] if not set(f["case_ids"]) & regenerate]
                    work = [c for c in batch if str(c["id"]) in regenerate]
                    accepted = None
                elif validate_generated_files(accepted, batch_ids)["uncovered_case_ids"]:
                    accepted = None
            work_ids = {str(case["id"]) for case in work}
            source_files = state["artifacts"]["repo_analysis"].get("source_files", [])
            batch_source = (
                extract_target_sources(
                    self.run_dir / "repo", [str(case.get("target") or "") for case in work], source_files
                )
                or target_source
            )
            collaborators = collaborator_signatures(self.run_dir / "repo", batch_source, source_files)
            rounds = state.setdefault("generation_rounds", {})
            for attempt in range(1, 1 if accepted is not None else TEST_GENERATION_MAX_ATTEMPTS + 1):
                # A deterministic model repeats a rejected answer verbatim: vary the sampling on every retry.
                retry_temperature = min(0.8, 0.3 * (attempt - 1) + 0.25 * rounds.get(str(batch_index), 0))
                prompt = json.dumps(
                    {
                        "assignment": (
                            "Generate executable test source ONLY for this approved batch. Generate exactly one file per "
                            "supplied case ID and exactly one concrete test declaration in that file. Set case_ids to a one-item "
                            "array containing the corresponding supplied ID. The host adds canonical CASE_ID source markers. "
                            "Use only necessary imports. Never emit placeholders, install dependencies, or execute tests."
                        ),
                        "repository": _compact_repo(state["artifacts"]["repo_analysis"], preferred),
                        "approved_inputs": {
                            "approved_cases": work,
                            "requirements": _requirements_for(requirements, linked_ids),
                            "test_plan": _plan_summary(state["artifacts"].get("test_planning", {})),
                            "generation_policy": policy,
                            "batch": {
                                "index": batch_index,
                                "total": total_batches,
                                "path_rule": (
                                    f"Use unique batch-specific test file names containing batch_{batch_index:02d} "
                                    "when no existing repository test path clearly applies."
                                ),
                            },
                        },
                        "target_source": batch_source,
                        "collaborator_signatures": collaborators or None,
                        "repository_evidence": evidence or state.get("evidence", ""),
                        "revision_feedback": state.get("feedback", {}).get("test_generation"),
                        "repair_feedback": repair_feedback or None,
                        "output_contract": schema,
                        "json_rules": {
                            "single_complete_object": True,
                            "markdown_fences": False,
                            "duplicate_list_items": False,
                            "respect_schema_limits": True,
                            "manual_questions": False,
                        },
                    },
                    separators=(",", ":"),
                )
                try:
                    generation_skills = skills_for("test_generation", state)
                    result = await self.runtime.run_stage(
                        stage="test_generation",
                        skill_name=generation_skills.primary,
                        companions=generation_skills.companions,
                        persona=persona_instructions("test_generation"),
                        prompt=prompt,
                        state=state,
                        persist=self.save,
                        response_format=GeneratedFilesDraft,
                        temperature=retry_temperature,
                    )
                    if result.get("status") != "completed":
                        raise ValueError(f"Unexpected test-generation model status: {result.get('status')}")

                    attempt_dir = self.run_dir / "generation_attempts"
                    attempt_dir.mkdir(exist_ok=True)
                    attempt_path = attempt_dir / f"batch-{batch_index:02d}-attempt-{uuid4().hex}.json"
                    atomic_json(attempt_path, {"batch": batch_index, "attempt": attempt, "result": result})
                    self.audit(
                        "generation_attempt_saved",
                        batch=batch_index,
                        attempt=attempt,
                        artifact=str(attempt_path.relative_to(self.run_dir)),
                    )
                    candidate = expand_generated_lines(
                        GeneratedFilesDraft.model_validate(result["artifact"]).model_dump()
                    )
                    candidate = _canonicalize_case_ids(candidate, work_ids)
                    for item in candidate["files"]:
                        if item["path"].endswith(".py") and (flat := flatten_nested_with(item["content"])):
                            item["content"] = (
                                flat  # deep `with patch(...)` chains cannot compile; see skill_contracts
                            )
                            self.audit("nested_with_flattened", batch=batch_index, path=item["path"])
                    candidate = ensure_generated_case_markers(candidate)
                    if AUTOFIX:
                        candidate, fixes = autofix_files(self._repo_context(), candidate)
                        tooling = state.setdefault(
                            "tooling", {"autofix": 0, "build_checks": 0, "build_failures": 0}
                        )
                        tooling["autofix"] += len(fixes)
                        for fix in fixes:
                            self.audit("autofix", batch=batch_index, attempt=attempt, **fix)
                    raw_batches.append(json.dumps(candidate, separators=(",", ":"), default=str))
                    validation = validate_generated_files(candidate, work_ids)
                    self._check_imports(candidate, state)
                    self._check_paths_are_new(candidate)
                    if validation["uncovered_case_ids"]:
                        raise ValueError(
                            "Generated batch does not implement all approved case IDs: "
                            + ", ".join(validation["uncovered_case_ids"])
                        )
                    collisions = (accepted_paths | {f["path"] for f in kept_files}) & {
                        item["path"] for item in candidate["files"]
                    }
                    if collisions:
                        raise ValueError(
                            "Generated batch reuses paths from an earlier batch: "
                            + ", ".join(sorted(collisions))
                        )
                    await self._build_gate(candidate, batch_index)
                    accepted = {**candidate, "files": kept_files + candidate["files"]}
                    cache["batches"][str(batch_index)] = accepted
                    self.save()
                    break
                except Exception as exc:
                    repair_feedback = (
                        f"Attempt {attempt} failed generation or static validation: {exc}. "
                        + (f"HOW TO FIX: {remedy_for(str(exc))} " if remedy_for(str(exc)) else "")
                        + "Regenerate ONLY this batch. "
                        "Return one complete schema-valid JSON object. Generate one file per supplied case ID, exactly one "
                        "test declaration per file, and one matching case_ids entry. The host supplies CASE_ID comments. "
                        "Keep imports minimal and source complete. Python files must define a top-level def test_name(...): "
                        "with a meaningful assertion against the actual application. Imports alone are not a test."
                    )
                    self.audit(
                        "test_generation_repair",
                        batch=batch_index,
                        attempt=attempt,
                        error=str(exc),
                    )
                    if attempt == TEST_GENERATION_MAX_ATTEMPTS:
                        rounds[str(batch_index)] = rounds.get(str(batch_index), 0) + 1
                        self.save()
                        raise ValueError(
                            f"Test generation batch {batch_index}/{total_batches} failed after "
                            f"{TEST_GENERATION_MAX_ATTEMPTS} attempts: {exc}"
                        ) from exc

            if accepted is None:
                raise ValueError(
                    f"Test generation batch {batch_index}/{total_batches} produced no accepted output."
                )
            accepted_files.extend(accepted["files"])
            accepted_paths.update(item["path"] for item in accepted["files"])
            covered.update(batch_ids)
            if accepted["selected_language"] not in languages:
                languages.append(accepted["selected_language"])
            if accepted["selected_framework"] not in frameworks:
                frameworks.append(accepted["selected_framework"])
            if accepted["rationale"] not in rationales:
                rationales.append(accepted["rationale"])

        all_ids = {str(case["id"]) for case in cases}
        final_payload = {
            "selected_language": ", ".join(languages)[:80] or "Repository native stack",
            "selected_framework": ", ".join(frameworks)[:120] or "Repository native test framework",
            "rationale": (
                f"Generated {len(all_ids)} approved design cases in {total_batches} statically validated batches. "
                + (" ".join(rationales))
            )[:600],
            "case_catalog": [
                {
                    "id": str(case.get("id", "")),
                    "title": str(case.get("title", "")),
                    "type": str(case.get("type", "")),
                    "priority": str(case.get("priority", "")),
                    "expected_result": str(case.get("expected_result", "")),
                }
                for case in cases
            ],
            "files": accepted_files,
        }
        validation = validate_generated_files(final_payload, all_ids)
        if validation["uncovered_case_ids"]:
            raise ValueError(
                "Generated files do not implement all approved case IDs: "
                + ", ".join(validation["uncovered_case_ids"])
            )
        state.pop("heal", None)  # a heal round is finished once every failing case has been regenerated
        state.setdefault("generation_batches", {})["test_generation"] = {
            "batch_size": TEST_GENERATION_BATCH_SIZE,
            "batch_count": total_batches,
            "approved_case_count": len(all_ids),
        }
        state.setdefault("raw_model_output_batches", {})["test_generation"] = raw_batches
        self.save()
        return {
            **final_payload,
            "validation": validation,
            "execution_status": "pending_host_execution",
            "completion_note": "Static traceability validation complete. Each approved case ID is mapped to exactly one generated test file; execution is a separate host step.",
        }

    def _require_coverage_or_reason(self, design: dict, state: dict) -> None:
        """Design policy: unit cases exist, every actor has cases, every requirement is covered (or explained).

        Each gap must be explained by citing the requirement ID / actor name in `gaps`. Violations are sent back to the
        model during the repair attempts; on the last attempt the design is accepted with the problem recorded in
        gaps_report.notes so a human decides at the review gate instead of the run being blocked.
        """
        arts = state["artifacts"]
        reqs = {r["id"]: r["text"] for r in arts["requirements"]["requirement_model"]["requirements"]}
        cited = " ".join(str(g) for g in state.get("draft", {}).get("gaps", [])).lower()
        cases = design_cases(design)
        problems: list[str] = []
        if arts["repo_analysis"].get("source_file_count") and not any(
            c.get("level") == "unit" for c in cases
        ):
            problems.append(
                "The design has no unit-level test case. Add unit cases for the core functions/classes, "
                "or explain in `gaps` (mention 'unit') why none is possible."
            )
        idle = [
            a["name"]
            for a in arts["requirements"].get("actors", [])
            if a["name"].lower() != "system"
            and not any(c.get("actor") == a["name"] for c in cases)
            and a["name"].lower() not in cited
        ]
        if idle:
            problems.append(
                f"Actor(s) without any test case: {', '.join(idle)}. Design cases per actor journey, or "
                "mention the actor in `gaps` with the reason."
            )
        missing = [rid for rid in design["gaps_report"]["uncovered_requirements"] if rid.lower() not in cited]
        if missing:
            listing = "; ".join(f"{rid}: {reqs[rid][:110]}" for rid in missing[:12])
            problems.append(
                f"{len(missing)} requirement(s) have no test case. Add cases tracing to them, or cite each "
                f"requirement ID in `gaps` with the reason. Uncovered: {listing}"
            )
        if not problems:
            return
        if state.get("design_attempt", 1) >= MODEL_REPAIR_ATTEMPTS:
            design["gaps_report"]["notes"] += ("; " if design["gaps_report"]["notes"] else "") + " | ".join(
                problems
            )
            return
        raise ValueError(" ".join(problems))

    def _repo_context(self) -> RepoContext:
        """Live repository facts for the central tools (routes are extracted once and cached in the run state)."""
        state = self.state
        analysis = state.get("artifacts", {}).get("repo_analysis", {})
        files = analysis.get("source_files", [])
        if "routes" not in state:
            state["routes"] = extract_routes(self.run_dir / "repo", files)
        return RepoContext(
            root=self.run_dir / "repo",
            source_files=files,
            test_files=analysis.get("test_files", []),
            package_files=analysis.get("package_files", []),
            workspace=self.workspace if self.workspace.exists() else None,
            routes=state["routes"],
            app_url=state.get("app_url"),
        )

    def _build_files(self, files: list[dict]) -> dict:
        """Compile/collect candidate files in the prepared workspace (only once the environment install is done)."""
        if not BUILD_GATE:
            return {"status": "skipped", "reason": "disabled by HARNESS_BUILD_GATE=off", "errors": []}
        if self.state.get("environment", {}).get("status") != "ready":
            return {"status": "skipped", "reason": "isolated environment not installed", "errors": []}
        return try_build(
            detect_stack(self.workspace),
            self.workspace,
            files,
            cache_dir=self.cache_dir,
            out_root=self.run_dir / "build_checks" / uuid4().hex[:10],
            provisioned=Provisioned.from_dicts(self.state.get("toolchains")),
        )

    async def _build_gate(self, candidate: dict, batch: int) -> None:
        report = await asyncio.to_thread(self._build_files, candidate["files"])
        tooling = self.state.setdefault("tooling", {"autofix": 0, "build_checks": 0, "build_failures": 0})
        if report["status"] != "skipped":
            tooling["build_checks"] += 1
        self.audit(
            "build_gate",
            batch=batch,
            status=report["status"],
            tool=report.get("tool"),
            seconds=report.get("seconds"),
            reason=report.get("reason"),
            errors=len(report.get("errors", [])),
        )
        if report["status"] == "failed":
            tooling["build_failures"] += 1
            detail = "; ".join(f"{e['file']}:{e['line']} {e['message']}" for e in report["errors"][:6])
            raise ValueError(f"Build check ({report['tool']}) failed: {detail}"[:1800])

    def _read_repo_file(self, rel: str) -> str | None:
        target = self.run_dir / "repo" / rel
        return target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None

    def _check_paths_are_new(self, candidate: dict) -> None:
        """Generated tests must be new files: existing repository files (including existing tests) are never replaced."""
        clash = [i["path"] for i in candidate["files"] if (self.run_dir / "repo" / i["path"]).exists()]
        if clash:
            raise ValueError(
                f"Generated path(s) already exist in the repository: {', '.join(clash)}. Existing files are kept; "
                "choose a NEW file name (for example add the case topic or batch number) in a new path."
            )

    def _check_imports(self, candidate: dict, state: dict) -> None:
        """Static gates (syntax, imports, undefined names, mocks, patch targets, URLs) on every generated file."""
        ctx = self._repo_context()
        for item in candidate["files"]:
            static_gate(ctx, item["path"], item["content"])

    async def validate(self, stage, state):

        draft = state["draft"]
        if stage == "requirements":
            validated = RequirementsDraft.model_validate(draft).model_dump()
            evidence_paths = set(re.findall(r"--- FILE: (.*?) ---", state.get("evidence", "")))
            for requirement in validated["requirements"]:
                unknown = set(requirement["source_files"]) - evidence_paths
                if unknown:
                    raise ValueError(f"Requirement cites files not supplied as evidence: {sorted(unknown)}")
            requirements = finalize_discovered_requirements(validated, state["repo_url"])
            if self.stages != CORE_STAGES:  # deterministic preview of the isolated-environment install plan
                stack = await asyncio.to_thread(detect_stack, self.run_dir / "repo")
                requirements["environment_plan"] = await asyncio.to_thread(
                    build_environment_plan,
                    stack,
                    self.run_dir / "repo",
                    self.cache_dir,
                    EXECUTION_TIMEOUT_SECONDS,
                    requirements.get("environment_needs"),
                )
            return requirements
        if stage == "test_planning":
            plan = TestPlan.model_validate(draft)
            if not plan.areas or not plan.execution_order:
                raise ValueError("Test plan must contain at least one area and execution step.")
            return plan.model_dump()
        if stage == "test_design":
            design = finalize_design(
                draft,
                state["artifacts"]["requirements"],
                state["repo_url"],
                self.skills_root,
                allowed_levels=_allowed_levels(state),
            )
            self._require_coverage_or_reason(design, state)
            return design
        if stage == "test_generation":
            generated = expand_generated_lines(GeneratedFilesDraft.model_validate(draft).model_dump())
            design = state["artifacts"]["test_design"]
            ids = {
                case["id"]
                for key, cases in design.items()
                if key.endswith("_tests") and isinstance(cases, list)
                for case in cases
            }
            validation = validate_generated_files(generated, ids)
            if validation["uncovered_case_ids"]:
                raise ValueError(
                    "Generated files do not implement all approved case IDs: "
                    + ", ".join(validation["uncovered_case_ids"])
                )
            return {
                **generated,
                "validation": validation,
                "execution_status": "pending_host_execution",
                "completion_note": "Generation and static traceability validation complete. Each descriptive test ID is mapped to exactly one generated test file; tests were not executed.",
            }
        raise ValueError(f"Unsupported validation stage: {stage}")

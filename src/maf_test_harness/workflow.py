"""Checkpointed autonomous MAF workflow with one human review gate per stage."""

# NOTE: no `from __future__ import annotations` here; MAF inspects handler annotations at runtime.
import asyncio
import json
import re
import traceback
from pathlib import Path
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

from .agent import prompt_settings
from .agent.personas import PERSONAS
from .envs import app_launcher
from .envs import recovery as env_recovery
from .envs.platform_compat import is_cancelled
from .hooks import (
    SESSION_START,
    STAGE_GATE,
    STOP,
    HookEngine,
    build_engine,
)
from .monitoring.telemetry import configure_telemetry
from .monitoring.trace import TraceRecorder
from .qa.packaging import build_deliverable
from .repo.analyzer import (
    analyze_repository,
    build_repository_evidence,
    clone_repository,
    declared_dependencies,
    validate_github_url,
)
from .repo.specs import ingest_specs
from .repo.stack import detect_stack
from .repo.testability import analyze_testability
from .settings import (
    AGENT_TOOLS,
    HOOKS,
    SKILL_EVALUATORS,
    STRICT_ENV,
)
from .skills.config import skills_for
from .skills.host import execute_host_skill
from .storage import read_json
from .tools.agent_tools import HarnessToolbox
from .tools.registry import ToolContext
from .tools.typed import default_registry
from .workflow_steps.execution import ExecutionSteps
from .workflow_steps.generation import GenerationSteps
from .workflow_steps.greenfield import SCAFFOLDS, GreenfieldSteps, has_no_code
from .workflow_steps.planning import PlanningSteps
from .workflow_steps.rerun import RerunSteps

# Levels that need a running application; without one the design stage may only use in-process levels.
from .workflow_support import (
    CORE_STAGES,
    ENV_STAGE,
    EXECUTION_STAGE,
    STAGES,
    atomic_json,
    digest,
    friendly_error,
)
from .workflow_support import (
    LIVE_APP_LEVELS as LIVE_APP_LEVELS,
)

# Compatibility exports for existing artifact helpers.
from .workflow_support import (
    _allowed_levels as _allowed_levels,
)
from .workflow_support import (
    _canonicalize_case_ids as _canonicalize_case_ids,
)
from .workflow_support import (
    _compact_design as _compact_design,
)
from .workflow_support import (
    _compact_repo as _compact_repo,
)
from .workflow_support import (
    _compact_requirements as _compact_requirements,
)
from .workflow_support import (
    _generation_policy as _generation_policy,
)
from .workflow_support import (
    _plan_summary as _plan_summary,
)
from .workflow_support import (
    _requirements_for as _requirements_for,
)
from .workflow_support import (
    _undeclared_libraries as _undeclared_libraries,
)


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


class Harness(RerunSteps, ExecutionSteps, GenerationSteps, GreenfieldSteps, PlanningSteps):
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
        requirements_mode: str = "deterministic",
    ):
        if requirements_mode not in {"deterministic", "model"}:
            raise ValueError("Unknown requirements mode.")
        self.requirements_mode = requirements_mode
        self.stages = list(STAGES if execute else CORE_STAGES)
        self.cache_dir = (cache_dir or Path(run_dir).resolve().parent / ".harness_cache").resolve()
        self.run_dir = run_dir.resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.skills_root = skills_root.resolve()
        self.runtime = runtime
        self._toolbox = HarnessToolbox(self._repo_context, self._build_files)
        if AGENT_TOOLS and runtime is not None and getattr(runtime, "tool_provider", None) is None:
            runtime.tool_provider = self._stage_tools
        if runtime is not None and getattr(runtime, "recorder", None) is None:
            runtime.recorder = TraceRecorder(self.run_dir / "trace.jsonl")
        self.view_path = self.run_dir / "run.json"
        self.view = (
            read_json(self.view_path)
            if self.view_path.exists()
            else {
                "status": "new",
                "pending": None,
                "consumed": [],
                "checkpoint_id": None,
            }
        )
        self.state = self.view.get("state", {})
        # Resume saved stage order so old checkpoint edges and approvals retain their meaning.
        saved_stages = self.state.get("stages")
        if saved_stages and set(saved_stages).issubset(set(STAGES)):
            self.stages = list(saved_stages)
        # Hooks: built-ins + .harness/hooks.json (see docs/HOOKS.md). A malformed config stops the run before it starts.
        self.hooks = hooks or build_engine(
            context={"writable_roots": [str(self.workspace)], "allow_downloads": False},
            audit=self.audit,
            setting=HOOKS,
        )
        if runtime is not None and getattr(runtime, "hooks", None) is None:
            runtime.hooks = self.hooks
        self.typed = default_registry(
            ToolContext(
                repo_root=self.run_dir / "repo",
                workspace=self.workspace,
                writable_roots=[self.workspace],
                skills_root=self.skills_root,
            ),
            self.hooks,
            self.audit,
        )
        self.hooks.notify(SESSION_START, run=self.run_dir.name, resumed=bool(self.state))
        self._build_workflow()

    def _build_workflow(self):
        attempt = self.view.get("rerun_attempt")
        self.storage = FileCheckpointStorage(
            self.run_dir / "checkpoints" / attempt if attempt else self.run_dir / "checkpoints"
        )
        start = self.view.get("restart_stage") or self.stages[0]
        first = self.stages.index(start)
        if EXECUTION_STAGE in self.stages:
            first = min(first, self.stages.index("test_generation"))
        graph_stages = self.stages[first:]
        executors = [StageExecutor(stage, self) for stage in graph_stages]
        builder = WorkflowBuilder(
            start_executor=executors[graph_stages.index(start)],
            name="maf-test-harness",
            checkpoint_storage=self.storage,
        )
        for current, following in zip(executors, executors[1:], strict=False):
            builder.add_edge(current, following)
        if EXECUTION_STAGE in self.stages:  # human-approved heal loop: failing tests go back to generation
            builder.add_edge(executors[-1], executors[graph_stages.index("test_generation")])
        self.workflow = builder.build()
        self._restored = False

    def _stage_tools(self, stage: str) -> list:
        """Tools an agent gets for a stage: the central tools, plus (opt-in) the skill validators of its own skills."""
        tools = self._toolbox.tools_for(stage)
        if SKILL_EVALUATORS and self.typed.names(stage):
            self.typed.context.allowed_skills = tuple(skills_for(stage, self.state).all)
            tools = [*tools, *self.typed.as_function_tools(stage)]
        return tools

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
            if self.view["status"] not in {"stopped", "stopping"}:
                self.view["status"] = "complete"
                self.hooks.notify(STOP, reason="complete")
        self.save()
        return self.view

    async def start(
        self,
        repo_url: str,
        app_url: str | None = None,
        start_app: bool = False,
        app_component: str | None = None,
        repo_knowledge: str = "",
        case_budget: int | None = None,
        run_name: str | None = None,
        run_mode: str = "brownfield",
        test_types: list[str] | None = None,
        spec_sources: dict | None = None,
        spec_intake: dict | None = None,
        documents: list | None = None,
        jira: dict | None = None,
        jira_token: str | None = None,
        target_language: str = "python",
    ):
        """``run_mode="greenfield"`` takes specs from ``spec_intake`` (an ``ingest_specs`` result) or from ``documents``
        (``(name, bytes)`` pairs or ``{name, content}`` dicts) and Jira (``jira`` or ``spec_sources["jira"]`` plus
        ``jira_token``). They are ingested here; the token is held in memory for that call only and never stored. The
        repository URL is then optional (no repository: scaffold, no execution)."""
        if self.view["status"] != "new":
            raise ValueError("Run already started. Resume its pending request.")
        if run_mode not in {"brownfield", "greenfield"}:
            raise ValueError("run_mode must be brownfield or greenfield.")
        greenfield = run_mode == "greenfield"
        repo_url = (repo_url or "").strip()
        if greenfield:
            if target_language not in SCAFFOLDS:
                raise ValueError(f"target_language must be one of {sorted(SCAFFOLDS)}.")
            docs = [
                {"name": d[0], "content": d[1]} if isinstance(d, tuple | list) else d for d in documents or []
            ]
            if docs:  # keep the originals with the run
                inputs = self.run_dir / "spec_inputs"
                inputs.mkdir(parents=True, exist_ok=True)
                for doc in docs:
                    (inputs / Path(doc["name"]).name).write_bytes(doc["content"])
            jira = jira or ((spec_sources or {}).get("jira") or None)
            if jira and jira_token:
                jira = {**jira, "api_token": jira_token}
            if spec_intake is None:
                extra = {"jira_via_mcp": m} if (m := (spec_sources or {}).get("jira_mcp")) else {}
                spec_intake = await asyncio.to_thread(
                    lambda: ingest_specs(docs, jira, **extra)
                )  # docs/MCP.md
            if not spec_intake.get("sections"):
                raise ValueError(
                    "Greenfield mode needs at least one document section or Jira story. "
                    + "; ".join(spec_intake.get("warnings") or [])
                )
        if not isinstance(repo_knowledge, str) or len(repo_knowledge) > 32000:
            raise ValueError("Repository knowledge must be text of at most 32,000 characters.")
        ref = None
        if repo_url or not greenfield:
            canonical, _, _, ref = validate_github_url(repo_url, with_ref=True)
            if repo_url.startswith("git clone "):
                repo_url = canonical
        if app_url is not None and not re.fullmatch(
            r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/\S*)?", app_url.strip()
        ):
            raise ValueError(
                "app_url must be a local http(s) URL such as http://localhost:8080 (tests run on this host)."
            )
        if not (self.run_dir / "prompts.json").exists():
            prompt_settings.save(self.run_dir / "prompts.json", prompt_settings.defaults())
        self.state = {
            "repo_url": repo_url,
            "test_types": list(test_types or ("unit", "integration")),
            **({"spec_sources": spec_sources} if greenfield and spec_sources else {}),
            **({"run_name": run_name} if run_name else {}),
            "run_mode": run_mode,
            **(
                {
                    "target_language": target_language,
                    "spec_intake": {k: spec_intake[k] for k in ("sources", "sections", "warnings")},
                }
                if greenfield
                else {}
            ),
            "repo_knowledge": repo_knowledge,
            "workflow_version": 2,
            "requirements_mode": self.requirements_mode,
            "repo_ref": ref,
            "app_url": app_url.strip() if app_url else None,
            "launch_app": bool(start_app and not app_url),
            "app_component": app_component,
            **({"case_budget": {"per_requirement": case_budget}} if case_budget else {}),
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
            fresh = read_json(self.view_path)
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
            self.state["active_stage"] = pending["stage"]
            self.view["status"] = "running"
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
                if is_cancelled():  # a Stop, not a failure: the stop handler records "stopped"
                    raise
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
        if stage == "repo_analysis" and has_no_code(state):
            await self.scaffold_repo_analysis(state, ctx)
            return
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
        if STRICT_ENV and stage != ENV_STAGE and state.get("environment", {}).get("status") == "incomplete":
            raise ValueError(
                "Strict environment guardrail: the environment is incomplete (a declared requirement is not installed or "
                "conflicts), so this stage cannot start. Retry environment setup."
            )
        if state.get("workflow_version") == 2 and stage in CORE_STAGES:
            self.typed.context.allow_downloads = False
        state["active_stage"] = stage
        self.state = state
        self.save()
        if stage in (ENV_STAGE, EXECUTION_STAGE) and has_no_code(state):
            await self.skip_without_code(stage, state, ctx)
            return
        if stage == ENV_STAGE:
            if (
                state.get("workflow_version") == 2
                and state.get("environment", {}).get("status") == "ready"
                and state.get("approved", {}).get(ENV_STAGE)
            ):
                await ctx.send_message(state)
                return
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
                if hasattr(self.runtime, "close_stage"):
                    await self.runtime.close_stage(stage)  # disconnect that stage's MCP servers
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
                if stage == ENV_STAGE and (version := state.pop("env_python_proposal", None)):
                    state["env_python"] = version
                    state.setdefault("env_python_tried", []).append(version)
                    await asyncio.to_thread(
                        env_recovery.wipe_venv, self.workspace
                    )  # rebuilt with the other interpreter
                    self.audit("environment_python_fallback", version=version)
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
            self.state = state
            state["active_stage"] = stage
            self.save()
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
            analyzed = artifact.model_dump()
            analyzed["reverse_engineering"] = await asyncio.to_thread(
                execute_host_skill,
                self.run_dir,
                self.skills_root,
                stage,
                "reverse-engineering",
                {"root": str(self.run_dir / "repo"), "source_files": artifact.source_files},
                state,
                self.audit,
            )
            atomic_json(self.run_dir / "reverse_engineering.json", analyzed["reverse_engineering"])
            analyzed["testability"] = await asyncio.to_thread(
                analyze_testability, self.run_dir / "repo", artifact.source_files
            )
            self.audit(
                "deterministic_reverse_engineering",
                stage=stage,
                model_calls=0,
                coverage=analyzed["reverse_engineering"]["coverage"],
            )
            await self.review(stage, analyzed, state, ctx)
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

    @property
    def workspace(self) -> Path:
        return self.run_dir / "workspace"

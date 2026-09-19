"""Checkpointed autonomous MAF workflow with one human review gate per stage."""
# NOTE: no `from __future__ import annotations` here; MAF inspects handler annotations at runtime.
import asyncio
import fcntl
import hashlib
import json
import os
import re
import traceback
from pathlib import Path
from threading import Event
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

from .execution import export_tests
from .models import GeneratedFilesDraft, RequirementsDraft, TestDesignDraft, TestPlan
from .personas import PERSONAS, persona_instructions
from .qa_report import build_qa_report, design_cases, render_markdown
from .repo_analyzer import (
    analyze_repository,
    build_repository_evidence,
    clone_repository,
    validate_github_url,
)
from .runners import RunPlan, Step, build_plan, execute_plan
from .settings import (
    EXECUTION_TIMEOUT_SECONDS,
    GENERATION_BATCH_SIZE,
    GENERATION_MAX_ATTEMPTS,
    INPUT_TOKEN_LIMIT,
    PROVIDER_CALL_LIMIT,
    RUN_TOKEN_LIMIT,
)
from .skill_contracts import (
    SKILL_NAMES,
    check_java_imports,
    check_python_imports,
    ensure_generated_case_markers,
    expand_generated_lines,
    finalize_design,
    finalize_discovered_requirements,
    validate_generated_files,
)
from .stack import detect_stack

MODEL_STAGES = ["repo_analysis", "requirements", "test_planning", "test_design", "test_generation"]
EXECUTION_STAGE = "test_execution"
STAGES = MODEL_STAGES + [EXECUTION_STAGE]
DEFAULT_RUN_TOKEN_LIMIT = RUN_TOKEN_LIMIT
DEFAULT_INPUT_TOKEN_LIMIT = INPUT_TOKEN_LIMIT
TEST_GENERATION_BATCH_SIZE = GENERATION_BATCH_SIZE
TEST_GENERATION_MAX_ATTEMPTS = GENERATION_MAX_ATTEMPTS
MAX_HEAL_ROUNDS = 3
MODEL_REPAIR_ATTEMPTS = 3  # host-validation repair loop for requirements/planning/design
# Levels that need a running application; without one the design stage may only use in-process levels.
LIVE_APP_LEVELS = {"ui", "e2e", "accessibility"}


def required_inputs(stage: str, values: dict | None = None) -> dict:
    """Compatibility shim: autonomous stages no longer require manual form inputs."""
    if stage not in STAGES:
        raise ValueError(f"Unknown workflow stage: {stage}")
    return dict(values or {})


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def atomic_json(path: Path, data: dict):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def _allowed_levels(state: dict[str, Any]) -> set[str]:
    base = {"unit", "integration", "api", "cli", "security", "performance"}
    return base | (LIVE_APP_LEVELS if state.get("app_url") else set())


def approved_case_ids(design: dict[str, Any]) -> set[str]:
    return {case["id"] for case in design_cases(design)}


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
            cases.append({
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
            })
    return {"cases": cases, "gaps_report": design.get("gaps_report", {})}


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
        "stack": {k: stack.get(k) for k in ("primary", "test_runner", "web_ui", "http_service") if k in stack},
        "live_app": {"available": bool(state.get("app_url")), "base_url_env_var": "BASE_URL",
                     "rule": "UI/E2E tests must read the target from the BASE_URL environment variable; never hard-code a host."},
        "level_rules": {
            "unit": "Isolated, no network; mock boundaries but never the behavior under test.",
            "integration": "Real collaborators in-process; temp dirs/in-memory DBs only.",
            "api": "Use the framework's in-process test client when one exists, else BASE_URL.",
            "cli": "Invoke the real entry point via subprocess or the framework runner; assert exit code and output.",
            "ui/e2e/accessibility": "Only with live_app.available; existing browser framework first, else Playwright.",
            "security": "Assert rejection/sanitization paths that the source shows.",
        },
        "framework_selection_rule": "Prefer detected test framework; otherwise choose a mainstream native framework compatible with the dominant stack.",
        "execution_policy": "Tests are executed by the host after human approval; write them to run unattended and deterministically.",
    }


class StageExecutor(Executor):
    def __init__(self, stage: str, host):
        super().__init__(id=stage)
        self.stage, self.host = stage, host

    @handler
    async def start(self, state: dict, ctx: WorkflowContext[dict, dict]):
        try:
            await self.host.enter(self.stage, state, ctx)
        except Exception as exc:
            self.host.audit("stage_error", stage=self.stage, error=str(exc), trace=traceback.format_exc()[-1800:])
            await self.host.ask(self.stage, "error", state, ctx,
                                title="Stage failed; approved artifacts retained", error=str(exc))

    @response_handler
    async def answer(self, original_request: dict, response: dict, ctx: WorkflowContext[dict, dict]):
        await self.host.answer(original_request.get("stage", self.stage), original_request, response, ctx)


class Harness:
    """Checkpointed autonomous MAF workflow with one human review gate per stage."""

    @staticmethod
    def run_dir_parent(run_dir: Path) -> Path:
        return Path(run_dir).resolve().parent

    def __init__(self, run_dir: Path, *, skills_root: Path, runtime=None, execute: bool = True,
                 cache_dir: Path | None = None):
        self.stages = list(STAGES if execute else MODEL_STAGES)
        self.cache_dir = (cache_dir or self.run_dir_parent(run_dir) / ".harness_cache").resolve()
        self.run_dir = run_dir.resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.skills_root = skills_root.resolve()
        self.runtime = runtime
        self.cancel_event = Event()
        self.view_path = self.run_dir / "run.json"
        self.view = json.loads(self.view_path.read_text()) if self.view_path.exists() else {
            "version": 2,
            "status": "new",
            "pending": None,
            "consumed": [],
            "checkpoint_id": None,
        }
        if self.view.get("version") not in {1, 2}:
            raise ValueError("Unsupported saved workflow version. Start a new run.")
        if self.view.get("version") == 1 and self.view.get("status") != "new":
            raise ValueError("This saved run uses the pre-autonomous workflow. Start a new run with the updated harness.")
        self.state = self.view.get("state", {})
        migrated = self._upgrade_saved_state()
        if migrated:
            self.save()
        self.storage = FileCheckpointStorage(self.run_dir / "checkpoints")
        executors = [StageExecutor(stage, self) for stage in self.stages]
        builder = WorkflowBuilder(
            start_executor=executors[0], name="maf-test-harness", checkpoint_storage=self.storage
        )
        for current, following in zip(executors, executors[1:]):
            builder.add_edge(current, following)
        if EXECUTION_STAGE in self.stages:  # human-approved heal loop: failing tests go back to generation
            builder.add_edge(executors[-1], executors[self.stages.index("test_generation")])
        self.workflow = builder.build()
        self._restored = False

    def _upgrade_saved_state(self) -> bool:
        changed = False
        if self.view.get("version") == 1 and self.view.get("status") == "new":
            self.view["version"] = 2
            changed = True
        holders = [self.view.get("state")]
        pending = self.view.get("pending")
        if isinstance(pending, dict):
            holders.append(pending.get("state"))
        for holder in holders:
            if not isinstance(holder, dict):
                continue
            budget = holder.get("budget")
            if not isinstance(budget, dict):
                continue
            expected = {
                "limit": DEFAULT_RUN_TOKEN_LIMIT,
                "input_limit": DEFAULT_INPUT_TOKEN_LIMIT,
                "call_limit": PROVIDER_CALL_LIMIT,
            }
            for key, value in expected.items():
                if int(budget.get(key, value)) != value:
                    budget[key] = value
                    changed = True
        return changed

    def save(self):
        self.view["state"] = self.state
        atomic_json(self.view_path, self.view)

    def audit(self, event: str, **details):
        with (self.run_dir / "audit.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "event": event,
                "at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                **details,
            }, default=str) + "\n")

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
        self.save()
        return self.view

    async def start(self, repo_url: str, app_url: str | None = None):
        if self.view["status"] != "new":
            raise ValueError("Run already started. Resume its pending request.")
        _, _, _, ref = validate_github_url(repo_url, with_ref=True)
        if app_url is not None and not re.fullmatch(r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/\S*)?", app_url.strip()):
            raise ValueError("app_url must be a local http(s) URL such as http://localhost:8080 (tests run on this host).")
        self.view["version"] = 2
        self.state = {
            "repo_url": repo_url.strip(),
            "repo_ref": ref,
            "app_url": app_url.strip() if app_url else None,
            "stages": self.stages,
            "artifacts": {},
            "revisions": {},
            "sessions": {},
            "model_requests": {},
            "feedback": {},
            "approved": {},
            "budget": {
                "limit": DEFAULT_RUN_TOKEN_LIMIT,
                "charged": 0,
                "calls": 0,
                "reported_tokens": 0,
                "input_limit": DEFAULT_INPUT_TOKEN_LIMIT,
                "call_limit": PROVIDER_CALL_LIMIT,
            },
        }
        result = await self.workflow.run(self.state)
        self._restored = True
        return await self._collect(result)

    async def respond(self, request_id: str, response: dict):
        with (self.run_dir / "run.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            fresh = json.loads(self.view_path.read_text())
            pending = fresh.get("pending")
            if not pending or pending["id"] != request_id or request_id in fresh.get("consumed", []):
                raise ValueError("Stale or already consumed approval request.")
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
            "requested", request_id=request_id, stage=stage, kind=kind,
            details=details, request_hash=request["hash"]
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
                if decision == "revise":
                    feedback = str(response.get("feedback", "")).strip()
                    if not feedback:
                        raise ValueError("Revision instructions are required.")
                    state.setdefault("feedback", {})[stage] = feedback
                    if stage == "test_generation":
                        state.pop("accepted_generation_batches", None)
                        state.pop("heal", None)
                    for downstream in self.stages[self.stages.index(stage) + 1:]:
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
                    await ctx.yield_output(state)
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
                state["completed_reason"] = "Test execution was skipped by the reviewer; generated tests were not run."
                self.audit("execution_skipped", request_id=request["id"])
                await ctx.yield_output(state)
                return
            if decision == "skip":
                raise ValueError("Only test execution can be skipped.")
            if stage == EXECUTION_STAGE and decision == "approve":
                await self.run_execution(state, request, ctx)
                return
            if decision == "deny":
                await self.ask(
                    stage, "error", state, ctx,
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
            state["stack"] = (await asyncio.to_thread(
                detect_stack, self.run_dir / "repo", artifact.languages)).to_dict()
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

    def _completion_reason(self, state: dict, stage: str) -> str:
        if stage == EXECUTION_STAGE:
            qa = state["artifacts"][EXECUTION_STAGE]["qa_report"]
            return (f"QA verdict {qa['verdict']} (execution {qa['execution_status']}). "
                    "Report saved as qa_report.json / qa_report.md in the run directory.")
        return ("Test source generated and statically validated against every approved design case. "
                "This run was configured without the execution stage; tests were not run.")

    def _execution_artifact(self, state: dict, report: dict | None) -> dict:
        arts = state["artifacts"]
        skill_checks = {stage: trace.get("skill_loaded_via_maf")
                        for stage, trace in state.get("skill_tool_trace", {}).items()}
        qa = build_qa_report(
            repo=arts["repo_analysis"], requirements=arts["requirements"], design=arts["test_design"],
            generation=arts["test_generation"], execution=report, stack=state.get("stack"),
            provenance={"runtime": state.get("runtime_provenance"), "skills": state.get("skill_provenance"),
                        "skill_loaded_via_maf_tool": skill_checks, "budget": state.get("budget")})
        execution = dict(report or {"status": "not_run"})
        return {"qa_report": qa, "execution": execution, "markdown": render_markdown(qa)}

    async def plan_execution(self, state: dict, ctx) -> None:
        arts = state["artifacts"]
        generated, design = arts["test_generation"], arts["test_design"]
        attempt = state["execution_attempts"] = state.get("execution_attempts", 0) + 1
        workspace = self.run_dir / "workspaces" / f"attempt-{attempt}"
        await asyncio.to_thread(export_tests, self.run_dir / "repo", generated, approved_case_ids(design), workspace)
        stack = await asyncio.to_thread(detect_stack, workspace)
        plan = build_plan(stack, workspace, generated["files"], cache_dir=self.cache_dir,
                          timeout=EXECUTION_TIMEOUT_SECONDS)
        if state.get("app_url"):
            for step in plan.steps:
                if step.kind == "test":
                    step.env["BASE_URL"] = state["app_url"]
        plan_dict = plan.to_dict()
        state["execution_plan"] = {"workspace": str(workspace), "plan": plan_dict, "hash": digest(plan_dict),
                                   "attempt": attempt}
        if plan.blocked_reason:  # nothing to approve: record an honest inconclusive report
            report = await asyncio.to_thread(execute_plan, plan, workspace,
                                             self.run_dir / "executions" / f"attempt-{attempt}")
            await self.review(EXECUTION_STAGE, self._execution_artifact(state, report), state, ctx)
            return
        import shlex
        await self.ask(
            EXECUTION_STAGE, "tool", state, ctx, title="Approve running the generated tests on this machine",
            details={
                "tool": "run_generated_tests", "plan_hash": state["execution_plan"]["hash"],
                "adapter": plan.adapter, "workspace": str(workspace), "app_url": state.get("app_url"),
                "steps": [{"name": st.name, "kind": st.kind, "command": shlex.join(st.argv)} for st in plan.steps],
                "effects": ("Installs dependencies and runs the generated tests as your user; repository code "
                            "executes. Local process only, NOT a security sandbox. Model credentials are not "
                            "passed to these commands."),
            })

    async def run_execution(self, state: dict, request: dict, ctx) -> None:
        info = state["execution_plan"]
        if request["details"].get("plan_hash") != info["hash"]:
            raise ValueError("Execution plan changed after it was shown for approval.")
        plan = RunPlan(adapter=info["plan"]["adapter"], steps=[Step(**st) for st in info["plan"]["steps"]],
                       notes=info["plan"].get("notes", []))
        out_dir = self.run_dir / "executions" / f"attempt-{info['attempt']}"
        self.audit("execution_started", request_id=request["id"], adapter=plan.adapter, plan_hash=info["hash"])
        report = await asyncio.to_thread(execute_plan, plan, Path(info["workspace"]), out_dir)
        self.audit("execution_completed", adapter=plan.adapter, status=report["status"], summary=report.get("summary"))
        await self.review(EXECUTION_STAGE, self._execution_artifact(state, report), state, ctx)

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
        evidence = "\n".join(f"- {c['id']} ({c['title']}): {c['status']} {c['message'][:500]}" for c in failed)
        state.setdefault("feedback", {})["test_generation"] = (
            (feedback + "\n" if feedback else "")
            + "Fix ONLY these failing generated tests from the execution evidence. Never weaken or delete "
              "assertions to make them pass; if the application is wrong, keep the assertion.\n" + evidence)
        failed_ids = {c["id"] for c in failed}
        state["heal"] = {"failed_case_ids": sorted(failed_ids), "round": state["heal_rounds"]}
        batches = state.get("accepted_generation_batches", {}).get("batches", {})
        for key in [k for k, b in batches.items() if failed_ids & {i for f in b["files"] for i in f["case_ids"]}]:
            batches.pop(key)
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
                    "live_app": {"available": bool(state.get("app_url")),
                                 "allowed_test_levels": sorted(_allowed_levels(state))},
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
            result = await self.runtime.run_stage(
                stage=stage,
                skill_name=SKILL_NAMES[stage],
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
                repair = (f"Attempt {attempt} was rejected by host validation: {str(exc)[:900]}. Return a corrected "
                          "complete JSON object that fixes exactly this problem and keeps everything else valid.")
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
        signature = digest({"cases": cases, "repo": state["artifacts"]["repo_analysis"],
                            "requirements": state["artifacts"].get("requirements"),
                            "plan": state["artifacts"].get("test_planning"),
                            "evidence": state.get("evidence"), "batch_size": TEST_GENERATION_BATCH_SIZE,
                            "skill": (self.skills_root / SKILL_NAMES["test_generation"] / "SKILL.md").read_text()})
        cache = state.setdefault("accepted_generation_batches", {})
        if cache.get("signature") != signature:
            cache.clear()
            cache.update(signature=signature, batches={})
        policy = _generation_policy(state["artifacts"]["repo_analysis"], state)
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
            batch = cases[start:start + TEST_GENERATION_BATCH_SIZE]
            batch_ids = {str(case["id"]) for case in batch}
            requirements = state["artifacts"].get("requirements", {})
            linked_ids = {link["id"] for case in batch for link in case.get("traceability_links", [])}
            preferred = [path for req in requirements.get("requirement_model", {}).get("requirements", [])
                         if req.get("id") in linked_ids for path in req.get("source_files", [])]
            evidence = build_repository_evidence(self.run_dir / "repo", max_files=16,
                                                 max_total_chars=40_000, preferred_paths=preferred)
            repair_feedback = ""
            accepted = cache["batches"].get(str(batch_index))
            if accepted is not None:
                cached_validation = validate_generated_files(accepted, batch_ids)
                if cached_validation["uncovered_case_ids"]:
                    accepted = None
            for attempt in range(1, 1 if accepted is not None else TEST_GENERATION_MAX_ATTEMPTS + 1):
                prompt = json.dumps(
                    {
                        "assignment": (
                            "Generate executable test source ONLY for this approved batch. Generate exactly one file per "
                            "supplied case ID and exactly one concrete test declaration in that file. Set case_ids to a one-item "
                            "array containing the corresponding supplied ID. The host adds canonical CASE_ID source markers. "
                            "Use only necessary imports. Never emit placeholders, install dependencies, or execute tests."
                        ),
                        "repository": state["artifacts"]["repo_analysis"],
                        "approved_inputs": {
                            "approved_cases": batch,
                            "requirements": _compact_requirements(requirements),
                            "test_plan": state["artifacts"].get("test_planning", {}),
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
                    result = await self.runtime.run_stage(
                        stage="test_generation",
                        skill_name=SKILL_NAMES["test_generation"],
                        persona=persona_instructions("test_generation"),
                        prompt=prompt,
                        state=state,
                        persist=self.save,
                        response_format=GeneratedFilesDraft,
                    )
                    if result.get("status") != "completed":
                        raise ValueError(f"Unexpected test-generation model status: {result.get('status')}")

                    attempt_dir = self.run_dir / "generation_attempts"
                    attempt_dir.mkdir(exist_ok=True)
                    attempt_path = attempt_dir / f"batch-{batch_index:02d}-attempt-{uuid4().hex}.json"
                    atomic_json(attempt_path, {"batch": batch_index, "attempt": attempt, "result": result})
                    self.audit("generation_attempt_saved", batch=batch_index, attempt=attempt,
                               artifact=str(attempt_path.relative_to(self.run_dir)))
                    candidate = expand_generated_lines(GeneratedFilesDraft.model_validate(result["artifact"]).model_dump())
                    candidate = ensure_generated_case_markers(candidate)
                    raw_batches.append(json.dumps(candidate, separators=(",", ":"), default=str))
                    validation = validate_generated_files(candidate, batch_ids)
                    self._check_imports(candidate, state)
                    if validation["uncovered_case_ids"]:
                        raise ValueError(
                            "Generated batch does not implement all approved case IDs: "
                            + ", ".join(validation["uncovered_case_ids"])
                        )
                    collisions = accepted_paths & {item["path"] for item in candidate["files"]}
                    if collisions:
                        raise ValueError(
                            "Generated batch reuses paths from an earlier batch: " + ", ".join(sorted(collisions))
                        )
                    accepted = candidate
                    cache["batches"][str(batch_index)] = candidate
                    self.save()
                    break
                except Exception as exc:
                    repair_feedback = (
                        f"Attempt {attempt} failed generation or static validation: {exc}. Regenerate ONLY this batch. "
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
                        raise ValueError(
                            f"Test generation batch {batch_index}/{total_batches} failed after "
                            f"{TEST_GENERATION_MAX_ATTEMPTS} attempts: {exc}"
                        ) from exc

            if accepted is None:
                raise ValueError(f"Test generation batch {batch_index}/{total_batches} produced no accepted output.")
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
        """Every requirement needs a test, or a stated reason (its ID cited in `gaps`).

        Enforced during the repair attempts; on the last attempt the design is accepted with the gap visible in
        gaps_report.notes so a human decides, instead of blocking the run.
        """
        reqs = {r["id"]: r["text"] for r in state["artifacts"]["requirements"]["requirement_model"]["requirements"]}
        cited = " ".join(str(g) for g in state.get("draft", {}).get("gaps", []))
        missing = [rid for rid in design["gaps_report"]["uncovered_requirements"] if rid not in cited]
        if not missing:
            return
        if state.get("design_attempt", 1) >= MODEL_REPAIR_ATTEMPTS:
            design["gaps_report"]["notes"] += ("; " if design["gaps_report"]["notes"] else "") + (
                f"{len(missing)} requirement(s) have no test and no stated reason: {', '.join(missing)}")
            return
        listing = "; ".join(f"{rid}: {reqs[rid][:110]}" for rid in missing[:12])
        raise ValueError(f"{len(missing)} requirement(s) have no test case. Add cases that trace to them, or cite each "
                         f"requirement ID in `gaps` with the reason it cannot be tested. Uncovered: {listing}")

    def _check_imports(self, candidate: dict, state: dict) -> None:
        """Static import-resolution gate for Python tests against the cloned repository."""
        analysis = state["artifacts"].get("repo_analysis", {})
        repo_files = list(analysis.get("source_files", []))
        dependency_text = "\n".join(
            (self.run_dir / "repo" / rel).read_text(encoding="utf-8", errors="replace")
            for rel in analysis.get("package_files", []) if (self.run_dir / "repo" / rel).is_file())
        all_files = list(repo_files) + [p for p in analysis.get("test_files", [])]
        for item in candidate["files"]:
            if item["path"].endswith(".py") and repo_files:
                check_python_imports(item["content"], repo_files, dependency_text, item["path"])
            elif item["path"].endswith((".java", ".kt")) and dependency_text:
                check_java_imports(item["content"], all_files, dependency_text, item["path"])

    async def validate(self, stage, state):

        draft = state["draft"]
        if stage == "requirements":
            validated = RequirementsDraft.model_validate(draft).model_dump()
            evidence_paths = set(re.findall(r"--- FILE: (.*?) ---", state.get("evidence", "")))
            for requirement in validated["requirements"]:
                unknown = set(requirement["source_files"]) - evidence_paths
                if unknown:
                    raise ValueError(f"Requirement cites files not supplied as evidence: {sorted(unknown)}")
            return finalize_discovered_requirements(validated, state["repo_url"])
        if stage == "test_planning":
            plan = TestPlan.model_validate(draft)
            if not plan.areas or not plan.execution_order:
                raise ValueError("Test plan must contain at least one area and execution step.")
            return plan.model_dump()
        if stage == "test_design":
            design = finalize_design(
                draft, state["artifacts"]["requirements"], state["repo_url"], self.skills_root,
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

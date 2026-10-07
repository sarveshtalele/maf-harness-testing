"""Execution operations extracted from the checkpointed Harness."""

import asyncio
import shlex
from functools import partial
from pathlib import Path

from ..agent import diagnosis
from ..envs import Provisioned, ToolchainError, app_launcher, env_check, provision_for
from ..envs import recovery as env_recovery
from ..envs.environment import build_environment_plan, steps_from_plan
from ..envs.execution import copy_repo, write_generated
from ..envs.platform_compat import raise_if_cancelled
from ..envs.runners import RunPlan, Step, build_plan, execute_plan, execute_setup
from ..envs.setup_guide import setup_guide
from ..envs.toolchains import apply_toolchains
from ..hooks import (
    ON_ENV_ERROR,
    ON_EXEC_ERROR,
)
from ..qa.report import build_qa_report, render_markdown
from ..qa.step_reports import run_completed_at
from ..qa.triage import remedy_for
from ..repo.stack import detect_stack
from ..settings import (
    DIAGNOSIS_AGENT,
    EXECUTION_TIMEOUT_SECONDS,
    STRICT_ENV,
)
from ..skills.reports import run_skill_checks
from ..workflow_support import (
    ENV_STAGE,
    EXECUTION_STAGE,
    MAX_HEAL_ROUNDS,
    atomic_json,
    digest,
)


class ExecutionSteps:
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
            generated_at=run_completed_at(self.run_dir),
            stack=state.get("stack"),
            environment={
                **(state.get("environment", {}).get("plan") or {}),
                "missing_packages": state.get("environment", {}).get("missing_packages") or [],
            },
            provenance={
                "runtime": state.get("runtime_provenance"),
                "skills": state.get("skill_provenance"),
                "skill_loaded_via_maf_tool": skill_checks,
                "budget": state.get("budget"),
            },
        )
        execution = dict(report or {"status": "not_run"})
        skill_reports = run_skill_checks(
            self.skills_root,
            arts["requirements"],
            arts["test_design"],
            arts["test_generation"],
            generated_at=run_completed_at(self.run_dir),
        )
        qa["skill_checks"] = skill_reports["checks"]
        environment = state.get("environment", {})
        qa["environment"] = {
            "status": environment.get("status", "not_planned"),
            "ecosystem": environment.get("plan", {}).get("ecosystem"),
        }
        atomic_json(self.run_dir / "qa_report.json", qa)
        markdown_temp = self.run_dir / "qa_report.md.tmp"
        markdown_temp.write_text(render_markdown(qa), encoding="utf-8")
        markdown_temp.replace(self.run_dir / "qa_report.md")
        return {
            "qa_report": qa,
            "execution": execution,
            "markdown": render_markdown(qa),
            "skill_reports": {
                "traceability": skill_reports["traceability"],
                "coverage": skill_reports["coverage"],
            },
        }

    async def _ensure_workspace(self) -> Path:
        """One persistent workspace per run: repository copy + isolated environment + generated tests."""
        if not self.workspace.exists():
            await asyncio.to_thread(copy_repo, self.run_dir / "repo", self.workspace)
        return self.workspace

    async def _agent_diagnosis(self, kind: str, state: dict, report: dict, ecosystem: str) -> dict | None:
        """Advisory note from the diagnosis agent for a failure recovery could not fix (opt-in, never blocks a gate)."""
        if not DIAGNOSIS_AGENT or self.runtime is None or not hasattr(self.runtime, "run_tool_agent"):
            return None
        failing = next(
            (
                s
                for s in reversed(report.get("steps", []))
                if s.get("status") not in {"command_succeeded", "passed"}
            ),
            {},
        )
        stdout = failing.get("stdout")
        log = diagnosis.log_tail([str(stdout), str(Path(stdout).with_name("stderr.log"))] if stdout else [])
        self.typed.context.ecosystem = ecosystem
        self.audit("agent_diagnosis_started", kind=kind, step=failing.get("name"))
        note = await diagnosis.diagnose(
            self.runtime,
            self.typed.as_function_tools(diagnosis.AGENT_NAME),
            kind=kind,
            failing={
                k: failing.get(k) for k in ("name", "argv", "status", "exit_code", "diagnoses", "recovery")
            },
            log=log,
            deterministic=list(failing.get("diagnoses") or []),
            state=state,
            persist=self.save,
        )
        self.audit(
            "agent_diagnosis_completed", kind=kind, ok="error" not in note, category=note.get("category")
        )
        return note

    def _environment_artifact(self, state: dict, report: dict) -> dict:
        plan = state["environment"]["plan"]
        return {
            "status": report["status"],
            "setup_guide": plan.get("setup_guide"),
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
            *([state["env_python"]] if state.get("env_python") else []),  # only after a fallback was approved
            **({"browser_tests": True} if state.get("app_url") else {}),
        )
        plan["setup_guide"] = setup_guide(plan, self.workspace, state.get("repo_knowledge", ""))
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
                "setup_guide": plan["setup_guide"],
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
        raise_if_cancelled()
        info = state["environment"]
        if request["details"].get("plan_hash") != info["hash"]:
            raise ValueError("Environment plan changed after it was shown for approval.")
        attempt = state["environment_attempts"] = state.get("environment_attempts", 0) + 1
        self.hooks.context["allow_downloads"] = True  # the approved plan lists every toolchain download
        self.audit("environment_started", request_id=request["id"], plan_hash=info["hash"])
        try:  # the approval covers downloading the toolchains listed in the plan
            provisioned = Provisioned()
            for component in info["plan"].get(
                "components", [{"ecosystem": info["plan"]["ecosystem"], "cwd": "."}]
            ):
                found = await asyncio.to_thread(
                    partial(
                        provision_for,
                        self.workspace / component["cwd"],
                        component["ecosystem"],
                        allow_download=True,
                        python_override=state.get("env_python")
                        if component["ecosystem"] == "python"
                        else None,
                    )
                )
                provisioned.toolchains.extend(found.toolchains)
                provisioned.events.extend(found.events)

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
        if report["status"] == "setup_failed":
            note = await self._agent_diagnosis("setup", state, report, info["plan"]["ecosystem"])
            if note is not None:
                report["agent_diagnosis"] = note
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
            info["ui_tooling"] = await asyncio.to_thread(env_check.ui_tooling, self.workspace)
            info["python_version"] = env_check.venv_python_version(self.workspace)
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
        reasons = self._environment_shortfalls(info, report)
        if STRICT_ENV and reasons:
            await self._block_incomplete_environment(state, report, reasons, ctx)
            return
        state.pop("env_python_proposal", None)
        await self.review(ENV_STAGE, self._environment_artifact(state, report), state, ctx)

    @staticmethod
    def _environment_shortfalls(info: dict, report: dict) -> list[str]:
        """Why the environment is NOT complete: a failed step, declared packages missing, unresolved version conflicts."""
        reasons = []
        if report.get("status") == "setup_failed":
            reasons.append(str(report.get("error") or "an install step failed"))
        if info.get("missing_packages"):
            reasons.append("declared packages not installed: " + ", ".join(info["missing_packages"]))
        remaining = (report.get("dependency_conflicts") or {}).get("remaining") or []
        if remaining:
            reasons.append(
                "installed packages are inconsistent: "
                + "; ".join(env_check.describe_conflict(c) for c in remaining[:3])
            )
        return reasons

    async def _block_incomplete_environment(self, state: dict, report: dict, reasons: list[str], ctx) -> None:
        """STRICT GUARDRAIL: nothing after environment_setup starts until every requirement is installed and consistent.

        The reviewer can only retry or stop. When another Python would fix it (a package with no wheel for this one), the
        retry proposes it: the next plan lists the download and needs its own approval.
        """
        info = state["environment"]
        info["status"] = "incomplete"
        proposal = await asyncio.to_thread(
            env_recovery.python_fallback, report, self.workspace, state.get("env_python_tried", [])
        )
        if proposal:
            state["env_python_proposal"] = proposal
        else:
            state.pop("env_python_proposal", None)
        self.audit("environment_incomplete", reasons=reasons, python_fallback=proposal)
        if report.get("status") == "ready":  # a failed step already fired OnEnvError
            self.hooks.notify(
                ON_ENV_ERROR,
                ENV_STAGE,
                status="incomplete",
                error="; ".join(reasons)[:600],
                attempt=state.get("environment_attempts", 0),
            )
        how = (
            f"Retry rebuilds the environment with Python {proposal}: the new install plan lists that download and you approve it."
            if proposal
            else "Install the missing build tools (Windows: Microsoft C++ Build Tools; macOS: xcode-select --install; Linux: build-essential) "
            "or a Python version that has wheels for the pinned packages, then Retry."
        )
        await self.ask(
            ENV_STAGE,
            "error",
            state,
            ctx,
            title="Environment incomplete: the strict guardrail blocks the next stage",
            details={
                "reasons": reasons,
                "python_fallback": proposal,
                "warnings": report.get("warnings", []),
                "agent_diagnosis": report.get("agent_diagnosis"),
                "setup_guide": setup_guide(info["plan"], self.workspace, state.get("repo_knowledge", "")),
            },
            error="Not every requirement is installed, so no later stage will start. "
            + " ".join(reasons)
            + " "
            + how,
        )

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
            ready_components=frozenset(
                (c["ecosystem"], c["cwd"])
                for c in state.get("environment", {})
                .get("plan", {})
                .get(
                    "components",
                    [
                        {
                            "ecosystem": state.get("environment", {})
                            .get("plan", {})
                            .get("ecosystem", stack.primary),
                            "cwd": ".",
                        }
                    ],
                )
            )
            if env_ready
            else frozenset(),
            **(
                {"assume_present": covered}
                if (covered := Provisioned.from_dicts(state.get("toolchains")).tools())
                else {}
            ),
        )
        if state.get("launch_app") and not plan.blocked_reason:
            state["app_url"] = f"http://127.0.0.1:{app_launcher.free_port()}"  # fresh port for every attempt
            port = int(state["app_url"].rsplit(":", 1)[1])
            plan.app = await asyncio.to_thread(
                app_launcher.detect_launch, workspace, port, state.get("app_component")
            )
            if plan.app and plan.app.get("blocked_reason"):
                plan.blocked_reason = plan.app["blocked_reason"]
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
                "environment": "ready (isolated during Test execution setup)"
                if env_ready
                else "not prepared: setup steps included",
                "steps": [
                    *(
                        [
                            {
                                "name": f"start-{service['kind']}",
                                "kind": "app",
                                "command": shlex.join(service["argv"]),
                                "cwd": service.get("cwd", "."),
                                "url": service.get("url"),
                                "env": service.get("env", {}),
                            }
                            for service in plan.app.get("services", [plan.app])
                        ]
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
        raise_if_cancelled()
        info = state["execution_plan"]
        if request["details"].get("plan_hash") != info["hash"]:
            raise ValueError("Execution plan changed after it was shown for approval.")
        plan = RunPlan(
            adapter=info["plan"]["adapter"],
            steps=[Step(**st) for st in info["plan"]["steps"]],
            notes=info["plan"].get("notes", []),
            app=info["plan"].get("app"),
        )
        # Compilation/collection can execute repository code: only after this execution approval.
        await self._build_gate(state["artifacts"]["test_generation"], batch=0)
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
        if report["status"] in {"blocked", "no_test_evidence", "setup_failed"}:
            eco = state.get("environment", {}).get("plan", {}).get("ecosystem", "")
            note = await self._agent_diagnosis("execution", state, report, eco)
            if note is not None:
                report["agent_diagnosis"] = note
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

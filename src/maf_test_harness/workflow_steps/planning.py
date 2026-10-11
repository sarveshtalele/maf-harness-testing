"""Planning operations extracted from the checkpointed Harness."""

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from ..agent import prompt_settings
from ..agent.personas import persona_instructions
from ..agent.runtime import AgentOutputError, GatewayUnavailableError
from ..envs.environment import build_environment_plan
from ..envs.setup_guide import setup_guide
from ..llm.prompts import build_prompt
from ..models import (
    GeneratedFilesDraft,
    RequirementsDraft,
    RequirementsInventoryDraft,
    TestDesignDraft,
    TestPlan,
)
from ..qa.controls import add_plan_controls, case_controls, plan_controls
from ..qa.quality import existing_requirement_witnesses, existing_test_evidence
from ..qa.report import design_cases
from ..repo.analyzer import build_repository_evidence
from ..repo.stack import detect_stack
from ..repo.testability import retarget_cases, testability_for_targets
from ..run_options import restrict_levels
from ..settings import (
    EXECUTION_TIMEOUT_SECONDS,
)
from ..skills.config import skills_for
from ..skills.contracts import (
    expand_generated_lines,
    finalize_design,
    finalize_discovered_requirements,
    validate_generated_files,
)
from ..skills.host import execute_host_skill
from ..workflow_support import (
    CORE_STAGES,
    MODEL_REPAIR_ATTEMPTS,
    _allowed_levels,
    _compact_repo,
    _compact_requirements,
    _plan_summary,
    atomic_json,
    case_budget,
)
from . import design_review
from .design import design_tests_batched
from .planning_batches import plan_tests_batched, planning_is_batched


class PlanningSteps:
    async def _call_model(self, *, stage, persona, prompt, state, **kwargs):
        settings = prompt_settings.load(self.run_dir / "prompts.json")
        role = kwargs.pop("role", None)
        if not role:
            persona = settings["personas"].get(stage, persona)
        batch = kwargs.pop("prompt_batch", None)
        key = f"{stage}:{batch}" if batch is not None else stage
        prompt = settings["overrides"].get(key) or prompt
        if settings["repository_prompt"]:
            persona += "\n\nUSER REPOSITORY REVIEW GUIDANCE:\n" + settings["repository_prompt"]
        identifier = uuid4().hex
        folder = self.run_dir / "prompts"
        folder.mkdir(exist_ok=True)
        record = {
            "id": identifier,
            "created_at": datetime.now(UTC).isoformat(),
            "stage": stage,
            "role": role or "stage_author",
            "batch": batch,
            "override_key": key,
            "persona": persona,
            "user_prompt": prompt,
            "sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        }
        state.setdefault("prompt_records", []).append(
            {k: record[k] for k in ("id", "stage", "role", "batch", "override_key", "sha256")}
        )
        atomic_json(folder / f"{identifier}.json", record)
        self.save()
        try:
            return await self.runtime.run_stage(
                stage=stage, persona=persona, prompt=prompt, state=state, **kwargs
            )
        finally:
            record.update(state.get("model_instructions", {}).get(stage, {}))
            record["skills"] = [
                {
                    "name": name,
                    "instructions": (self.skills_root / name / "SKILL.md").read_text(encoding="utf-8"),
                }
                for name in (kwargs.get("skill_name"), *kwargs.get("companions", ()))
                if name
            ]
            atomic_json(folder / f"{identifier}.json", record)

    def _settle_response_cache(self, stage, validated: bool) -> None:
        """Tell the runtime whether the host accepted the last model result (only accepted ones are cached)."""
        settle = getattr(self.runtime, "settle_response_cache", None)
        if settle is not None:
            settle(stage, validated)

    def _selected_app_source(
        self, state: dict, *, preferred: list[str] | None = None, max_chars: int = 14000
    ) -> dict[str, str]:
        component = state.get("app_component")
        if not component:
            return {}
        paths = state["artifacts"].get("repo_analysis", {}).get("source_files", [])
        paths = sorted(
            (p for p in paths if p.startswith(component.rstrip("/") + "/")),
            key=lambda p: (
                p not in (preferred or []),
                not p.endswith(("App.tsx", "App.jsx")),
                "/components/" not in p and "/hooks/" not in p,
                p,
            ),
        )
        facts, remaining = {}, max_chars
        for path in paths:
            text = self._read_repo_file(path)
            if text:
                excerpt = text[: min(16000 if max_chars > 14000 else 3000, remaining)]
                facts[path] = excerpt
                remaining -= len(excerpt)
                if remaining <= 0:
                    break
        return facts

    async def model(self, stage, state, ctx):
        if stage == "requirements" and state.get("run_mode") == "greenfield":
            await self.author_specs(state, ctx)
            return
        if stage == "requirements" and state.get("requirements_mode", "model") == "deterministic":
            repo = state["artifacts"]["repo_analysis"]
            analysis = dict(repo.get("reverse_engineering", {}))
            draft = await asyncio.to_thread(
                execute_host_skill,
                self.run_dir,
                self.skills_root,
                stage,
                "requirement-analysis",
                {
                    "analysis": analysis,
                    "app_name": repo.get("repo_name") or "Repository",
                    "knowledge": state.get("repo_knowledge", ""),
                },
                state,
                self.audit,
            )
            state["draft"] = draft
            artifact = await self.validate(stage, state)
            artifact["metadata"]["generator"] = "requirement-analysis"
            artifact["metadata"]["execution_mode"] = "host-static"
            artifact["analysis_mode"] = "deterministic"
            artifact["model_calls"] = 0
            self.audit(
                "deterministic_requirements",
                stage=stage,
                model_calls=0,
                requirements=len(artifact["requirement_model"]["requirements"]),
            )
            await self.review(stage, artifact, state, ctx)
            return
        if stage == "test_generation":
            artifact = await self._generate_tests_batched(state)
        elif (
            stage == "test_design"
            and len(state["artifacts"]["requirements"]["requirement_model"]["requirements"]) > 12
        ):
            artifact = await design_tests_batched(self, state)
        elif stage == "test_planning" and planning_is_batched(state["artifacts"]["requirements"]):
            artifact = await plan_tests_batched(self, state)
        else:
            state["design_reviews"] = {}
            artifact = await self._model_artifact(stage, state)
        if stage == "test_design":
            design_review.apply_design_reviews(artifact, state)
        await self.review(stage, artifact, state, ctx)

    async def _model_artifact(self, stage, state, requirements_override=None, prompt_batch=None):
        requirements_source = requirements_override or state["artifacts"].get("requirements", {})
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
                "Select native test frameworks automatically and keep the plan compact: group requirements into at "
                "most 20 areas by component, at most 6 short scenarios per area, one sentence per text field."
            )
            if requirements_override:
                assignment += (
                    " This is one batch of a larger repository: plan ONLY the supplied requirements, "
                    "at most 6 areas, at most 4 scenarios per area."
                )
            upstream = {"requirements": _compact_requirements(requirements_source)}
            if requirements_source.get("analysis_mode") == "deterministic":
                upstream["requirements"]["requirements"] = [
                    {key: item.get(key) for key in ("id", "source_files", "source_symbol", "source_line")}
                    for item in upstream["requirements"]["requirements"]
                ]
        elif stage == "test_design":
            response_format = TestDesignDraft
            assignment = (
                "Create traceable implementation-ready test cases in the compact draft schema. Cover approved requirements "
                "with distinct source-backed cases; do not manufacture boundaries or contracts. Every supplied requirement "
                "needs a case or an explicit ID-linked exclusion reason. A method link requires actually invoking that method; "
                "constructors do not cover setters. Use source-valid positive fixtures and complement existing tests. "
                "Do not claim full application coverage from a declaration inventory. "
                "Every supplied requirement must be accounted for by exactly one of: a new traceable case; an existing "
                "repository test that directly invokes and asserts it (cite path::test name in gaps with the exact "
                "requirement ID); or an ID-linked gap with a concrete reason. Target the method that actually implements "
                "the expected behavior (arithmetic done in a callee belongs to the callee). "
                f"Case budget: at most {case_budget(state)} case(s) per requirement; prefer one positive case "
                "plus the most important negative or boundary case. The host drops cases beyond the budget."
                + (
                    f" Design ONLY {' and/or '.join(state['test_types'])} cases (per run test_types); "
                    "no ui, e2e, accessibility, performance, security or cli levels."
                    if state.get("test_types")
                    else ""
                )
            )
            upstream = {
                "requirements": _compact_requirements(requirements_source),
                "test_plan": _plan_summary(state["artifacts"]["test_planning"]),
            }
        else:
            raise ValueError(f"Unsupported model stage: {stage}")

        schema = response_format.model_json_schema()
        if stage == "test_design":
            requirements = _compact_requirements(requirements_source)
            # No per-batch ID enum here: it sits in the schema near the top of the prompt and would make every batch
            # miss the provider prefix cache. The allowed IDs are in `approved_traceability_ids` (late) and host
            # validation (finalize_design) rejects unknown ones. The actor enum is identical for every batch.
            schema["$defs"]["TestDesignCaseDraft"]["properties"]["actor"]["enum"] = sorted(
                {a["name"] for a in requirements["actors"]} | {"System"}
            )
        evidence_paths = re.findall(r"--- FILE: (.*?) ---", state.get("evidence", ""))
        repair = ""
        review_repaired = False
        for attempt in range(1, MODEL_REPAIR_ATTEMPTS + 1):
            prompt = build_prompt(
                {
                    "assignment": assignment,
                    "repository": _compact_repo(state["artifacts"]["repo_analysis"], []),
                    "approved_inputs": {
                        **upstream,
                        "user_repository_context_unverified": state.get("repo_knowledge", ""),
                    },
                    "repository_evidence": state.get("evidence", "")
                    if stage == "requirements"
                    else build_repository_evidence(
                        self.run_dir / "repo",
                        max_files=max(
                            1,
                            len(
                                {
                                    p
                                    for r in requirements_source.get("requirement_model", {}).get(
                                        "requirements", []
                                    )
                                    for p in r.get("source_files", [])
                                }
                            ),
                        ),
                        # The batch's own source files are preferred and the requirements already carry their evidence.
                        max_total_chars=14000,
                        preferred_paths=[
                            p
                            for r in requirements_source.get("requirement_model", {}).get("requirements", [])
                            for p in r.get("source_files", [])
                        ],
                    )
                    if requirements_override
                    else state.get("evidence", "")[:32000],
                    "evidence_file_paths": evidence_paths if stage == "requirements" else None,
                    "existing_tests": existing_test_evidence(
                        self.run_dir / "repo",
                        state["artifacts"]["repo_analysis"].get("test_files", []),
                        requirements_source.get("requirement_model", {}).get("requirements", []),
                    )
                    if stage == "test_design"
                    else None,
                    "existing_test_witnesses": {
                        rid: [f"{w['test_file']}:{w['line']} calls {w['symbol']}" for w in rows[:3]]
                        for rid, rows in existing_requirement_witnesses(
                            self.run_dir / "repo",
                            state["artifacts"]["repo_analysis"].get("test_files", []),
                            requirements_source.get("requirement_model", {}).get("requirements", []),
                        ).items()
                    }
                    if stage == "test_design"
                    else None,
                    "testability": testability_for_targets(
                        state["artifacts"]["repo_analysis"].get("testability"),
                        [
                            *(
                                p
                                for r in requirements_source.get("requirement_model", {}).get(
                                    "requirements", []
                                )
                                for p in [*r.get("source_files", []), str(r.get("source_symbol") or "")]
                            )
                        ],
                        self.run_dir / "repo",
                        state["artifacts"]["repo_analysis"].get("source_files", []),
                    )
                    or None
                    if stage == "test_design"
                    else None,
                    "stack_profile": state.get("stack", {}),
                    "selected_application": {
                        "component": state.get("app_component"),
                        "source": self._selected_app_source(state),
                        "coverage_rule": "When a frontend component is selected and live UI levels are available, include meaningful browser navigation, visible data/empty-state and responsive layout checks traced to its source. Do not replace frontend qualification with API-only coverage. Do not invent unspecified browser capabilities.",
                    },
                    "live_app": {
                        "available": bool(state.get("app_url")),
                        "allowed_test_levels": sorted(_allowed_levels(state)),
                    },
                    "revision_feedback": state.get("feedback", {}).get(stage),
                    "repair_feedback": repair or None,
                    "approved_traceability_ids": {
                        key: [item["id"] for item in _compact_requirements(requirements_source).get(key, [])]
                        for key in (
                            "requirements",
                            "acceptance_criteria",
                            "validation_rules",
                            "api_contracts",
                        )
                    }
                    if stage == "test_design"
                    else None,
                    "traceability_rule": "Copy source IDs verbatim from approved_traceability_ids. Never create source IDs or actors. Unknown behavior remains a gap; do not invent it.",
                    "output_contract": schema,
                    "json_rules": {
                        "single_complete_object": True,
                        "markdown_fences": False,
                        "duplicate_list_items": False,
                        "respect_schema_limits": True,
                        "manual_questions": False,
                    },
                }
            )
            skillset = skills_for(stage, state)
            try:
                result = await self._call_model(
                    stage=stage,
                    skill_name=skillset.primary,
                    companions=skillset.companions,
                    persona=persona_instructions(stage),
                    prompt=prompt,
                    state=state,
                    persist=self.save,
                    prompt_batch=prompt_batch,
                    response_format=response_format,
                )
            except GatewayUnavailableError:
                self._settle_response_cache(stage, False)
                raise  # an outage is not a bad answer: no repair attempts
            except (
                ValidationError,
                AgentOutputError,
            ) as exc:  # the runtime wraps pydantic errors as AgentOutputError
                self._settle_response_cache(stage, False)
                self.audit(
                    "stage_repair", stage=stage, attempt=attempt, error="Invalid model JSON/schema output"
                )
                if attempt == MODEL_REPAIR_ATTEMPTS:
                    raise
                detail = (
                    str(exc.errors(include_input=False))
                    if isinstance(exc, ValidationError)
                    else str(exc).split("[type=")[0]
                )[:900]
                truncated = re.search(
                    r"EOF while parsing|did not return a complete JSON|truncat", str(exc), re.I
                )
                repair = (
                    "The previous response was cut off at the output limit. Return a MUCH more compact JSON object: "
                    "fewer list items, one short sentence per text field, no repeated wording. "
                    if truncated
                    else "The previous response was invalid JSON/schema output. Return exactly one schema-valid JSON "
                    "object, escape newlines/control characters within strings, and follow the output_contract. "
                ) + detail
                continue
            if result["status"] != "completed":
                self._settle_response_cache(stage, False)
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
                artifact = await self.validate(stage, state, requirements_source=requirements_override)
            except ValueError as exc:  # temperature 0 repeats itself, so the retry must carry the reason
                self._settle_response_cache(stage, False)
                self.audit("stage_repair", stage=stage, attempt=attempt, error=str(exc)[:600])
                if attempt == MODEL_REPAIR_ATTEMPTS:
                    raise
                repair = (
                    f"Attempt {attempt} was rejected by host validation: {str(exc)[:16000]}. Return a corrected "
                    "complete JSON object that fixes exactly this problem and keeps everything else valid."
                )
                continue
            if stage == "test_design" and design_review.enabled():
                reviews = await design_review.review_design_cases(
                    self, state, design_cases(artifact), prompt_batch or 0
                )
                weak = design_review.flagged(reviews)
                if weak and not review_repaired and attempt < MODEL_REPAIR_ATTEMPTS:
                    review_repaired = True  # one reviewer-driven repair per batch, then keep and mark
                    self.audit("design_review_repair", stage=stage, batch=prompt_batch, cases=len(weak))
                    repair = design_review.repair_feedback(weak)
                    continue
            self._settle_response_cache(stage, True)
            break
        return artifact

    async def validate(self, stage, state, requirements_source=None):

        draft = state["draft"]
        if stage == "requirements":
            inventory = state.get("requirements_mode") == "deterministic"
            validated = (
                (RequirementsInventoryDraft if inventory else RequirementsDraft)
                .model_validate(draft)
                .model_dump()
            )
            evidence_paths = (
                {
                    f["path"]
                    for f in state["artifacts"]["repo_analysis"]
                    .get("reverse_engineering", {})
                    .get("files", [])
                }
                if inventory
                else set(re.findall(r"--- FILE: (.*?) ---", state.get("evidence", "")))
            )
            for requirement in validated["requirements"]:
                unknown = set(requirement["source_files"]) - evidence_paths
                if unknown:
                    raise ValueError(f"Requirement cites files not supplied as evidence: {sorted(unknown)}")
            requirements = finalize_discovered_requirements(validated, state["repo_url"])
            root = (self.run_dir / "repo").resolve()

            def check_evidence(value):
                if isinstance(value, dict):
                    for path in value.get("source_files", []):
                        target = (root / path).resolve()
                        if not target.is_relative_to(root) or not target.is_file():
                            raise ValueError(
                                f"Requirement evidence does not exist in the analyzed repository: {path}"
                            )
                    for child in value.values():
                        check_evidence(child)
                elif isinstance(value, list):
                    for child in value:
                        check_evidence(child)

            check_evidence(requirements)
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
                requirements["setup_guide"] = setup_guide(
                    requirements["environment_plan"],
                    self.workspace,
                    state.get("repo_knowledge", ""),
                    source_root=self.run_dir / "repo",
                )
            return requirements
        if stage == "test_planning":
            plan = TestPlan.model_validate(draft)
            if not plan.areas or not plan.execution_order:
                raise ValueError("Test plan must contain at least one area and execution step.")
            return add_plan_controls(
                plan.model_dump(), requirements_source or state["artifacts"]["requirements"]
            )
        if stage == "test_design":
            if state.get("test_types") and isinstance(draft, dict):
                notes = restrict_levels(draft, state["test_types"])
                if notes:
                    draft["gaps"] = [*(draft.get("gaps") or []), *notes]
            design = finalize_design(
                draft,
                requirements_source or state["artifacts"]["requirements"],
                state["repo_url"] or "greenfield:specs",  # no repository in greenfield runs
                self.skills_root,
                allowed_levels=_allowed_levels(state),
                case_budget=case_budget(state),
            )
            if requirements_source is None and design["gaps_report"].get("budget_trimmed"):
                self.audit(
                    "design_cases_trimmed",
                    stage=stage,
                    budget=case_budget(state),
                    trimmed=design["gaps_report"]["budget_trimmed"]["case_ids"],
                )
            for note in retarget_cases(
                design_cases(design),
                self.run_dir / "repo",
                state["artifacts"].get("repo_analysis", {}).get("source_files", []),
            ):
                self.audit("design_case_retargeted", stage=stage, note=note)
                design.setdefault("host_notes", []).append(note)
            controls = plan_controls(
                state["artifacts"]["requirements"], state["artifacts"].get("test_planning")
            )
            for case in design_cases(design):
                case["control_ids"] = case_controls(case, controls)
            self._require_coverage_or_reason(design, state, requirements_source=requirements_source)
            if (
                requirements_source is None
                and state.get("app_component")
                and any(
                    path.startswith(state["app_component"].rstrip("/") + "/")
                    and path.endswith((".tsx", ".jsx", ".vue", ".svelte", ".html"))
                    for path in state["artifacts"]["repo_analysis"].get("source_files", [])
                )
                and _allowed_levels(state) & {"ui", "e2e", "accessibility"}
                and not any(
                    case.get("level") in {"ui", "e2e", "accessibility"} for case in design_cases(design)
                )
            ):
                raise ValueError(
                    "Selected frontend lacks browser coverage. Add source-backed UI/E2E cases for its visible workflow and responsive layout; API-only cases cannot qualify the selected frontend."
                )
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

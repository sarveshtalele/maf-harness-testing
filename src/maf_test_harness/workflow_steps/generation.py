"""Generation operations extracted from the checkpointed Harness."""

import asyncio
import json
import re
from copy import deepcopy
from typing import Any
from uuid import uuid4

from ..agent.personas import persona_instructions
from ..agent.runtime import GatewayUnavailableError
from ..envs import Provisioned
from ..envs.platform_compat import raise_if_cancelled
from ..llm.prompts import generation_prompt
from ..models import GeneratedFilesDraft
from ..qa.quality import (
    existing_requirement_witnesses,
    existing_test_evidence,
    require_complete_intake,
    validate_java_traceability,
)
from ..qa.report import design_cases
from ..qa.triage import remedy_for
from ..repo.analyzer import (
    build_repository_evidence,
    collaborator_signatures,
    extract_target_sources,
)
from ..repo.import_recipes import python_import_recipes
from ..repo.routes import extract_routes
from ..repo.stack import detect_stack
from ..repo.testability import testability_for_targets
from ..settings import (
    AUTOFIX,
    BUILD_GATE,
    MAX_FAILED_BATCH_RATIO,
)
from ..skills.config import skills_for
from ..skills.contracts import (
    case_marker_meta,
    ensure_generated_case_markers,
    expand_generated_lines,
    flatten_nested_with,
    validate_generated_files,
)
from ..tools.autofix import autofix_files
from ..tools.build_gate import try_build
from ..tools.code_intel import RepoContext, source_contracts, static_gate
from ..workflow_support import (
    TEST_GENERATION_BATCH_SIZE,
    TEST_GENERATION_MAX_ATTEMPTS,
    _canonicalize_case_ids,
    _compact_design,
    _compact_repo,
    _generation_policy,
    _plan_summary,
    _requirements_for,
    atomic_json,
    digest,
    retry_reminder,
)
from .greenfield import has_no_code
from .test_review import review_generated_tests


class GenerationSteps:
    async def _generate_tests_batched(self, state: dict) -> dict:
        """Generate approved design cases in small validated batches with bounded repair retries."""

        require_complete_intake(state)
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
                "quality_policy": "strict-source-invocation-reviewed-v2",
                "skills": {
                    name: (self.skills_root / name / "SKILL.md").read_text(encoding="utf-8")
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
        failures: list[dict[str, Any]] = []  # fail-soft: batches that exhausted their attempts
        covered: set[str] = set()
        languages: list[str] = []
        frameworks: list[str] = []
        rationales: list[str] = []
        case_reviews: list[dict] = []
        raw_batches: list[str] = []

        total_batches = (len(cases) + TEST_GENERATION_BATCH_SIZE - 1) // TEST_GENERATION_BATCH_SIZE
        for batch_index, start in enumerate(range(0, len(cases), TEST_GENERATION_BATCH_SIZE), 1):
            raise_if_cancelled()
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
            work, kept_files, kept_reviews, failed_source = batch, [], [], []
            if accepted is not None:
                cached_ids = {i for f in accepted["files"] for i in f["case_ids"]}
                regenerate = batch_ids & failed_ids
                if not cached_ids >= batch_ids:
                    accepted = None
                elif regenerate:
                    # Heal: only the failing cases go back to the model; passing files are kept byte for byte.
                    failed_source = [f for f in accepted["files"] if set(f["case_ids"]) & regenerate]
                    kept_files = [f for f in accepted["files"] if not set(f["case_ids"]) & regenerate]
                    kept_reviews = [
                        r for r in accepted.get("case_reviews", []) if r["case_id"] not in regenerate
                    ]
                    work = [c for c in batch if str(c["id"]) in regenerate]
                    accepted = None
                elif validate_generated_files(accepted, batch_ids)["uncovered_case_ids"]:
                    accepted = None
            work_ids = {str(case["id"]) for case in work}
            source_files = state["artifacts"]["repo_analysis"].get("source_files", [])
            # Targets are usually symbols ("Class.method"); the linked requirements' source files name the modules,
            # so both are passed (files select recipes, symbols pick the import names).
            recipes = python_import_recipes(
                self.run_dir / "repo",
                source_files,
                [*(str(c.get("target") or "") for c in work), *preferred],
            )
            batch_source = (
                extract_target_sources(
                    self.run_dir / "repo", [str(case.get("target") or "") for case in work], source_files
                )
                or target_source
            )
            if any(case.get("level") in {"ui", "e2e", "accessibility"} for case in work):
                app_sources = self._selected_app_source(state, preferred=preferred, max_chars=96000)
                batch_source += "\n".join(
                    f"--- SELECTED APPLICATION SOURCE: {path} ---\n{source}"
                    for path, source in app_sources.items()
                )
            collaborators = collaborator_signatures(
                self.run_dir / "repo",
                batch_source,
                source_files,
                targets=[str(c.get("target") or "") for c in work],
            )
            rounds = state.setdefault("generation_rounds", {})
            carried: dict[
                str, dict
            ] = {}  # case id -> its file from an earlier attempt that passed static checks
            for attempt in range(1, 1 if accepted is not None else TEST_GENERATION_MAX_ATTEMPTS + 1):
                # A deterministic model repeats a rejected answer verbatim: vary the sampling on every retry.
                retry_temperature = min(0.4, 0.3 * (attempt - 1) + 0.25 * rounds.get(str(batch_index), 0))
                prompt = generation_prompt(
                    {
                        "assignment": (
                            "Generate executable test source ONLY for this approved batch. Generate exactly one file per "
                            "supplied case ID and exactly one concrete test declaration in that file. Set case_ids to a one-item "
                            "array containing the corresponding supplied ID. The host adds canonical CASE_ID source markers. "
                            "Name the single test declaration descriptively from the case title in the language convention "
                            "(test_snake_case in Python, camelCase in Java/JS/TS, PascalCase in C#); where the framework supports "
                            "display names put '<case_id>: <title>' there (JUnit 5 @DisplayName, pytest docstring, jest test('...') string). "
                            "Use only necessary imports. Never emit placeholders, install dependencies, or execute tests."
                            + (
                                " Repair the provided failed_generated_source using the recorded execution differences in revision_feedback. Verify the actual implementation before correcting an expectation; preserve meaningful assertions and passing files."
                                if failed_source
                                else ""
                            )
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
                        # Targets are usually symbols ("Class.method"); the linked requirements' source files name
                        # the modules, so both are passed (files select recipes, symbols pick the import names).
                        "import_recipes": recipes or None,
                        "testability": testability_for_targets(
                            state["artifacts"]["repo_analysis"].get("testability"),
                            [str(case.get("target") or "") for case in work],
                            self.run_dir / "repo",
                            source_files,
                        )
                        or None,
                        "failed_generated_source": failed_source or None,
                        "collaborator_signatures": collaborators or None,
                        "state_and_schema_source": source_contracts(self._repo_context()),
                        "existing_tests": existing_test_evidence(
                            self.run_dir / "repo",
                            state["artifacts"]["repo_analysis"].get("test_files", []),
                            [
                                r
                                for r in requirements.get("requirement_model", {}).get("requirements", [])
                                if r["id"] in linked_ids
                            ],
                        )
                        if work
                        else "",
                        "strict_quality_rules": [
                            "Never convert a filesystem path into an import by normalising '-', spaces or leading digits; import only modules proven by source_files.",
                            "Prefer the repository's existing public execution API and existing test patterns/fixtures (for example a registry fixture) over importing internals.",
                            "Never inject or reference fields/members absent from the source. Never use tautological assertions (for example a property map that is always non-null).",
                            "Never copy or re-implement application code inside a test; import and exercise the real implementation. Targets listed in testability.non_public_targets cannot be imported: test them through the public function, component or class that uses them.",
                            "Respect a supplied `testability` section: low testability targets need an integration/behaviour-level test, not mocks of static acquisition.",
                            "Use source-valid fixture values for positive cases. Invalid values belong only in explicit boundary/negative/validation cases with a real rejection assertion.",
                            "Read existing tests; add complementary behavior instead of repeating an existing assertion. Constant assertions and mock-only assertions do not qualify application behavior.",
                            "Static links are not measured behavior, line, branch or mutation coverage. Never claim those measurements without executed tool evidence.",
                        ],
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
                    }
                )
                try:
                    generation_skills = skills_for("test_generation", state)
                    result = await self._call_model(
                        stage="test_generation",
                        prompt_batch=batch_index,
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
                        artifact=attempt_path.relative_to(self.run_dir).as_posix(),
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
                    candidate = ensure_generated_case_markers(
                        candidate,
                        {
                            str(c["id"]): case_marker_meta(c, self.run_dir.name, state.get("run_name", ""))
                            for c in work
                        },
                    )
                    if AUTOFIX:
                        candidate, fixes = autofix_files(self._repo_context(), candidate, recipes, preferred)
                        tooling = state.setdefault(
                            "tooling", {"autofix": 0, "build_checks": 0, "build_failures": 0}
                        )
                        tooling["autofix"] += len(fixes)
                        for fix in fixes:
                            self.audit("autofix", batch=batch_index, attempt=attempt, **fix)
                    # A repair answer that fixes the one reviewed case and drops its siblings (seen live: batch 39
                    # returned 1 of 3 files) keeps the siblings' earlier, already validated files.
                    have = {i for f in candidate["files"] for i in f["case_ids"]}
                    used = {f["path"] for f in candidate["files"]}
                    for cid in sorted(work_ids - have):
                        old = carried.get(cid)
                        if old and old["path"] not in used:
                            candidate["files"].append(deepcopy(old))
                            used.add(old["path"])
                            self.audit("generation_case_carried_over", batch=batch_index, case_id=cid)
                    raw_batches.append(json.dumps(candidate, separators=(",", ":"), default=str))
                    validation = validate_generated_files(candidate, work_ids)
                    validate_java_traceability(candidate, work, requirements, self.run_dir / "repo")
                    self._check_imports(candidate, state, preferred)
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
                    carried.update({i: f for f in candidate["files"] for i in f["case_ids"]})
                    self._settle_response_cache("test_generation", True)  # accepted: cache this model answer
                    # The source-backed reviewer needs application code; a no-repo greenfield run has none yet.
                    candidate["case_reviews"] = (
                        []
                        if has_no_code(state)
                        else await review_generated_tests(self, state, candidate, work, batch_index)
                    )
                    accepted = {
                        **candidate,
                        "files": kept_files + candidate["files"],
                        "case_reviews": kept_reviews + candidate["case_reviews"],
                    }
                    cache["batches"][str(batch_index)] = accepted
                    self.save()
                    break
                except Exception as exc:
                    self._settle_response_cache("test_generation", False)
                    if isinstance(exc, GatewayUnavailableError):
                        raise  # gateway down, not a bad batch: stop now; Retry resumes from the cached batches
                    repair_feedback = (
                        f"Attempt {attempt} failed generation or static validation: {exc}. "
                        + (f"HOW TO FIX: {remedy_for(str(exc))} " if remedy_for(str(exc)) else "")
                        + "Regenerate ONLY this batch. "
                        "Return one complete schema-valid JSON object. Generate one file per supplied case ID, exactly one "
                        "test declaration per file, and one matching case_ids entry. The host supplies CASE_ID comments. "
                        "Keep imports minimal and source complete. " + retry_reminder(policy)
                    )
                    self.audit(
                        "test_generation_repair",
                        batch=batch_index,
                        attempt=attempt,
                        error=str(exc),
                    )
                    if attempt == TEST_GENERATION_MAX_ATTEMPTS:
                        rounds[str(batch_index)] = rounds.get(str(batch_index), 0) + 1
                        failures.append(
                            {
                                "batch": batch_index,
                                "case_ids": sorted(work_ids),
                                "run_id": self.run_dir.name,
                                "run_name": state.get("run_name", ""),
                                "attempts": attempt,
                                "last_error": str(exc)[:1000],
                            }
                        )
                        self.audit(
                            "test_generation_batch_failed",
                            batch=batch_index,
                            run_id=self.run_dir.name,
                            run_name=state.get("run_name", ""),
                            case_ids=sorted(work_ids),
                            error=str(exc)[:300],
                        )
                        self.save()

            if accepted is None and failures and failures[-1]["batch"] == batch_index:
                # Recorded; a stage rerun (or heal) retries it. Heal rounds keep the batch's passing files.
                accepted_files.extend(kept_files)
                case_reviews.extend(kept_reviews)
                accepted_paths.update(item["path"] for item in kept_files)
                continue
            if accepted is None:
                raise ValueError(
                    f"Test generation batch {batch_index}/{total_batches} produced no accepted output."
                )
            accepted_files.extend(accepted["files"])
            case_reviews.extend(accepted.get("case_reviews", []))
            accepted_paths.update(item["path"] for item in accepted["files"])
            covered.update(batch_ids)
            if accepted["selected_language"] not in languages:
                languages.append(accepted["selected_language"])
            if accepted["selected_framework"] not in frameworks:
                frameworks.append(accepted["selected_framework"])
            if accepted["rationale"] not in rationales:
                rationales.append(accepted["rationale"])

        failed_case_ids = {cid for failure in failures for cid in failure["case_ids"]}
        if failures and (len(failures) / total_batches > MAX_FAILED_BATCH_RATIO or not accepted_files):
            raise ValueError(
                f"Test generation failed for {len(failures)}/{total_batches} batches "
                f"(limit {MAX_FAILED_BATCH_RATIO:.0%}). Last error: {failures[-1]['last_error']}"
            )
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
            "generation_failures": failures,
            "case_reviews": sorted(case_reviews, key=lambda r: (-r["confidence"], r["case_id"])),
        }
        reviews_by_id = {r["case_id"]: r for r in case_reviews}
        for case in final_payload["case_catalog"]:
            review = reviews_by_id.get(case["id"], {})
            case.update(
                confidence=review.get("confidence"),
                reviewer_verdict="generation_failed"
                if case["id"] in failed_case_ids
                else review.get("verdict"),
                reviewer_reason=review.get("reason"),
            )
        final_payload["case_catalog"].sort(key=lambda c: -(c.get("confidence") or 0))
        validation = validate_generated_files(final_payload, all_ids - failed_case_ids)
        validation["generation_failed_case_ids"] = sorted(failed_case_ids)
        validation["quality"] = validate_java_traceability(
            final_payload, cases, state["artifacts"].get("requirements", {}), self.run_dir / "repo"
        )
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

    def _require_coverage_or_reason(self, design: dict, state: dict, requirements_source=None) -> None:
        """Design policy: unit cases exist, every actor has cases, every requirement is covered (or explained).

        Each excluded requirement needs an explicit ID-linked reason. Unexplained gaps
        are rejected on every attempt, including the last repair attempt.
        """
        arts = state["artifacts"]
        gap_entries = [str(g) for g in state.get("draft", {}).get("gaps", [])]
        cited = " ".join(gap_entries).lower()

        def mentions(word: str) -> bool:
            return re.search(r"(?<![\w-])" + re.escape(word.lower()) + r"(?![\w-])", cited) is not None

        cases = design_cases(design)
        problems: list[str] = []
        # A component batch (requirements_source given) may legitimately be UI-only or single-actor; the unit-level
        # and per-actor policies apply to the merged design only. Per-requirement coverage is checked in both.
        whole_design = requirements_source is None
        if (
            whole_design
            and arts["repo_analysis"].get("source_file_count")
            and not mentions("unit")
            and not any(c.get("level") == "unit" for c in cases)
        ):
            problems.append(
                "The design has no unit-level test case. Add unit cases for the core functions/classes, "
                "or explain in `gaps` (mention 'unit') why none is possible."
            )
        idle = [
            a["name"]
            for a in (arts["requirements"].get("actors", []) if whole_design else [])
            if a["name"].lower() != "system"
            and not any(c.get("actor") == a["name"] for c in cases)
            and not mentions(a["name"])
        ]
        if idle:
            problems.append(
                f"Actor(s) without any test case: {', '.join(idle)}. Design cases per actor journey, or "
                "mention the actor in `gaps` with the reason."
            )
        requirements = (
            (requirements_source or arts["requirements"]).get("requirement_model", {}).get("requirements", [])
        )
        uncovered = design["gaps_report"]["uncovered_requirements"]
        try:
            root = self.run_dir / "repo"
        except Exception:  # bare host without a run dir
            root = None
        test_files = arts["repo_analysis"].get("test_files", [])
        witnesses = (
            existing_requirement_witnesses(
                root, test_files, [r for r in requirements if r.get("id") in uncovered]
            )
            if root and test_files
            else {}
        )
        id_pattern = re.compile(r"REQ-[A-Z0-9][A-Z0-9-]*[A-Z0-9]", re.I)

        def explained(rid: str) -> bool:
            token = re.compile(r"(?<![\w-])" + re.escape(rid) + r"(?![\w-])", re.I)
            for entry in gap_entries:
                if token.search(entry):
                    reason = id_pattern.sub("", token.sub("", entry))
                    if len(re.sub(r"[\W_]+", "", reason)) >= 10:
                        return True
            return False

        excluded = [rid for rid in uncovered if rid not in witnesses and explained(rid)]
        design["gaps_report"]["pre_existing_test_witnesses"] = witnesses
        design["gaps_report"]["excluded_requirements"] = excluded
        missing = [rid for rid in uncovered if rid not in witnesses and rid not in excluded]
        if missing:
            listing = "; ".join(missing)
            problems.append(
                f"{len(missing)} requirement(s) have no test case. Add cases tracing to them, or cite each "
                f"requirement ID in `gaps` with the reason. Uncovered: {listing}"
            )
        if not problems:
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
        if self.state.get("workflow_version") == 2 and self.state.get("active_stage") != "test_execution":
            return {
                "status": "skipped",
                "reason": "runtime validation deferred until execution approval",
                "errors": [],
            }
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
        accepted = [
            f
            for artifact in self.state.get("accepted_generation_batches", {}).get("batches", {}).values()
            for f in artifact.get("files", [])
        ]
        by_path = {f["path"]: f for f in [*accepted, *candidate["files"]]}
        report = await asyncio.to_thread(self._build_files, list(by_path.values()))
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

    def _check_imports(self, candidate: dict, state: dict, preferred: list[str] | None = None) -> None:
        """Static gates (syntax, imports, undefined names, mocks, patch targets, URLs) on every generated file."""
        from ..envs.runners import repository_components, resolve_ecosystem

        ctx = self._repo_context()
        source_root = self.workspace if self.workspace.exists() else self.run_dir / "repo"
        stack = detect_stack(source_root)
        node_roots = [cwd for kind, cwd in repository_components(stack, source_root) if kind == "node"]
        for item in candidate["files"]:
            if (
                resolve_ecosystem(stack, [item]) == "node"
                and node_roots
                and not any(cwd == "." or item["path"].startswith(cwd + "/") for cwd in node_roots)
            ):
                raise ValueError(
                    f"Node test {item['path']} is outside its package.json component. "
                    f"Place this spec under a NEW tests/ path inside one of {node_roots}; "
                    "the runner resolves dependencies from that component."
                )
            static_gate(ctx, item["path"], item["content"], preferred)

"""Greenfield mode: specs from documents/Jira replace repository analysis; no repository means a scaffold, no execution."""

import asyncio
import json
from datetime import UTC, datetime

from pydantic import ValidationError

from ..agent.personas import persona_instructions
from ..agent.runtime import AgentOutputError, GatewayUnavailableError
from ..llm.prompts import build_prompt
from ..models import RepoAnalysis, SpecsDraft
from ..repo.specs import build_specs, section_ref, specs_to_requirements
from ..repo.stack import detect_stack
from ..workflow_support import ENV_STAGE, MODEL_REPAIR_ATTEMPTS, atomic_json, digest

SPEC_SKILL = "spec-authoring"
SPEC_BATCH = 10  # sections per model call
NO_CODE_NOTE = "skipped: no application code"
SCAFFOLDS = {
    "python": {
        "language": "Python",
        "framework": "pytest",
        "files": {"pytest.ini": "[pytest]\ntestpaths = tests\n", "tests/__init__.py": ""},
        "command": "pytest",
    },
    "javascript": {
        "language": "JavaScript",
        "framework": "jest",
        "files": {
            "package.json": json.dumps(
                {
                    "name": "greenfield-project",
                    "private": True,
                    "scripts": {"test": "jest"},
                    "devDependencies": {"jest": "^29.7.0"},
                },
                indent=2,
            )
            + "\n",
            "jest.config.js": 'module.exports = { testEnvironment: "node", testMatch: ["**/tests/**/*.test.js"] };\n',
            "tests/.gitkeep": "",
        },
        "command": "npx jest",
    },
}


def is_greenfield(state: dict) -> bool:
    return state.get("run_mode") == "greenfield"


def has_no_code(state: dict) -> bool:
    return is_greenfield(state) and not state.get("repo_url")


class GreenfieldSteps:
    def _scaffold_artifact(self, state: dict) -> dict:
        """Write the empty project skeleton to <run>/repo and describe it as a repo_analysis artifact."""
        plan = SCAFFOLDS[state.get("target_language") or "python"]
        root = self.run_dir / "repo"
        for rel, content in plan["files"].items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        package_files = [f for f in plan["files"] if f in ("pytest.ini", "package.json", "jest.config.js")]
        artifact = RepoAnalysis(
            repo_url="",
            repo_name=state.get("run_name") or "greenfield-project",
            languages={plan["language"]: 0},
            package_files=package_files,
            test_frameworks=[plan["framework"]],
            likely_test_directories=["tests"],
            notes=[
                "Greenfield run without a repository: repository analysis skipped, tests target an empty "
                f"{plan['framework']} project scaffold.",
            ],
        ).model_dump()
        artifact["scaffold"] = {
            "language": plan["language"],
            "framework": plan["framework"],
            "files": sorted(plan["files"]),
            "test_command": plan["command"],
            "note": "Tests run once application code exists; execution is skipped for this run.",
        }
        return artifact

    async def scaffold_repo_analysis(self, state: dict, ctx) -> None:
        artifact = await asyncio.to_thread(self._scaffold_artifact, state)
        state["stack"] = (
            await asyncio.to_thread(detect_stack, self.run_dir / "repo", artifact["languages"])
        ).to_dict()
        state["declared_dependencies"], state["evidence"] = [], ""
        stage = "repo_analysis"
        state["active_stage"] = stage
        state["artifacts"][stage] = artifact
        state["revisions"][stage] = 1
        state.setdefault("approved", {})[stage] = digest(artifact)
        atomic_json(self.run_dir / f"{stage}-1.json", artifact)
        self.audit("greenfield_repo_analysis_skipped", stage=stage, scaffold=artifact["scaffold"])
        self.save()
        await ctx.send_message(state)

    async def skip_without_code(self, stage: str, state: dict, ctx) -> None:
        """Environment setup and test execution have nothing to act on when there is no application code."""
        state["active_stage"] = stage
        if stage == ENV_STAGE:
            state["environment"] = {"status": "skipped", "reason": NO_CODE_NOTE}
            artifact = {"status": "skipped", "notes": [NO_CODE_NOTE]}
            state["artifacts"][stage] = artifact
            state.setdefault("approved", {})[stage] = digest(artifact)
            self.audit("environment_skipped", reason=NO_CODE_NOTE)
            self.save()
            await ctx.send_message(state)
            return
        artifact = self._execution_artifact(state, None)
        artifact["execution"] = {"status": "skipped", "reason": NO_CODE_NOTE}
        qa = artifact["qa_report"]
        qa["execution_note"] = NO_CODE_NOTE
        atomic_json(self.run_dir / "qa_report.json", qa)
        (self.run_dir / "qa_report.md").write_text(
            artifact["markdown"] + f"\n\nExecution: {NO_CODE_NOTE}\n", encoding="utf-8"
        )
        state["artifacts"][stage] = artifact
        state["completed_reason"] = f"Test execution {NO_CODE_NOTE}; generated tests were not run."
        self.audit("execution_skipped", reason=NO_CODE_NOTE)
        await self._finish(state, ctx)

    async def author_specs(self, state: dict, ctx) -> None:
        """Requirements stage in greenfield mode: sections -> specs (model, host-validated) -> requirements -> review."""
        stage = "requirements"
        if self.runtime is None:
            raise ValueError("Configure API key/model before running this stage, then retry.")
        sections = state["spec_intake"]["sections"]
        counters: dict[str, int] = {}
        specs, assumptions, gaps = [], [], []
        for index in range(0, len(sections), SPEC_BATCH):
            batch = sections[index : index + SPEC_BATCH]
            built, draft = await self._author_batch(state, batch, counters, index // SPEC_BATCH)
            specs += built
            assumptions += draft.assumptions
            gaps += draft.gaps
        covered = {
            spec["source"].get("jira_key") or f"{spec['source']['doc']}#{spec['source']['section_anchor']}"
            for spec in specs
        }
        gaps += [
            f"No spec was produced for source section {section_ref(s)}."
            for s in sections
            if section_ref(s) not in covered
        ]
        artifact = specs_to_requirements(specs, state.get("run_name") or "Greenfield project")
        artifact.update(
            specs=specs,
            spec_intake={
                "sources": state["spec_intake"]["sources"],
                "warnings": state["spec_intake"]["warnings"],
                "section_count": len(sections),
            },
            assumptions=assumptions[:12],
            gaps=gaps[:12],
            analysis_mode="greenfield",
            metadata={
                "generator": SPEC_SKILL,
                "repository_url": state.get("repo_url", ""),
                "generated_at": datetime.now(UTC).isoformat(),
            },
        )
        atomic_json(self.run_dir / "specs.json", specs)
        self.audit("greenfield_specs", stage=stage, sections=len(sections), specs=len(specs))
        await self.review(stage, artifact, state, ctx)

    async def _author_batch(self, state: dict, batch: list[dict], counters: dict, number: int):
        stage, repair = "spec_authoring", ""
        for attempt in range(1, MODEL_REPAIR_ATTEMPTS + 1):
            prompt = build_prompt(
                {
                    "assignment": (
                        "Turn the supplied source sections into specs. Cite each spec's section by its ref; never invent "
                        "a source, behavior, limit or value that the section text does not state."
                    ),
                    "output_contract": SpecsDraft.model_json_schema(),
                    "json_rules": {
                        "single_complete_object": True,
                        "markdown_fences": False,
                        "respect_schema_limits": True,
                    },
                    "approved_inputs": {
                        "sections": [
                            {
                                "ref": section_ref(s),
                                "title": s["title"],
                                "text": s["text"],
                                **(
                                    {"acceptance_criteria": s["acceptance_criteria"]}
                                    if s.get("acceptance_criteria")
                                    else {}
                                ),
                            }
                            for s in batch
                        ],
                    },
                    "revision_feedback": state.get("feedback", {}).get("requirements"),
                    "repair_feedback": repair or None,
                }
            )
            try:
                result = await self._call_model(
                    stage=stage,
                    skill_name=SPEC_SKILL,
                    companions=(),
                    persona=persona_instructions(stage),
                    prompt=prompt,
                    state=state,
                    persist=self.save,
                    prompt_batch=number,
                    response_format=SpecsDraft,
                )
                if result["status"] != "completed":
                    raise ValueError(f"Unexpected model status: {result['status']}")
                draft = SpecsDraft.model_validate(result["artifact"])
                local = dict(counters)
                built = build_specs([d.model_dump() for d in draft.specs], batch, local)
            except GatewayUnavailableError:
                self._settle_response_cache(stage, False)
                raise  # an outage is not a bad answer: no repair attempts
            except (ValidationError, AgentOutputError, ValueError) as exc:
                self._settle_response_cache(stage, False)
                self.audit("stage_repair", stage=stage, attempt=attempt, error=str(exc)[:600])
                if attempt == MODEL_REPAIR_ATTEMPTS:
                    raise
                repair = f"Attempt {attempt} was rejected by host validation: {str(exc)[:4000]}. Return a corrected complete JSON object."
                continue
            counters.update(local)
            self._settle_response_cache(stage, True)
            return built, draft

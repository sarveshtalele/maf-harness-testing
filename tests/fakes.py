"""Shared scripted stage runtime for workflow tests (replaces only the model call)."""

import json
import sys
from pathlib import Path


def clone_fixture(url, destination):
    destination.mkdir()
    (destination / "service.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")


FULL_MARKS = {
    "source_and_api_match": 25,
    "assertion_checks_real_behavior": 30,
    "fixture_validity": 20,
    "isolation_and_determinism": 15,
    "traceability": 10,
}


def review_response(kwargs, source="service.py", scores=None):
    from maf_test_harness.models import TestReviewDraft

    if kwargs.get("response_format") is not TestReviewDraft:
        return None
    cases = json.loads(kwargs["prompt"])["approved_inputs"]["approved_cases"]
    return {
        "status": "completed",
        "artifact": {
            "reviews": [
                {
                    "case_id": c["id"],
                    "scores": dict(scores or FULL_MARKS),
                    "confidence": 90,
                    "verdict": "supported",
                    "reason": "Scripted reviewer transport: source and assertions match the fixture.",
                    "source_files": [source],
                }
                for c in cases
            ]
        },
    }


def design_review_response(kwargs, confidence=90, verdict="approved", reason="Scripted design reviewer."):
    from maf_test_harness.models import DesignReviewDraft

    if kwargs.get("response_format") is not DesignReviewDraft:
        return None
    cases = json.loads(kwargs["prompt"])["cases_under_review"]
    return {
        "status": "completed",
        "artifact": {
            "reviews": [
                {"case_id": c["id"], "verdict": verdict, "confidence": confidence, "reason": reason}
                for c in cases
            ]
        },
    }


class ScriptedStageRuntime:
    """Returns schema-valid stage artifacts for the tiny `service.add` repository."""

    def __init__(self, test_body="assert add(1, 2) == 3", extra_cases=()):
        self.calls = []
        self.test_body = test_body
        self.extra_cases = list(extra_cases)
        self.tool_agent_calls = []
        self.tool_agent_note = {
            "summary": "pip could not build a dependency",
            "likely_cause": "no C compiler",
            "category": "environment",
            "suggested_fixes": ["install build tools"],
            "needs_human": True,
        }

    async def run_tool_agent(self, *, stage, persona, prompt, tools, state, persist, **kwargs):
        self.tool_agent_calls.append({"stage": stage, "tools": [t.name for t in tools], "prompt": prompt})
        if isinstance(self.tool_agent_note, Exception):
            raise self.tool_agent_note
        return self.tool_agent_note

    async def run_stage(self, *, stage, state, **kwargs):
        if review := review_response(kwargs):
            return review
        if review := design_review_response(kwargs):
            return review
        self.calls.append(stage)
        if stage == "requirements":
            return {
                "status": "completed",
                "artifact": {
                    "app_name": "demo",
                    "summary": "Simple addition service",
                    "actors": [
                        {"name": "Developer", "description": "Calls add()", "source_files": ["service.py"]}
                    ],
                    "requirements": [
                        {
                            "text": "add returns the sum of two inputs",
                            "evidence": "service.py defines add(a, b) as a + b",
                            "source_files": ["service.py"],
                            "category": "functional",
                            "actor": "Developer",
                        }
                    ],
                    "acceptance_criteria": [],
                    "validation_rules": [],
                    "api_contracts": [],
                    "environment_needs": [],
                    "exclusions": [],
                    "assumptions": [],
                    "gaps": [],
                },
            }
        if stage == "test_planning":
            return {
                "status": "completed",
                "artifact": {
                    "objective": "Verify addition",
                    "recommended_frameworks": ["pytest"],
                    "setup_actions": [],
                    "areas": [
                        {
                            "name": "addition",
                            "objective": "verify sum",
                            "level": "unit",
                            "risk": "medium",
                            "evidence_files": ["service.py"],
                            "scenarios": ["add two integers"],
                        }
                    ],
                    "execution_order": ["addition unit tests"],
                    "assumptions": [],
                    "gaps": [],
                },
            }
        if stage == "test_design":
            req = state["artifacts"]["requirements"]["requirement_model"]["requirements"][0]["id"]
            case = {
                "title": "add returns sum",
                "priority": "P1",
                "level": "unit",
                "actor": "Developer",
                "target": "service.add",
                "description": "Two integers are added.",
                "preconditions": [],
                "data_notes": "1 and 2",
                "expected_result": "add(1, 2) returns 3",
                "steps": ["Call add(1, 2)", "Compare with 3"],
                "traceability_links": [{"type": "requirement", "id": req}],
            }
            negative = [{**c, "traceability_links": case["traceability_links"]} for c in self.extra_cases]
            return {
                "status": "completed",
                "artifact": {
                    "positive_tests": [case],
                    "negative_tests": negative,
                    "boundary_tests": [],
                    "validation_tests": [],
                    "contract_tests": [],
                    "gaps": [],
                },
            }
        prompt = json.loads(kwargs["prompt"])
        files = []
        for case in prompt["approved_inputs"]["approved_cases"]:
            slug = "".join(ch if ch.isalnum() else "_" for ch in case["title"]).strip("_").lower()
            if case["title"] == "add returns sum":
                body = self.test_body
            elif case["title"].startswith("add returns sum"):
                body = "assert add(1, 2) == 3"  # extra case from the browser fixture: always passes
            else:
                body = "assert add(2, 2) == 5"
            files.append(
                {
                    "path": f"tests/test_{slug}.py",
                    "case_ids": [case["id"]],
                    "lines": ["from service import add", "", "", f"def test_{slug}():", f"    {body}"],
                }
            )
        return {
            "status": "completed",
            "artifact": {
                "selected_language": "Python",
                "selected_framework": "pytest",
                "rationale": "Python source.",
                "files": files,
            },
        }


def local_env_plan(stack, root, cache_dir, timeout=900, needs=None, **kwargs):
    """Environment plan whose only step is a harmless local command (unit tests have no network)."""
    return {
        "ecosystem": "python",
        "isolation": "test double",
        "prerequisites": [],
        "missing_prerequisites": [],
        "notes": [],
        "steps": [
            {
                "name": "noop-install",
                "kind": "setup",
                "argv": [sys.executable, "-c", "print('ok')"],
                "env": {},
                "cwd": ".",
                "timeout": 60,
                "continue_on_failure": False,
            }
        ],
    }


def children_import_this_checkout(monkeypatch) -> None:
    """Skill-script children get only BASIC_ENV, so without this they import whatever maf_test_harness is installed."""
    from maf_test_harness.skills import scripts

    src = str(Path(__file__).resolve().parents[1] / "src")
    real_run_command = scripts.run_command

    def run(argv, cwd, output_dir, **kwargs):
        kwargs["env"] = {**(kwargs.get("env") or {}), "PYTHONPATH": src}
        return real_run_command(argv, cwd, output_dir, **kwargs)

    monkeypatch.setattr(scripts, "run_command", run)

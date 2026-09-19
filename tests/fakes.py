"""Shared scripted stage runtime for workflow tests (replaces only the model call)."""

import json
import sys


def clone_fixture(url, destination):
    destination.mkdir()
    (destination / "service.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")


class ScriptedStageRuntime:
    """Returns schema-valid stage artifacts for the tiny `service.add` repository."""

    def __init__(self, test_body="assert add(1, 2) == 3", extra_cases=()):
        self.calls = []
        self.test_body = test_body
        self.extra_cases = list(extra_cases)

    async def run_stage(self, *, stage, state, **kwargs):
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
            body = self.test_body if case["title"] == "add returns sum" else "assert add(2, 2) == 5"
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


def local_env_plan(stack, root, cache_dir, timeout=900):
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

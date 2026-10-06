import asyncio
import json
from pathlib import Path

import pytest
from fakes import review_response

from maf_test_harness.workflow import Harness
from maf_test_harness.workflow_steps import generation as generation_steps


def test_failed_generation_preserves_all_attempt_artifacts(tmp_path):
    class ImportsOnly:
        async def run_stage(self, **kwargs):
            if review := review_response(kwargs):
                return review
            return {
                "status": "completed",
                "artifact": {
                    "selected_language": "Python",
                    "selected_framework": "pytest",
                    "rationale": "native",
                    "files": [{"path": "test_cli.py", "lines": ["import sys"], "case_ids": ["TC-1"]}],
                },
            }

    host = Harness(tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=ImportsOnly())
    state = {
        "artifacts": {
            "repo_analysis": {"languages": {"Python": 1}},
            "test_design": {"positive_tests": [{"id": "TC-1"}]},
        }
    }
    with pytest.raises(ValueError, match="no recognized concrete test"):
        asyncio.run(host._generate_tests_batched(state))
    attempts = list((tmp_path / "generation_attempts").glob("*.json"))
    assert len(attempts) == 3
    assert {json.loads(p.read_text())["attempt"] for p in attempts} == {1, 2, 3}
    for p in attempts:
        assert json.loads(p.read_text())["result"]["artifact"]["files"][0]["lines"] == ["import sys"]


def test_retry_reuses_accepted_batches_only_with_matching_inputs(tmp_path, monkeypatch):

    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 1)
    monkeypatch.setattr(generation_steps, "MAX_FAILED_BATCH_RATIO", 0.0)  # any failed batch fails the stage

    class Provider:
        fail_second = True
        calls = []

        async def run_stage(self, **kwargs):
            if review := review_response(kwargs):
                return review
            case = json.loads(kwargs["prompt"])["approved_inputs"]["approved_cases"][0]["id"]
            self.calls.append(case)
            code = (
                ["import sys"]
                if case == "TC-2" and self.fail_second
                else ["def value():", "    return 2", "", "def test_value():", "    assert value() == 2"]
            )
            return {
                "status": "completed",
                "artifact": {
                    "selected_language": "Python",
                    "selected_framework": "pytest",
                    "rationale": "native",
                    "files": [{"path": f"test_{case}.py", "lines": code, "case_ids": [case]}],
                },
            }

    (tmp_path / "repo").mkdir(exist_ok=True)
    (tmp_path / "repo" / "service.py").write_text("def value(): return 2")
    provider = Provider()
    host = Harness(tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=provider)
    state = {
        "artifacts": {
            "repo_analysis": {"languages": {"Python": 1}},
            "test_design": {"positive_tests": [{"id": "TC-1"}, {"id": "TC-2"}]},
        }
    }
    with pytest.raises(ValueError):
        asyncio.run(host._generate_tests_batched(state))
    provider.fail_second = False
    result = asyncio.run(host._generate_tests_batched(state))
    assert provider.calls.count("TC-1") == 1
    assert result["validation"]["uncovered_case_ids"] == []


def test_generated_paths_that_exist_in_the_repository_are_repaired_not_overwritten(tmp_path):
    (tmp_path / "repo" / "tests").mkdir(parents=True)
    (tmp_path / "repo" / "tests" / "test_value.py").write_text("existing test")

    class Provider:
        calls = []

        async def run_stage(self, **kwargs):
            if review := review_response(kwargs):
                return review
            self.calls.append(kwargs["prompt"])
            path = "tests/test_value.py" if len(self.calls) == 1 else "tests/test_value_new.py"
            return {
                "status": "completed",
                "artifact": {
                    "selected_language": "Python",
                    "selected_framework": "pytest",
                    "rationale": "native",
                    "files": [
                        {
                            "path": path,
                            "lines": [
                                "def value():",
                                "    return 2",
                                "",
                                "def test_value():",
                                "    assert value() == 2",
                            ],
                            "case_ids": ["TC-1"],
                        }
                    ],
                },
            }

    (tmp_path / "repo").mkdir(exist_ok=True)
    (tmp_path / "repo" / "service.py").write_text("def value(): return 2")
    provider = Provider()
    host = Harness(tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=provider)
    state = {
        "artifacts": {
            "repo_analysis": {"languages": {"Python": 1}},
            "test_design": {"positive_tests": [{"id": "TC-1"}]},
        }
    }
    result = asyncio.run(host._generate_tests_batched(state))
    assert [f["path"] for f in result["files"]] == ["tests/test_value_new.py"]
    assert "already exist in the repository" in provider.calls[1]
    assert (tmp_path / "repo" / "tests" / "test_value.py").read_text() == "existing test"

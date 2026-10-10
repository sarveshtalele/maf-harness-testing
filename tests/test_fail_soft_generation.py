import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fakes import review_response

from maf_test_harness.qa.report import build_qa_report
from maf_test_harness.workflow import Harness
from maf_test_harness.workflow_steps import generation as generation_steps

GOOD = ["def value():", "    return 2", "", "def test_value():", "    assert value() == 2"]


class Provider:
    def __init__(self, bad):
        self.bad = set(bad)

    async def run_stage(self, **kwargs):
        if review := review_response(kwargs):
            return review
        case = json.loads(kwargs["prompt"])["approved_inputs"]["approved_cases"][0]["id"]
        return {
            "status": "completed",
            "artifact": {
                "selected_language": "Python",
                "selected_framework": "pytest",
                "rationale": "native",
                "files": [
                    {
                        "path": f"test_{case}.py",
                        "lines": ["import sys"] if case in self.bad else GOOD,
                        "case_ids": [case],
                    }
                ],
            },
        }


def run(tmp_path, monkeypatch, ids, bad):
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 1)
    (tmp_path / "repo").mkdir(exist_ok=True)
    (tmp_path / "repo" / "service.py").write_text("def value(): return 2", encoding="utf-8")
    host = Harness(tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=Provider(bad))
    state = {
        "artifacts": {
            "repo_analysis": {"languages": {"Python": 1}},
            "test_design": {"positive_tests": [{"id": i} for i in ids]},
        }
    }
    return asyncio.run(host._generate_tests_batched(state))


def test_one_failed_batch_is_recorded_and_the_others_continue(tmp_path, monkeypatch):
    result = run(tmp_path, monkeypatch, ["TC-1", "TC-2", "TC-3"], bad={"TC-2"})
    assert [f["case_ids"] for f in result["files"]] == [["TC-1"], ["TC-3"]]
    assert result["generation_failures"][0]["case_ids"] == ["TC-2"]
    assert result["validation"]["generation_failed_case_ids"] == ["TC-2"]
    assert result["validation"]["uncovered_case_ids"] == []
    catalog = {c["id"]: c for c in result["case_catalog"]}
    assert catalog["TC-2"]["reviewer_verdict"] == "generation_failed"
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "test_generation_batch_failed" in audit


def test_too_many_failed_batches_fail_the_stage(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="failed for 2/3 batches"):
        run(tmp_path, monkeypatch, ["TC-1", "TC-2", "TC-3"], bad={"TC-2", "TC-3"})


def test_failed_cases_are_reported_and_block_a_pass_verdict():
    design = {"positive_tests": [{"id": "TC-1", "title": "a"}, {"id": "TC-2", "title": "b"}]}
    generation = {
        "files": [
            {"path": "test_a.py", "case_ids": ["TC-1"], "content": "def test_value():\n    assert True\n"}
        ],
        "generation_failures": [{"batch": 2, "case_ids": ["TC-2"], "attempts": 3, "last_error": "x"}],
    }
    execution = {"status": "passed", "cases": [{"name": "test_a.py::test_value", "status": "passed"}]}
    report = build_qa_report(
        repo={}, requirements={}, design=design, generation=generation, execution=execution
    )
    status = {c["id"]: c["status"] for c in report["cases"]}
    assert status["TC-2"] == "generation_failed"
    assert report["verdict"] != "PASS"


def stage_models(value):
    code = "from maf_test_harness import settings; print(settings.model_for('requirements'), settings.model_for('test_design'))"
    env = {**os.environ, "HARNESS_STAGE_MODELS": value, "OPENAI_MODEL": "base-model"}
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=Path.cwd()
    )


def test_stage_models_override_only_listed_stages():
    proc = stage_models('{"requirements": "small-model"}')
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["small-model", "base-model"]


@pytest.mark.parametrize("value", ["not json", '["a"]'])
def test_invalid_stage_models_are_rejected(value):
    proc = stage_models(value)
    assert proc.returncode != 0 and "HARNESS_STAGE_MODELS" in proc.stderr


def test_each_stage_keeps_a_small_unique_tool_set():
    """MAF guidance: tool selection degrades as the tool count grows; keep each stage's set small and unique."""
    from maf_test_harness.tools.agent_tools import STAGE_TOOLS

    for stage, tools in STAGE_TOOLS.items():
        assert len(tools) == len(set(tools)), stage
        assert len(tools) <= 20, stage


class DroppingProvider:
    """Real run 1a9bbae78ff0 batch 39: the reviewer flagged one case; the repair answer returned only that one."""

    def __init__(self):
        self.generations = 0
        self.reviews = 0

    async def run_stage(self, **kwargs):
        if review := review_response(kwargs):
            self.reviews += 1
            if self.reviews == 1:
                review["artifact"]["reviews"][0].update(verdict="needs_revision", confidence=40)
            return review
        asked = [c["id"] for c in json.loads(kwargs["prompt"])["approved_inputs"]["approved_cases"]]
        self.generations += 1
        ids = asked if self.generations == 1 else asked[:1]
        files = [{"path": f"test_{c}.py", "lines": GOOD, "case_ids": [c]} for c in ids]
        return {
            "status": "completed",
            "artifact": {
                "selected_language": "Python",
                "selected_framework": "pytest",
                "rationale": "native",
                "files": files,
            },
        }


def test_repair_that_drops_sibling_cases_keeps_their_earlier_files(tmp_path, monkeypatch):
    monkeypatch.setattr(generation_steps, "TEST_GENERATION_BATCH_SIZE", 3)
    (tmp_path / "repo").mkdir(exist_ok=True)
    (tmp_path / "repo" / "service.py").write_text("def value(): return 2", encoding="utf-8")
    provider = DroppingProvider()
    host = Harness(tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=provider)
    state = {
        "artifacts": {
            "repo_analysis": {"languages": {"Python": 1}},
            "test_design": {"positive_tests": [{"id": i} for i in ("TC-1", "TC-2", "TC-3")]},
        }
    }
    result = asyncio.run(host._generate_tests_batched(state))
    assert provider.generations == 2 and not result["generation_failures"]
    assert sorted(i for f in result["files"] for i in f["case_ids"]) == ["TC-1", "TC-2", "TC-3"]
    assert "generation_case_carried_over" in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")

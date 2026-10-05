"""check_skill_output: stage agents may run the reviewed validators of their own skills, and nothing else."""

import json
import shutil
from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
from test_execution_stage import local_pytest_plan

import maf_test_harness.workflow as wf
from maf_test_harness.tools.registry import ToolContext
from maf_test_harness.tools.typed import TYPED_STAGE_TOOLS, TYPED_TOOL_NAMES, default_registry
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow_steps import execution as execution_steps

SKILLS = Path("skills")


def registry(skills_root=SKILLS, allowed=("test-data-generator", "traceability-generator")):
    return default_registry(ToolContext(skills_root=skills_root, allowed_skills=allowed))


def test_a_reviewed_model_tier_script_runs_and_reports_its_output():
    result = registry().call(
        "check_skill_output",
        {
            "skill": "test-data-generator",
            "script": "scripts/deterministic_helpers.py",
            "args": ["--seed", "7", "--count", "2"],
        },
    )
    assert result.ok and result.code == "OK"
    assert result.data["exit_code"] == 0 and len(result.data["sha256"]) == 64 and result.evidence


def test_a_host_tier_validator_runs_on_the_agents_draft_and_its_verdict_comes_back():
    result = registry().call(
        "check_skill_output",
        {
            "skill": "traceability-generator",
            "script": "scripts/validate_traceability.py",
            "args": ["input/traceability.json", "--source", "input/source.json"],
            "files": {"traceability.json": "{}", "source.json": "{}"},
        },
    )
    assert not result.ok and result.code == "SKILL-CHECK-FAILED" and result.evidence
    assert result.data["exit_code"] != 0 and result.suggested_fixes


def test_only_skills_assigned_to_the_stage_may_be_used():
    result = registry(allowed=("test-data-generator",)).call(
        "check_skill_output",
        {"skill": "traceability-generator", "script": "scripts/validate_traceability.py"},
    )
    assert result.code == "SKILL-SCOPE" and "test-data-generator" in result.suggested_fixes[0]
    assert (
        registry(allowed=()).call("check_skill_output", {"skill": "x", "script": "y"}).code == "SKILL-SCOPE"
    )


@pytest.mark.parametrize(
    "script",
    ["scripts/not_reviewed.py", "../traceability-generator/scripts/validate_traceability.py", "SKILL.md"],
)
def test_scripts_that_are_not_on_the_reviewed_allow_list_are_refused(script):
    result = registry().call("check_skill_output", {"skill": "test-data-generator", "script": script})
    assert result.code == "SCRIPT-REFUSED"


def test_a_modified_script_is_refused_even_though_it_is_listed(tmp_path):
    copy = tmp_path / "skills"
    shutil.copytree(SKILLS, copy)
    script = copy / "test-data-generator" / "scripts" / "deterministic_helpers.py"
    script.write_bytes(script.read_bytes() + b"\n# tampered\n")
    result = registry(copy).call(
        "check_skill_output", {"skill": "test-data-generator", "script": "scripts/deterministic_helpers.py"}
    )
    assert result.code == "SCRIPT-REFUSED" and "changed since it was reviewed" in result.evidence


def test_argument_paths_outside_the_working_directory_are_refused():
    result = registry().call(
        "check_skill_output",
        {
            "skill": "test-data-generator",
            "script": "scripts/deterministic_helpers.py",
            "args": ["../../etc/passwd"],
        },
    )
    assert result.code == "SCRIPT-REFUSED"


def test_no_skills_directory_configured():
    result = default_registry(ToolContext()).call("check_skill_output", {"skill": "a", "script": "b"})
    assert result.code == "NO-SKILLS"


def test_the_tool_is_registered_for_the_design_stage_only():
    assert TYPED_STAGE_TOOLS["test_design"] == ("check_skill_output",)
    assert "check_skill_output" in TYPED_TOOL_NAMES
    assert all(
        "check_skill_output" not in names
        for stage, names in TYPED_STAGE_TOOLS.items()
        if stage != "test_design"
    )


# --- the opt-in switch -------------------------------------------------------------------------------------------


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    monkeypatch.setattr(execution_steps, "build_plan", local_pytest_plan)
    monkeypatch.setattr(execution_steps, "build_environment_plan", local_env_plan)
    service = RuntimeService()
    harness = wf.Harness(
        tmp_path / "run",
        skills_root=SKILLS,
        requirements_mode="model",
        runtime=ScriptedStageRuntime(),
        cache_dir=tmp_path / "cache",
    )
    service.submit(harness.start("https://github.com/acme/demo")).result(15)
    yield harness
    service.close()


def names(tools):
    return [t.name for t in tools]


def test_evaluators_are_not_offered_by_default(host):
    assert "check_skill_output" not in names(host._stage_tools("test_design"))


def test_evaluators_are_offered_when_enabled_and_scoped_to_the_stage_skills(host, monkeypatch):
    monkeypatch.setattr(wf, "SKILL_EVALUATORS", True)
    host.state = {"stack": {}, "artifacts": {}}
    tools = host._stage_tools("test_design")
    assert "check_skill_output" in names(tools) and "find_symbol" in names(
        tools
    )  # central tools are still there
    assert set(host.typed.context.allowed_skills) == set(wf.skills_for("test_design", host.state).all)
    assert "check_skill_output" not in names(host._stage_tools("test_generation"))


def test_the_offered_tool_runs_through_a_real_maf_function_tool(host, monkeypatch):
    import asyncio

    monkeypatch.setattr(wf, "SKILL_EVALUATORS", True)
    host.state = {"stack": {}, "artifacts": {}}
    tool = next(t for t in host._stage_tools("test_design") if t.name == "check_skill_output")
    skill = sorted(host.typed.context.allowed_skills)[0]
    content = asyncio.run(tool.invoke(arguments={"skill": skill, "script": "scripts/nothing.py"}))
    assert json.loads(content[0].text)["code"] == "SCRIPT-REFUSED"

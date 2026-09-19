import json
from pathlib import Path

from maf_test_harness import cli
from maf_test_harness.skills_registry import tree_sha256

SKILLS = Path("skills")


def write_run(tmp_path, loaded=True, mode_ok=True, stale_hash=False):
    state = {"runtime_provenance": {"factory": "agent_framework.create_harness_agent", "agent_framework_core": "1",
                                    "agent_framework_openai": "1", "model": "m", "skill_mode": "progressive"},
             "skill_provenance": {}, "skill_tool_trace": {}, "invocations": [{"status": "returned"}]}
    for stage, skill in cli.SKILL_NAMES.items():
        state["skill_provenance"][stage] = {"uses_maf_skills_provider": mode_ok,
                                            "package_sha256": "0" * 64 if stale_hash else tree_sha256(SKILLS / skill)}
        state["skill_tool_trace"][stage] = {"skill_loaded_via_maf": loaded}
    (tmp_path / "run.json").write_text(json.dumps({"state": state}))
    return tmp_path


def test_verify_run_passes_only_with_full_maf_and_skill_evidence(tmp_path, capsys):
    assert cli.verify_run(write_run(tmp_path), SKILLS) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_verify_run_fails_when_a_skill_was_not_loaded_via_maf(tmp_path, capsys):
    assert cli.verify_run(write_run(tmp_path, loaded=False), SKILLS) == 1
    assert "FAIL  requirements: load_skill" in capsys.readouterr().out


def test_verify_run_flags_preloaded_mode_and_changed_skill_packages(tmp_path, capsys):
    assert cli.verify_run(write_run(tmp_path, mode_ok=False), SKILLS) == 1
    assert cli.verify_run(write_run(tmp_path, stale_hash=True), SKILLS) == 1
    assert "package unchanged" in capsys.readouterr().out


def test_lint_skills_command_exit_code(capsys):
    assert cli.main(["lint-skills", str(SKILLS)]) == 0
    assert "OK   generic-test-generator" in capsys.readouterr().out

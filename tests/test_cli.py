import json
from pathlib import Path

from maf_test_harness import cli
from maf_test_harness.skills_registry import tree_sha256

SKILLS = Path("skills")


def write_run(tmp_path, loaded=True, mode_ok=True, stale_hash=False):
    state = {
        "runtime_provenance": {
            "factory": "agent_framework.create_harness_agent",
            "agent_framework_core": "1",
            "agent_framework_openai": "1",
            "model": "m",
            "skill_mode": "progressive",
        },
        "skill_provenance": {},
        "skill_tool_trace": {},
        "invocations": [{"status": "returned"}],
    }
    companion = "test-data-generator"
    for stage, skill in cli.SKILL_NAMES.items():
        names = [skill, companion] if stage == "test_design" else [skill]
        entries = [
            {"name": n, "package_sha256": "0" * 64 if stale_hash else tree_sha256(SKILLS / n)} for n in names
        ]
        state["skill_provenance"][stage] = {
            **entries[0],
            "companions": entries[1:],
            "uses_maf_skills_provider": mode_ok,
        }
        state["skill_tool_trace"][stage] = {
            "skill_loaded_via_maf": loaded,
            "loaded_skills": names if loaded else [],
        }
    (tmp_path / "run.json").write_text(json.dumps({"state": state}))
    return tmp_path


def test_verify_run_passes_only_with_full_maf_and_skill_evidence(tmp_path, capsys):
    assert cli.verify_run(write_run(tmp_path), SKILLS) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_verify_run_fails_when_a_skill_was_not_loaded_via_maf(tmp_path, capsys):
    assert cli.verify_run(write_run(tmp_path, loaded=False), SKILLS) == 1
    assert "FAIL  requirements: load_skill executed" in capsys.readouterr().out


def test_verify_run_flags_preloaded_mode_and_changed_skill_packages(tmp_path, capsys):
    assert cli.verify_run(write_run(tmp_path, mode_ok=False), SKILLS) == 1
    assert cli.verify_run(write_run(tmp_path, stale_hash=True), SKILLS) == 1
    assert "packages unchanged" in capsys.readouterr().out


def test_lint_skills_command_exit_code(capsys):
    assert cli.main(["lint-skills", str(SKILLS)]) == 0
    out = capsys.readouterr().out
    assert (
        "OK   generic-test-generator" in out and "OK   SKILLS.lock.json" in out and "6 vendored skills" in out
    )


def test_verify_run_checks_companion_skills_and_the_upstream_lock(tmp_path, capsys):
    run = write_run(tmp_path)
    state = json.loads((run / "run.json").read_text())
    state["state"]["skill_tool_trace"]["test_design"]["loaded_skills"] = [
        "test-design-generator"
    ]  # companion missing
    (run / "run.json").write_text(json.dumps(state))
    assert cli.verify_run(run, SKILLS) == 1
    out = capsys.readouterr().out
    assert "FAIL  test_design: load_skill executed" in out and "PASS  vendored upstream skills match" in out

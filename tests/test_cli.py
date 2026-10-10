import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from maf_test_harness import cli
from maf_test_harness.skills.registry import tree_sha256

SKILLS = Path("skills")


@pytest.mark.parametrize("override", [None, "custom runs"])
def test_cli_creates_default_run_in_checkout_or_explicit_storage(tmp_path, monkeypatch, override):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.delenv("HARNESS_RUNS_DIR", raising=False)
    if override:
        monkeypatch.setenv("HARNESS_RUNS_DIR", override)
    monkeypatch.setattr(cli, "validate_gateway_settings", lambda: None)

    class Runtime:
        async def close(self):
            pass

    paths = []

    def host(path, **kwargs):
        paths.append(path)
        path.mkdir(parents=True)
        return SimpleNamespace(view={"status": "stopped"}, state={})

    monkeypatch.setattr(cli, "MAFHarnessRuntime", lambda **kwargs: Runtime())
    monkeypatch.setattr(cli, "Harness", host)
    monkeypatch.setattr(
        cli, "DailyUsageLedger", lambda: SimpleNamespace(limit=100, snapshot=lambda: {"used": 0})
    )
    assert cli.main(["run", "https://github.com/acme/demo"]) == 2
    expected = tmp_path / override if override else tmp_path / ".maf-harness" / "runs"
    assert paths[0].parent == expected and paths[0].is_dir()


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
        "OK   generic-test-generator" in out
        and "OK   SKILLS.lock.json" in out
        and "13 locked packages" in out
    )


def test_lint_skills_strict_fails_for_unlocked_repo_skill(tmp_path, capsys):
    copy = tmp_path / "skills"
    shutil.copytree(SKILLS, copy)
    assert cli.main(["lint-skills", "--strict", str(copy)]) == 0
    shutil.copytree(copy / "test-planning", copy / "extra-skill")
    (copy / "extra-skill" / "plugin.yaml").write_text(
        (copy / "extra-skill" / "plugin.yaml").read_text().replace("test-planning", "extra-skill")
    )
    (copy / "extra-skill" / "SKILL.md").write_text(
        (copy / "extra-skill" / "SKILL.md").read_text().replace("name: test-planning", "name: extra-skill")
    )
    assert cli.main(["lint-skills", str(copy)]) in (0, 1)  # non-strict ignores lock completeness
    assert cli.main(["lint-skills", "--strict", str(copy)]) == 1
    assert "extra-skill: no entry in SKILLS.lock.json" in capsys.readouterr().out


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

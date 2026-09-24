"""Phase 1 layout: modules live in subpackages, the flat import paths keep working as aliases."""

import importlib
import pkgutil
import sys

import pytest

import maf_test_harness

MOVED = {
    "platform_compat": "envs.platform_compat",
    "env_check": "envs.env_check",
    "environment": "envs.environment",
    "runners": "envs.runners",
    "execution": "envs.execution",
    "app_launcher": "envs.app_launcher",
    "skills_config": "skills.config",
    "skills_registry": "skills.registry",
    "skill_contracts": "skills.contracts",
    "skill_scripts": "skills.scripts",
    "agent_tools": "tools.agent_tools",
    "code_intel": "tools.code_intel",
    "autofix": "tools.autofix",
    "build_gate": "tools.build_gate",
    "maf_runtime": "agent.runtime",
    "personas": "agent.personas",
    "usage": "llm.usage",
    "qa_report": "qa.report",
    "triage": "qa.triage",
    "observability": "monitoring.report",
    "trace": "monitoring.trace",
    "telemetry": "monitoring.telemetry",
    "repo_analyzer": "repo.analyzer",
    "stack": "repo.stack",
    "routes": "repo.routes",
    "artifact_presenter": "ui.artifact_presenter",
    "runtime_service": "ui.runtime_service",
}


@pytest.mark.parametrize(("old", "new"), sorted(MOVED.items()))
def test_old_import_path_is_the_new_module_object(old, new):
    legacy = importlib.import_module(f"maf_test_harness.{old}")
    current = importlib.import_module(f"maf_test_harness.{new}")
    assert legacy is current
    assert legacy.__name__ == f"maf_test_harness.{new}"


def test_from_package_import_resolves_the_moved_module():
    from maf_test_harness import runners
    from maf_test_harness.envs import runners as current

    assert runners is current


def test_patching_through_the_old_name_reaches_the_real_module(monkeypatch):
    from maf_test_harness import runners
    from maf_test_harness.envs import runners as current

    monkeypatch.setattr(runners, "VENV_DIR", "patched-venv")
    assert current.VENV_DIR == "patched-venv"


def test_every_module_in_the_package_imports():
    names = [m.name for m in pkgutil.walk_packages(maf_test_harness.__path__, "maf_test_harness.")]
    assert len(names) > 60
    for name in names:
        importlib.import_module(name)
        assert name in sys.modules


def test_skills_root_still_points_at_the_repository_skills_directory():
    from maf_test_harness.monitoring import report

    assert (report.DEFAULT_SKILLS_ROOT / "SKILLS.lock.json").is_file()

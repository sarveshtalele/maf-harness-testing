"""Monorepo layouts found by the mcp-server-market-v2 E2E run: a nested Python project and a frontend without a runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from maf_test_harness.envs import runners
from maf_test_harness.repo.stack import StackProfile
from maf_test_harness.skills.contracts import check_python_imports, python_import_roots

REPO = [
    "backend/pyproject.toml",
    "backend/core/seed.py",
    "backend/modules/observability/models.py",
    "backend/tests/conftest.py",
    "frontend/package.json",
]


def test_nested_python_project_is_an_import_root_and_its_bare_modules_validate():
    assert python_import_roots(REPO) == ["backend"]
    check_python_imports(
        "from core.seed import seed\nfrom modules.observability import models\n", REPO, "", "t.py"
    )
    with pytest.raises(ValueError):
        check_python_imports("from nowhere.at_all import x\n", REPO, "", "t.py")
    # Without a manifest backend/ is just a package directory, not a root.
    assert python_import_roots([f for f in REPO if not f.endswith("pyproject.toml")]) == []


def _write(root: Path, rel: str, text: str = "") -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def test_pytest_runs_with_the_nested_projects_config_rootdir_and_pythonpath(tmp_path):
    _write(tmp_path, "backend/pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    _write(tmp_path, "backend/core/seed.py")
    generated = [{"path": "backend/tests/test_seed.py", "content": "def test_x():\n    pass\n"}]
    plan = runners.build_plan(StackProfile(primary="python"), tmp_path, generated, cache_dir=tmp_path / "c")
    step = plan.steps[-1]
    assert step.argv[step.argv.index("-c") + 1] == "backend/pyproject.toml"
    assert step.argv[step.argv.index("--rootdir") + 1] == "backend"
    assert str(tmp_path / "backend") in step.env["PYTHONPATH"].split(__import__("os").pathsep)


def test_node_component_without_a_declared_runner_explains_instead_of_running_npx(tmp_path):
    _write(tmp_path, "package.json", '{"devDependencies": {"typescript": "^5"}}')
    generated = [{"path": "tests/a.test.ts", "content": "test('x', () => {})"}]
    plan = runners.build_plan(StackProfile(primary="node"), tmp_path, generated, cache_dir=tmp_path / "c")
    assert not plan.steps and "No JavaScript test runner is declared" in plan.notes[0]
    _write(tmp_path, "package.json", '{"devDependencies": {"jest": "^29"}}')
    plan = runners.build_plan(
        StackProfile(primary="node", test_runner="jest"), tmp_path, generated, cache_dir=tmp_path / "c"
    )
    assert plan.steps[-1].name == "jest"

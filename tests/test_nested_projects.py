"""Monorepo layouts found by the mcp-server-market-v2 E2E run: a nested Python project and a frontend without a runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from maf_test_harness.envs import runners
from maf_test_harness.repo.stack import StackProfile
from maf_test_harness.skills.contracts import check_python_imports, python_import_roots, self_rooted_dirs

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


SELF_ROOTED = [
    "backend/__init__.py",
    "backend/main.py",
    "backend/database/__init__.py",
    "backend/admin/__init__.py",
    "backend/admin/dashboard.py",
]


def _self_rooted_repo(tmp_path: Path, dashboard: str = "from database import get_db\n") -> list[str]:
    _write(tmp_path, "backend/__init__.py")
    _write(tmp_path, "backend/main.py", "import sys\nsys.path.insert(0, 'backend')\n")
    _write(tmp_path, "backend/database/__init__.py", "def get_db():\n    return None\n")
    _write(tmp_path, "backend/admin/__init__.py")
    _write(tmp_path, "backend/admin/dashboard.py", dashboard)
    return SELF_ROOTED


def test_self_rooted_package_imports_sibling_by_bare_name(tmp_path):
    files = _self_rooted_repo(tmp_path)
    assert self_rooted_dirs(tmp_path, files) == ["backend"]
    assert "backend" in python_import_roots(files, root=tmp_path)
    assert "backend" not in python_import_roots(files)
    check_python_imports("from database import get_db\n", files, "", "t.py", root=tmp_path)
    with pytest.raises(ValueError):
        check_python_imports("from database import get_db\n", files, "", "t.py")


def test_full_dotted_self_import_and_root_importable_names_are_not_self_rooted(tmp_path):
    files = _self_rooted_repo(tmp_path, dashboard="from backend.database import get_db\n")
    assert self_rooted_dirs(tmp_path, files) == []
    files = _self_rooted_repo(tmp_path, dashboard="from database import get_db\n")
    _write(tmp_path, "database/__init__.py", "def get_db():\n    return None\n")
    assert self_rooted_dirs(tmp_path, [*files, "database/__init__.py"]) == []


def test_runner_puts_self_rooted_directory_on_pythonpath(tmp_path):
    _self_rooted_repo(tmp_path)
    generated = [{"path": "tests/test_dashboard.py", "content": "def test_x():\n    pass\n"}]
    plan = runners.build_plan(StackProfile(primary="python"), tmp_path, generated, cache_dir=tmp_path / "c")
    assert str(tmp_path / "backend") in plan.steps[-1].env["PYTHONPATH"].split(__import__("os").pathsep)


def test_declared_or_installed_distribution_is_never_a_self_rooted_directory(tmp_path):
    # Review fix 1: app/requests.py must not make app/ an import root that shadows the real `import requests`.
    files = ["app/requests.py", "app/client.py", "app/api.py"]
    _write(tmp_path, "app/requests.py", "def get():\n    return 1\n")
    _write(tmp_path, "app/client.py", "import requests\n")
    _write(tmp_path, "app/api.py", "import requests\n")
    assert self_rooted_dirs(tmp_path, files) == ["app"]
    assert self_rooted_dirs(tmp_path, files, skip=frozenset({"Requests"})) == []
    assert "app" not in python_import_roots(files, root=tmp_path, skip=frozenset({"requests"}))


def test_runner_does_not_put_a_declared_shadowing_directory_on_pythonpath(tmp_path):
    _write(tmp_path, "requirements.txt", "requests==2.31\n")
    _write(tmp_path, "app/requests.py", "def get():\n    return 1\n")
    _write(tmp_path, "app/client.py", "import requests\n")
    _write(tmp_path, "app/api.py", "import requests\n")
    generated = [{"path": "tests/test_client.py", "content": "def test_x():\n    pass\n"}]
    plan = runners.build_plan(StackProfile(primary="python"), tmp_path, generated, cache_dir=tmp_path / "c")
    assert str(tmp_path / "app") not in plan.steps[-1].env["PYTHONPATH"].split(__import__("os").pathsep)


def test_tests_tooling_docs_vendor_and_hidden_directories_are_never_roots(tmp_path):
    # Review fix 2: the same bare-import pattern inside non-source directories must be ignored.
    files = []
    for folder in ["tests/unit", "scripts", "docs", "vendor/pkg", ".venv/lib", "examples"]:
        _write(tmp_path, f"{folder}/helpers.py")
        _write(tmp_path, f"{folder}/a.py", "from helpers import x\n")
        _write(tmp_path, f"{folder}/b.py", "from helpers import x\n")
        files += [f"{folder}/helpers.py", f"{folder}/a.py", f"{folder}/b.py"]
    assert self_rooted_dirs(tmp_path, files) == []
    # Control: the same layout in a source directory is found.
    _write(tmp_path, "app/helpers.py")
    _write(tmp_path, "app/a.py", "from helpers import x\n")
    _write(tmp_path, "app/b.py", "from helpers import x\n")
    assert self_rooted_dirs(tmp_path, [*files, "app/helpers.py", "app/a.py", "app/b.py"]) == ["app"]


def test_sibling_roots_sharing_a_child_name_keep_only_the_stronger(tmp_path):
    # Review fix 3: both sibling roots provide a child named `common`.
    for folder in ["svc_a", "svc_b"]:
        _write(tmp_path, f"{folder}/common/__init__.py")
    _write(tmp_path, "svc_a/x.py", "from common import c\n")
    _write(tmp_path, "svc_a/y.py", "from common import c\n")
    _write(tmp_path, "svc_b/z.py", "from common import c\n")
    _write(tmp_path, "svc_b/w.py", "from common import c\n")
    files = [
        "svc_a/common/__init__.py",
        "svc_b/common/__init__.py",
        "svc_a/x.py",
        "svc_a/y.py",
        "svc_b/z.py",
        "svc_b/w.py",
    ]
    assert self_rooted_dirs(tmp_path, files) == []  # a tie drops both
    _write(tmp_path, "svc_a/v.py", "from common import c\n")
    assert self_rooted_dirs(tmp_path, [*files, "svc_a/v.py"]) == ["svc_a"]


def test_nested_roots_keep_the_outer_only_when_it_has_its_own_hits(tmp_path):
    # Review fix 3: the outer root has direct hits, so the inner one is dropped.
    _self_rooted_repo(tmp_path)
    _write(tmp_path, "backend/api.py", "from database import get_db\n")
    _write(tmp_path, "backend/admin/util.py")
    _write(tmp_path, "backend/admin/x.py", "from util import a\n")
    _write(tmp_path, "backend/admin/y.py", "from util import a\n")
    files = [
        *SELF_ROOTED,
        "backend/api.py",
        "backend/admin/util.py",
        "backend/admin/x.py",
        "backend/admin/y.py",
    ]
    assert self_rooted_dirs(tmp_path, files) == ["backend"]


def test_nested_roots_keep_the_inner_when_the_outer_has_no_own_hits(tmp_path):
    # Review fix 3: the outer root only qualifies through files inside the inner one, so the inner is kept.
    _write(tmp_path, "backend/__init__.py")
    _write(tmp_path, "backend/database/__init__.py")
    _write(tmp_path, "backend/admin/x.py", "from database import a\nfrom util import b\n")
    _write(tmp_path, "backend/admin/y.py", "from database import a\nfrom util import b\n")
    _write(tmp_path, "backend/admin/util.py")
    files = [
        "backend/__init__.py",
        "backend/database/__init__.py",
        "backend/admin/x.py",
        "backend/admin/y.py",
        "backend/admin/util.py",
    ]
    assert self_rooted_dirs(tmp_path, files) == ["backend/admin"]


def test_analysis_does_not_reuse_roots_computed_for_older_file_contents(tmp_path):
    # Review fix 4: the cache key is the file list; a fresh analysis of the same list must recompute.
    from maf_test_harness.repo.analyzer import analyze_repository

    files = _self_rooted_repo(tmp_path)
    assert self_rooted_dirs(tmp_path, files) == ["backend"]
    _write(tmp_path, "backend/admin/dashboard.py", "import os\n")
    analyze_repository(tmp_path, "https://example.test/repo")
    assert self_rooted_dirs(tmp_path, files) == []


def test_hygiene_resolves_patch_targets_through_the_repository_root(tmp_path):
    # Review fix 5: patch("database.missing_name") is only checked when the root-aware module map is used.
    from maf_test_harness.skills.contracts import check_python_test_hygiene

    files = _self_rooted_repo(tmp_path)
    _write(tmp_path, "backend/database/__init__.py", "def get_db():\n    return None\n")
    content = "from unittest.mock import patch\n\n\ndef test_x():\n    with patch('database.missing_name'):\n        pass\n"

    def read(rel):
        return (tmp_path / rel).read_text(encoding="utf-8") if (tmp_path / rel).is_file() else None

    check_python_test_hygiene(content, "t.py", files, read_source=read)
    with pytest.raises(ValueError):
        check_python_test_hygiene(content, "t.py", files, read_source=read, root=tmp_path)

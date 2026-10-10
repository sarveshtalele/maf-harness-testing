from __future__ import annotations

import pytest

from maf_test_harness.qa import triage
from maf_test_harness.repo.import_recipes import python_import_recipes
from maf_test_harness.skills.contracts import (
    check_python_imports,
    expand_generated_lines,
    validate_generated_files,
)

MAIN = (
    "import sys\nfrom pathlib import Path\n_HERE = Path(__file__).resolve().parent\n"
    "sys.path.insert(0, str(_HERE))\nfrom engine.graph_builder import DependencyGraphBuilder\n"
)
FILES = {
    "skills/change-impact-analysis/scripts/main.py": MAIN,
    "skills/change-impact-analysis/scripts/engine/graph_builder.py": "class DependencyGraphBuilder:\n    pass\n",
}


@pytest.fixture
def repo(tmp_path):
    for rel, src in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(src, encoding="utf-8")
    return tmp_path


def test_recipe_from_app_sys_path_insert(repo):
    rec = python_import_recipes(repo, list(FILES), ["DependencyGraphBuilder.build in graph_builder.py"])
    r = rec["skills/change-impact-analysis/scripts/engine/graph_builder.py"]
    assert r["sys_path_root"] == "skills/change-impact-analysis/scripts"
    assert r["import"] == "from engine.graph_builder import DependencyGraphBuilder"
    assert "pyproject.toml" in r["code"] and '/ "skills" / "change-impact-analysis" / "scripts"' in r["code"]


def test_recipe_derived_without_app_insert(tmp_path):
    files = ["skill-packages/03-agent/agent.py"]
    (tmp_path / files[0]).parent.mkdir(parents=True)
    (tmp_path / files[0]).write_text("def run():\n    pass\n", encoding="utf-8")
    rec = python_import_recipes(tmp_path, files, ["agent.py::run"])
    assert rec[files[0]]["sys_path_root"] == "skill-packages/03-agent"
    assert rec[files[0]]["import"] == "from agent import run"
    assert python_import_recipes(tmp_path, ["app/core.py"], ["core.py"]) == {}


def test_validator_accepts_recipe_and_quotes_it_otherwise(repo):
    read = lambda p: FILES.get(p)  # noqa: E731
    good = (
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/change-impact-analysis/scripts'))\n"
        "from engine.graph_builder import DependencyGraphBuilder\n"
    )
    check_python_imports(good, list(FILES), "", "tests/t.py", read_source=read)
    bad = "from skills.change_impact_analysis.scripts.engine.graph_builder import DependencyGraphBuilder\n"
    with pytest.raises(ValueError) as err:
        check_python_imports(bad, list(FILES), "", "tests/t.py", read_source=read)
    assert "from engine.graph_builder import DependencyGraphBuilder" in str(err.value)
    assert "Do not translate" in str(err.value)
    assert "import_recipes" in triage.remedy_for(str(err.value))


def test_stray_json_punctuation_in_lines_is_a_syntax_error_with_remedy():
    # Real run add278f4c106 batch 15: the model emitted `],` and `case_ids:[` as source lines (pure model output).
    payload = {
        "files": [
            {
                "path": "t.py",
                "case_ids": ["C"],
                "lines": ["def test_a():", "    assert [1] == [1]", "],", "case_ids:["],
            }
        ]
    }
    with pytest.raises(ValueError, match="generated Python source is invalid") as err:
        validate_generated_files(expand_generated_lines(payload), {"C"})
    assert "syntactically complete" in triage.remedy_for(str(err.value))


def test_symbol_targets_resolve_through_requirement_files_without_name_overlap(tmp_path):
    from maf_test_harness.repo.import_recipes import python_import_recipes

    for rel, body in {
        "skills/my-skill/scripts/engine/impact_analyzer.py": "class ImpactAnalyzer:\n    def analyze(self):\n        return 1\n",
        "skills/other-skill/scripts/engine/analyzer.py": "def generate_report():\n    return 2\n",
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body, encoding="utf-8")
    files = [
        "skills/my-skill/scripts/engine/impact_analyzer.py",
        "skills/other-skill/scripts/engine/analyzer.py",
    ]
    recipes = python_import_recipes(tmp_path, files, ["ImpactAnalyzer.analyze", files[0]])
    assert list(recipes) == [files[0]]
    # no app sys.path insert: fallback root is the last non-identifier dir (skills/my-skill)
    assert recipes[files[0]]["import"] == "from scripts.engine.impact_analyzer import ImpactAnalyzer"


def test_json_structure_leaked_into_lines_is_dropped_but_code_is_kept():
    from maf_test_harness.skills.contracts import _JSON_DEBRIS, expand_generated_lines

    lines = ["def test_x():", "    data = [", "        1,", "    ]", "    assert data == [1]", "case_ids"]
    out = expand_generated_lines({"files": [{"path": "t.py", "lines": lines, "case_ids": ["TC-1"]}]})
    assert (
        out["files"][0]["content"]
        == "def test_x():\n    data = [\n        1,\n    ]\n    assert data == [1]\n"
    )
    for debris in ("case_ids", '"case_ids": ["TC-1"]', "case_ids]: [", '"],"', '"case_ids:["'):
        assert _JSON_DEBRIS.match(debris), debris
    for code in ("    ]", "],", "case_ids = ['a']", "assert case_ids", "x = [1]"):
        assert not _JSON_DEBRIS.match(code), code


def test_recipe_purges_cached_package_when_two_dirs_share_a_top_level_name(tmp_path):
    from maf_test_harness.repo.import_recipes import python_import_recipes

    files = [
        "skills/a-one/scripts/engine/__init__.py",
        "skills/a-one/scripts/engine/x.py",
        "skills/b-two/scripts/engine/__init__.py",
        "skills/b-two/scripts/engine/y.py",
    ]
    for rel in files:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("def run():\n    return 1\n", encoding="utf-8")
    how = python_import_recipes(tmp_path, files, [files[1]])[files[1]]["code"]
    assert "del sys.modules[_m]" in how and '"scripts"' in how  # fallback root skills/a-one -> top 'scripts'
    solo = python_import_recipes(tmp_path, files[:2], [files[1]])[files[1]]["code"]
    assert "del sys.modules" not in solo


def _two_engines(tmp_path):
    for skill, fn in (("change-impact-analysis", "analyzer"), ("factory/legacy-discovery", "scanner")):
        d = tmp_path / "skills" / skill / "scripts" / "engine"
        d.mkdir(parents=True)
        (d / "__init__.py").write_text("")
        (d / f"{fn}.py").write_text(f"def {fn}_fn():\n    return '{fn}'\n")
        (d.parent / "main.py").write_text(  # the application's own launcher shows the working sys.path root
            "import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            f"from engine.{fn} import {fn}_fn\n"
        )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    files = [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.py")]
    return files, python_import_recipes(
        tmp_path, files, [f for f in files if "/engine/" in f and not f.endswith("__init__.py")]
    )


def test_recipe_code_runs_for_two_colliding_engine_packages(tmp_path):
    import subprocess
    import sys

    files, recipes = _two_engines(tmp_path)
    a = recipes["skills/change-impact-analysis/scripts/engine/analyzer.py"]
    b = recipes["skills/factory/legacy-discovery/scripts/engine/scanner.py"]
    assert a["purge"] and b["purge"]
    (tmp_path / "tests").mkdir()
    t = tmp_path / "tests" / "test_both.py"
    body = "\n".join(
        f"{r['code'].replace(chr(10) + r['import'], '')}\n{r['import'].replace(' import ', ' import ')}\nprint({r['import'].split()[-1]}())"
        for r in (a, b)
    )
    t.write_text(body)
    out = subprocess.run([sys.executable, str(t)], capture_output=True, text=True, cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["analyzer", "scanner"]


def test_autofix_rewrites_unique_recipe_and_skips_ambiguous(tmp_path):
    from maf_test_harness.tools.autofix import fix_import_recipes

    files, recipes = _two_engines(tmp_path)
    scanner = "skills/factory/legacy-discovery/scripts/engine/scanner.py"
    src = '"""doc."""\nfrom __future__ import annotations\nimport pytest\nfrom skills.factory.legacy_discovery.scripts.engine.scanner import scanner_fn\n'
    fixed, fixes = fix_import_recipes("tests/t.py", src, recipes, [scanner])
    assert fixes[0]["kind"] == "import_recipe_applied"
    assert "from engine.scanner import scanner_fn" in fixed and "legacy_discovery" not in fixed
    lines = fixed.splitlines()
    assert lines[:2] == ['"""doc."""', "from __future__ import annotations"] and "import sys" in lines[2:4]
    ns: dict = {"__file__": str(tmp_path / "tests" / "t.py")}
    exec(compile(fixed.replace("import pytest\n", ""), "t", "exec"), ns)
    assert ns["scanner_fn"]() == "scanner"
    # `engine.scanner` appears under one skill only, but `engine` modules of both match `from engine import x`
    amb = "from engine import something\n"
    assert fix_import_recipes(
        "t.py", amb, {**recipes, "x/scripts/engine/__init__.py": recipes[scanner]}, []
    ) == (amb, [])


def test_autofix_scripts_main_ambiguous_is_left_alone():
    from maf_test_harness.tools.autofix import fix_import_recipes

    def rec(root):
        return {
            "module": "main",
            "purge": "",
            "code": "import sys\nx\nfrom main import run",
            "import": "from main import run",
            "sys_path_root": root,
        }

    recipes = {
        "skills/a/scripts/main.py": rec("skills/a/scripts"),
        "skills/b/scripts/main.py": rec("skills/b/scripts"),
    }
    src = "from scripts.main import run\n"
    assert fix_import_recipes("t.py", src, recipes, []) == (src, [])
    fixed, fixes = fix_import_recipes("t.py", src, recipes, ["skills/b/scripts/main.py"])
    assert "from main import run" in fixed and fixes


def test_autofix_injects_purge_before_sys_path_insert(tmp_path):
    from maf_test_harness.tools.autofix import fix_missing_purge

    files, recipes = _two_engines(tmp_path)
    src = (
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path.cwd() / 'skills' / 'change-impact-analysis' / 'scripts'))\n"
        "from engine.analyzer import analyzer_fn\n"
    )
    fixed, fixes = fix_missing_purge("t.py", src, recipes)
    assert fixes and fixed.splitlines()[2].startswith("for _m in [m for m in sys.modules")
    assert fix_missing_purge("t.py", fixed, recipes)[1] == []


def test_autofix_path_parents_division():
    from maf_test_harness.tools.autofix import fix_path_parents_division

    src = 'from pathlib import Path\n\nROOT = Path(__file__).resolve().parents / "skills" / "x"\n'
    fixed, fixes = fix_path_parents_division("t.py", src)
    assert fixes and 'ROOT = _ROOT / "skills" / "x"' in fixed
    ns: dict = {"__file__": __file__}
    exec(fixed, ns)
    assert ns["ROOT"].parts[-2:] == ("skills", "x")
    assert fix_path_parents_division("t.py", 'p = Path(__file__).resolve().parents[1] / "x"\n')[1] == []


def test_autofix_tie_breaks_look_alike_scripts_by_test_location():
    # real run 1a9bbae78ff0 batches 26/28: `import scripts.main` under skills/<group>/<skill>/tests/...
    from maf_test_harness.tools.autofix import fix_import_recipes

    def rec(root):
        return {
            "module": "main",
            "purge": "",
            "code": f"import sys\n# {root}\nfrom main import run",
            "import": "from main import run",
            "sys_path_root": root,
        }

    recipes = {
        "skills/integrations/jira-ticket/scripts/main.py": rec("skills/integrations/jira-ticket/scripts"),
        "skills/integrations/github-pr/scripts/main.py": rec("skills/integrations/github-pr/scripts"),
        "skills/factory/other/scripts/main.py": rec("skills/factory/other/scripts"),
    }
    src = "from scripts.main import run\n"
    path = "skills/integrations/jira-ticket/tests/batch_26/test_main.py"
    fixed, fixes = fix_import_recipes(path, src, recipes, [])
    assert "from main import run" in fixed and fixes
    assert "# skills/integrations/jira-ticket/scripts" in fixed
    # no location signal (equal scores) stays ambiguous
    assert fix_import_recipes("tests/t.py", src, recipes, []) == (src, [])


def test_missing_import_is_added_before_code_that_needs_it():
    # real run: `import sys` was appended after `from change_impact_skill import ...`, below sys.path.insert(...)
    from maf_test_harness.tools.autofix import _insert_python_imports

    src = (
        "# CASE_ID: x\nimport subprocess\nfrom pathlib import Path\n\nimport pytest\n\n"
        'sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))\n'
        "from change_impact_skill import f\n"
    )
    out = _insert_python_imports(src, ["import sys"])
    assert out.index("import sys") < out.index("sys.path.insert")
    ns: dict = {"__file__": __file__}
    exec(
        compile(
            out.replace("import pytest\n", "").replace("from change_impact_skill import f\n", ""), "t", "exec"
        ),
        ns,
    )


def test_trailing_json_closer_after_python_source_is_dropped():
    # real run 1a9bbae78ff0 batch 55: the last `lines` entry was a bare "}" -> "unmatched '}' (line 105)"
    from maf_test_harness.skills.contracts import expand_generated_lines

    lines = [
        "def test_x():",
        "    with pytest.raises(KeyError, match='delete-me'):",
        "        {}['delete-me']",
        "}",
    ]
    out = expand_generated_lines({"files": [{"path": "tests/t.py", "lines": lines, "case_ids": ["TC-1"]}]})
    assert out["files"][0]["content"].endswith("{}['delete-me']\n")
    # a closer that real code needs is kept
    keep = ["x = {", "    1: 2,", "}"]
    out = expand_generated_lines({"files": [{"path": "t.py", "lines": keep, "case_ids": ["TC-1"]}]})
    assert out["files"][0]["content"] == "x = {\n    1: 2,\n}\n"

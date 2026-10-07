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
    assert "pyproject.toml" in r["how"]


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

"""A word in a comment or description is not a declared dependency (regression: `import engine` passed every gate).

Found on a live run against requirement-analysis-skill: its `assets/requirements.txt` says "The engine is pure Python standard
library", so `from engine.requirement_parser import ...` (the real module is `scripts.engine.requirement_parser`) was accepted as a
"declared dependency" and failed only at collection, three times.
"""

import pytest

from maf_test_harness.tools.code_intel import RepoContext, resolve_import, static_gate

SOURCES = [
    "scripts/engine/__init__.py",
    "scripts/engine/requirement_parser.py",
    "scripts/requirement_analysis_skill.py",
]
BAD = "from engine.requirement_parser import RequirementParser\n\n\ndef test_it():\n    assert RequirementParser\n"
GOOD = BAD.replace("from engine.", "from scripts.engine.")


def context(tmp_path, files):
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for rel in SOURCES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("class RequirementParser:\n    pass\n", encoding="utf-8")
    return RepoContext(root=tmp_path, source_files=SOURCES, package_files=list(files))


COMMENT_ONLY = {
    "assets/requirements.txt": "# The engine is pure Python standard library (argparse, ast, json).\n# No third-party packages are required.\n"
}


def test_a_word_in_a_requirements_comment_does_not_declare_a_dependency(tmp_path):
    ctx = context(tmp_path, COMMENT_ONLY)
    with pytest.raises(
        ValueError, match=r"Did you mean the repository module scripts\.engine\.requirement_parser"
    ):
        static_gate(ctx, "tests/test_a.py", BAD)


def test_the_correct_repository_path_passes(tmp_path):
    static_gate(context(tmp_path, COMMENT_ONLY), "tests/test_a.py", GOOD)


def test_resolve_import_does_not_call_it_a_declared_dependency(tmp_path):
    result = resolve_import(context(tmp_path, COMMENT_ONLY), "engine.requirement_parser")
    assert result["status"] != "declared_dependency"
    assert "scripts.engine.requirement_parser" in str(result)


def test_an_inline_comment_does_not_declare_either(tmp_path):
    ctx = context(tmp_path, {"requirements.txt": "flask>=3  # a small web engine\n"})
    assert "engine" not in ctx.dependency_text and "flask" in ctx.dependency_text
    with pytest.raises(ValueError, match="engine.requirement_parser"):
        static_gate(ctx, "tests/test_a.py", BAD)


def test_a_pyproject_description_is_not_a_declaration(tmp_path):
    toml = '[project]\nname = "demo"\ndescription = "A rules engine for planning"\ndependencies = ["pyyaml>=6"]\n'
    ctx = context(tmp_path, {"pyproject.toml": toml})
    assert "pyyaml" in ctx.dependency_text.lower() and "engine" not in ctx.dependency_text
    with pytest.raises(ValueError, match="engine.requirement_parser"):
        static_gate(ctx, "tests/test_a.py", BAD)


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ({"requirements.txt": "requests>=2\n"}, "import requests\n"),
        ({"requirements-dev.txt": "# tools\nrequests>=2  # http\n"}, "import requests\n"),
        ({"pyproject.toml": '[project]\ndependencies = ["PyYAML>=6"]\n'}, "import yaml\n"),
        ({"setup.py": "setup(install_requires=['requests'])  # deps\n"}, "import requests\n"),
        ({"Pipfile": "[packages]\nrequests = '*'\n"}, "import requests\n"),
    ],
)
def test_real_declarations_still_resolve(tmp_path, files, code):
    ctx = context(tmp_path, files)
    static_gate(ctx, "tests/test_a.py", code + "\n\ndef test_it():\n    assert True\n")


def test_declared_text_of_non_python_manifests_is_unchanged(tmp_path):
    pom = "<project><dependencies><dependency><artifactId>junit-jupiter</artifactId></dependency></dependencies></project>"
    ctx = context(tmp_path, {"pom.xml": pom})
    assert "junit-jupiter" in ctx.dependency_text

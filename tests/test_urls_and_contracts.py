import pytest

from maf_test_harness.repo.analyzer import RepoInputError, validate_github_url
from maf_test_harness.skills.contracts import (
    finalize_discovered_requirements,
    validate_generated_files,
)


@pytest.mark.parametrize(
    ("url", "ref"),
    [
        ("https://github.com/acme/widgets", None),
        ("https://github.com/acme/widgets/", None),
        ("https://github.com/acme/widgets.git", None),
        ("https://www.github.com/acme/widgets", None),
        ("https://github.com/acme/widgets/tree/main", "main"),
        ("https://github.com/acme/widgets/tree/release/1.2", "release/1.2"),
    ],
)
def test_github_page_and_clone_urls_normalize_to_one_clone_url(url, ref):
    assert validate_github_url(url, with_ref=True) == (
        "https://github.com/acme/widgets.git",
        "acme",
        "widgets",
        ref,
    )
    assert validate_github_url(url) == ("https://github.com/acme/widgets.git", "acme", "widgets")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/acme/widgets",
        "git@github.com:acme/widgets.git",
        "https://gitlab.com/acme/widgets",
        "https://github.com/acme",
        "https://github.com/acme/widgets/issues/1",
        "https://github.com/acme/widgets/blob/main/a",
        "https://token@github.com/acme/widgets",
        "https://github.com/acme/widgets?x=1",
        "https://github.com/acme/widgets/tree/../x",
        "https://github.com/acme/widgets/tree/a..b",
    ],
)
def test_unsafe_or_non_repository_urls_rejected(url):
    with pytest.raises(RepoInputError):
        validate_github_url(url)


def _draft(**over):
    base = {
        "app_name": "demo",
        "summary": "s",
        "actors": [{"name": "Admin", "description": "manages", "source_files": ["a.py"]}],
        "requirements": [
            {
                "text": "Admin can delete",
                "evidence": "a.py:3",
                "source_files": ["a.py"],
                "category": "security",
                "actor": "Admin",
            },
            {
                "text": "Deletion is logged",
                "evidence": "a.py:9",
                "source_files": ["a.py"],
                "category": "functional",
                "actor": "system",
            },
        ],
        "acceptance_criteria": [{"requirement_ref": 2, "text": "log line written", "source_files": ["a.py"]}],
        "validation_rules": [{"text": "id must be positive", "source_files": ["a.py"]}],
        "api_contracts": [
            {
                "method": "delete",
                "path": "/items/{id}",
                "status_codes": ["204", "404"],
                "source_files": ["a.py"],
            }
        ],
        "exclusions": [],
        "assumptions": [],
        "gaps": [],
    }
    return {**base, **over}


def test_requirements_finalizer_assigns_stable_ids_and_rewires_references():
    out = finalize_discovered_requirements(_draft(), "https://github.com/acme/demo")
    again = finalize_discovered_requirements(_draft(), "https://github.com/acme/demo")
    assert out["requirement_model"] == again["requirement_model"]  # deterministic IDs
    reqs = out["requirement_model"]["requirements"]
    criterion = out["requirement_model"]["acceptance_criteria"][0]
    assert criterion["requirement_id"] == reqs[1]["id"] and criterion["id"].startswith("AC-")
    assert out["api_contracts"][0]["method"] == "DELETE" and out["api_contracts"][0]["id"].startswith("API-")
    assert out["requirement_model"]["validation_rules"][0]["id"].startswith("VAL-")
    assert out["actors"][0]["name"] == "Admin"


def test_requirements_finalizer_rejects_unknown_actor_and_bad_refs():
    bad_actor = _draft()
    bad_actor["requirements"][0]["actor"] = "Ghost"
    with pytest.raises(ValueError, match="unknown actor"):
        finalize_discovered_requirements(bad_actor, "u")
    with pytest.raises(ValueError, match="requirement_ref"):
        finalize_discovered_requirements(
            _draft(acceptance_criteria=[{"requirement_ref": 9, "text": "x", "source_files": ["a.py"]}]), "u"
        )


def payload(code, path):
    return {"files": [{"path": path, "content": "// CASE_ID: TC-1\n" + code, "case_ids": ["TC-1"]}]}


@pytest.mark.parametrize(
    ("code", "path"),
    [
        ("test.skip('x', () => { expect(a()).toBe(1) })\n", "a.test.js"),
        ("@Disabled\n@Test\nvoid t() { assertEquals(1, a()); }\n", "ATest.java"),
        ("test('x', () => {\n  // TODO write me\n  expect(a()).toBe(1)\n})\n", "a.test.js"),
        ("test('broken', () => {\n  expect(a(.toBe(1)\n})\n", "a.test.js"),
        ("expect(/x/.test(y)).toBe(true)\n", "a.test.js"),
    ],
)
def test_validator_rejects_skipped_placeholder_broken_or_undeclared_tests(code, path):
    with pytest.raises(ValueError):
        validate_generated_files(payload(code, path), {"TC-1"})


def test_ui_test_may_use_the_html_placeholder_attribute():
    code = "import { test, expect } from '@playwright/test'\ntest('search', async ({ page }) => {\n  await page.locator('[placeholder=\"Search\"]').fill('x')\n  await expect(page.locator('h1')).toBeVisible()\n})\n"
    assert validate_generated_files(payload(code, "e2e/search.spec.ts"), {"TC-1"})["test_declarations"] == 1


def py_payload(code):
    return {"files": [{"path": "test_x.py", "content": "# CASE_ID: TC-1\n" + code, "case_ids": ["TC-1"]}]}


def test_two_test_methods_for_one_case_is_rejected_with_a_python_only_parametrize_hint():
    two_funcs = (
        "import subprocess\n\n"
        "def test_help_flag_prints_help_and_exits_code_0():\n"
        "    r = subprocess.run(['app', '--help']); assert r.returncode == 0\n\n"
        "def test_h_flag_prints_help_and_exits_code_0():\n"
        "    r = subprocess.run(['app', '-h']); assert r.returncode == 0\n"
    )
    with pytest.raises(ValueError, match=r"pytest\.mark\.parametrize") as excinfo:
        validate_generated_files(py_payload(two_funcs), {"TC-1"})
    assert "JUnit" not in str(excinfo.value)  # a Python target must not be shown JUnit syntax


def test_two_test_methods_for_one_exception_type_variant_is_also_rejected():
    """The real failure this covers: test_main_catches_valueerror + test_main_catches_runtimeerror for 1 case."""
    two_funcs = (
        "import pytest\n\n"
        "def test_main_catches_valueerror():\n"
        "    with pytest.raises(SystemExit):\n        main(ValueError())\n\n"
        "def test_main_catches_runtimeerror():\n"
        "    with pytest.raises(SystemExit):\n        main(RuntimeError())\n"
    )
    with pytest.raises(ValueError, match="different exception/error type"):
        validate_generated_files(py_payload(two_funcs), {"TC-1"})


def test_two_test_methods_for_one_case_in_java_is_rejected_with_a_junit_only_hint():
    two_methods = (
        "class ATest {\n"
        '  @org.junit.Test public void testHelpFlag() { assertEquals(0, run("--help")); }\n'
        '  @org.junit.Test public void testHFlag() { assertEquals(0, run("-h")); }\n}\n'
    )
    payload = {
        "files": [{"path": "ATest.java", "content": "// CASE_ID: TC-1\n" + two_methods, "case_ids": ["TC-1"]}]
    }
    with pytest.raises(ValueError, match="JUnit") as excinfo:
        validate_generated_files(payload, {"TC-1"})
    assert "pytest" not in str(excinfo.value)  # a Java target must not be shown pytest syntax


def test_a_parametrized_test_covering_both_variants_counts_as_one_declaration():
    parametrized = (
        "import subprocess\nimport pytest\n\n"
        "@pytest.mark.parametrize('flag', ['--help', '-h'])\n"
        "def test_help_flag_prints_help_and_exits_code_0(flag):\n"
        "    r = subprocess.run(['app', flag]); assert r.returncode == 0\n"
    )
    assert validate_generated_files(py_payload(parametrized), {"TC-1"})["test_declarations"] == 1


REPO = ["reverse_engineer_skill.py", "src/pkg/core.py", "src/pkg/__init__.py", "skills/agent/tool.py"]


@pytest.mark.parametrize(
    "code",
    [
        "import os\nimport pytest\nfrom unittest import mock\n",
        "from reverse_engineer_skill import main\n",
        "from pkg.core import run\nimport pkg\n",  # src layout is on PYTHONPATH
        "from skills.agent.tool import go\n",
        "import requests\n",  # declared dependency
    ],
)
def test_resolvable_python_imports_pass(code):
    from maf_test_harness.skills.contracts import check_python_imports

    check_python_imports(code, REPO, "requests==2.31\n")


@pytest.mark.parametrize(
    ("code", "needle"),
    [
        ("from tool import go\n", "Did you mean the repository module skills.agent.tool"),
        ("from pkg.missing import x\n", "does not match any repository path"),
        ("import notinstalled\n", "not a declared dependency"),
    ],
)
def test_unresolvable_python_imports_fail_with_a_hint(code, needle):
    from maf_test_harness.skills.contracts import check_python_imports

    with pytest.raises(ValueError, match=needle):
        check_python_imports(code, REPO, "requests\n")


def test_installed_transitive_module_of_a_declared_dependency_resolves():
    """`langchain` is declared; `langchain_core` is only its transitive dependency, but it is really installed."""
    from maf_test_harness.skills.contracts import check_python_imports

    code = "from langchain_core.language_models.fake import FakeListLLM\n"
    with pytest.raises(ValueError, match="not a declared dependency"):
        check_python_imports(code, REPO, "langchain==0.1.20\n")
    check_python_imports(code, REPO, "langchain==0.1.20\n", installed=frozenset({"langchain-core"}))


def test_non_importable_module_paths_get_actionable_guidance_and_path_aware_tests_pass():
    from maf_test_harness.skills.contracts import check_python_imports

    repo = ["skill-packages/03-agent-sdk-skill/reverse_engineer_agent.py"]
    with pytest.raises(ValueError, match="not an importable package.*sys.path"):
        check_python_imports(
            "from skill_packages.agent_sdk_skill.reverse_engineer_agent import Agent\n", repo, ""
        )
    aware = (
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path.cwd() / 'skill-packages/03-agent-sdk-skill'))\n"
        "from reverse_engineer_agent import Agent\n"
    )
    check_python_imports(aware, repo, "")


POM = "<dependency><groupId>junit</groupId><artifactId>junit</artifactId></dependency>"
JAVA_REPO = ["src/main/java/org/acme/shop/model/Category.java"]


def test_java_imports_resolve_against_jdk_repo_and_declared_dependencies():
    from maf_test_harness.skills.contracts import check_java_imports

    check_java_imports(
        "import java.util.List;\nimport javax.validation.Validator;\nimport org.junit.Test;\n"
        "import static org.junit.Assert.assertEquals;\nimport org.acme.shop.model.Category;\n",
        JAVA_REPO,
        POM,
    )


@pytest.mark.parametrize(
    "line",
    [
        "import io.restassured.RestAssured;",
        "import com.jayway.restassured.RestAssured;",
        "import org.mockito.Mockito;",
    ],
)
def test_java_imports_of_undeclared_libraries_are_rejected(line):
    from maf_test_harness.skills.contracts import check_java_imports

    with pytest.raises(ValueError, match="not declared in the build files"):
        check_java_imports(line + "\n", JAVA_REPO, POM)


@pytest.mark.parametrize(
    ("path", "code", "needle"),
    [
        (
            "ATest.java",
            "import org.junit.Test;\npublic class ATest {\n  @Test public void t() {\n    assertEquals(1, 1);\n  }\n",
            "end of file|unclosed",
        ),
        ("a.test.ts", "test('x', () => {\n  expect(f(1)).toBe(2)\n", "unclosed"),
        ("b_test.go", "package b\nfunc TestX(t *testing.T) {\n  if f() != 1 {\n", "unclosed|expected|found"),
    ],
)
def test_truncated_sources_are_rejected_before_a_run_is_wasted(path, code, needle):
    with pytest.raises(ValueError, match=needle):
        validate_generated_files(payload(code, path), {"TC-1"})


def test_java_semantic_errors_are_not_mistaken_for_syntax_errors():
    code = (
        "import org.unknown.Lib;\npublic class ATest {\n  @org.junit.Test public void t() { Lib.go(); }\n}\n"
    )
    assert validate_generated_files(payload(code, "ATest.java"), {"TC-1"})["test_declarations"] == 1


PIPELINE = """
import os
from helpers import util

MODULE_CONSTANT = 3

class Cache:
    pass

def run_pipeline(url):
    FILE_CAP = 300
    if len(url) > FILE_CAP:
        def _layer(f):
            return 0
        SLOTS = {0: 75}
    return url
"""


def read(rel):
    return PIPELINE if rel == "engine/pipeline.py" else None


def test_imported_names_must_exist_at_module_level_and_locals_get_a_precise_hint():
    from maf_test_harness.skills.contracts import check_python_imports

    files = ["engine/pipeline.py", "helpers.py"]
    check_python_imports(
        "from engine.pipeline import run_pipeline, Cache, MODULE_CONSTANT, util, os\n",
        files,
        "",
        "t.py",
        read,
    )
    with pytest.raises(ValueError, match=r"'_layer'.*only inside run_pipeline\(\).*cannot be imported"):
        check_python_imports("from engine.pipeline import _layer, FILE_CAP, SLOTS\n", files, "", "t.py", read)
    with pytest.raises(ValueError, match="'nope'.*no such module-level name"):
        check_python_imports("from engine.pipeline import nope\n", files, "", "t.py", read)


def test_modules_with_star_imports_or_getattr_are_not_second_guessed():
    from maf_test_harness.skills.contracts import check_python_imports

    for source in ("from os.path import *\n", "def __getattr__(name):\n    return 1\n"):
        check_python_imports(
            "from engine.pipeline import anything\n",
            ["engine/pipeline.py"],
            "",
            "t.py",
            lambda rel, src=source: src,
        )


def test_submodules_are_importable_names():
    from maf_test_harness.skills.contracts import check_python_imports

    check_python_imports(
        "from engine import pipeline\n",
        ["engine/__init__.py", "engine/pipeline.py"],
        "",
        "t.py",
        lambda rel: "" if rel.endswith("__init__.py") else None,
    )


def test_modules_in_non_package_directories_are_importable_by_bare_name_because_the_runner_adds_the_directory():
    from maf_test_harness.skills.contracts import check_python_imports, extra_import_roots

    repo = ["skill-packages/03-agent-sdk-skill/reverse_engineer_agent.py", "engine/pipeline.py", "run.py"]
    assert extra_import_roots(repo) == ["skill-packages/03-agent-sdk-skill"]  # engine/ is importable already
    check_python_imports("from reverse_engineer_agent import ReverseEngineerAgent\n", repo, "", "t.py")
    with pytest.raises(ValueError, match="does not match any repository path|not stdlib"):
        check_python_imports("from other_module import X\n", repo, "", "t.py")


def test_resolve_id_repairs_garbled_hash_suffixes_but_never_guesses_between_candidates():
    from maf_test_harness.skills.contracts import resolve_id

    known = [
        "AC-PUT-CATEGORIES-ID-WITH-VALID-BODY-RETURNS-204-WITH-S-BD50B1F2",
        "AC-GET-ITEMS-RETURNS-200-11223344",
    ]
    assert (
        resolve_id("AC-PUT-CATEGORIES-ID-WITH-VALID-BODY-RETURNS-204-WITH-S-BD50F1B2", known) == known[0]
    )  # transposed
    assert resolve_id("ac-get-items-returns-200-11223344", known) == known[1]  # case
    assert resolve_id(known[0], known) == known[0]
    assert resolve_id("AC-DELETE-SOMETHING-ELSE-00000000", known) is None
    twins = ["TC-POS-SUM-AAAAAAAA", "TC-POS-SUM-BBBBBBBB"]
    assert resolve_id("TC-POS-SUM-CCCCCCCC", twins) is None  # ambiguous: refuse rather than guess


def test_generation_case_ids_are_canonicalized_before_validation():
    from maf_test_harness.workflow import _canonicalize_case_ids

    files = {"files": [{"path": "t.py", "case_ids": ["TC-POS-ADD-RETURNS-SUM-DBF3F6C3"]}]}
    out = _canonicalize_case_ids(files, {"TC-POS-ADD-RETURNS-SUM-DBF3F6CE"})
    assert out["files"][0]["case_ids"] == ["TC-POS-ADD-RETURNS-SUM-DBF3F6CE"]


def test_python_source_that_only_fails_at_compile_time_is_rejected():
    """ast.parse accepts 21 nested `with` blocks; CPython refuses them when compiling (i.e. at collection)."""
    nested = "".join("    " * i + "with cm():\n" for i in range(1, 22)) + "    " * 22 + "pass\n"
    code = (
        "from contextlib import nullcontext as cm\n\n\ndef test_deep():\n"
        + nested
        + "    assert value() == 1\n"
    )
    with pytest.raises(ValueError, match="too many statically nested blocks|invalid Python|syntax"):
        validate_generated_files(payload(code, "test_deep.py"), {"TC-1"})


def test_declared_dependencies_are_parsed_from_build_and_manifest_files(tmp_path):
    from maf_test_harness.repo.analyzer import declared_dependencies

    files = {
        "pom.xml": "<project><dependencies><dependency><artifactId>junit</artifactId></dependency>"
        "<dependency><artifactId>hibernate-validator</artifactId></dependency></dependencies></project>",
        "build.gradle": "dependencies { testImplementation 'org.mockito:mockito-core:5.0' }",
        "package.json": '{"dependencies": {"express": "4"}, "devDependencies": {"jest": "29"}}',
        "requirements.txt": "requests==2.31\nanthropic>=0.40\n# comment\n",
        "go.mod": "module x\nrequire github.com/stretchr/testify v1.8.0\n",
    }
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    found = declared_dependencies(tmp_path, list(files))
    assert {
        "junit",
        "hibernate-validator",
        "mockito-core",
        "express",
        "jest",
        "requests",
        "anthropic",
        "github.com/stretchr/testify",
    } <= set(found)
    assert "restassured" not in found and "rest-assured" not in found


def _deeply_nested_test(depth: int) -> str:
    lines = [
        "from contextlib import contextmanager",
        "LOG = []",
        "",
        "@contextmanager",
        "def cm(name):",
        "    LOG.append('enter ' + name)",
        "    try:",
        "        yield name.upper()",
        "    finally:",
        "        LOG.append('exit ' + name)",
        "",
        "def test_deep():",
    ]
    for i in range(depth):
        lines.append("    " * (i + 1) + f"with cm('c{i}') as v{i}:")
    lines.append("    " * (depth + 1) + "assert v0 == 'C0' and v" + str(depth - 1) + f" == 'C{depth - 1}'")
    return "\n".join(lines) + "\n"


def test_deep_with_chains_are_flattened_to_exitstack_with_identical_semantics():
    from maf_test_harness.skills.contracts import flatten_nested_with

    source = _deeply_nested_test(25)
    with pytest.raises(SyntaxError, match="too many statically nested blocks"):
        compile(source, "t.py", "exec")  # the original cannot even be compiled
    flat = flatten_nested_with(source)
    assert flat and "ExitStack" in flat and "with cm(" not in flat.replace("enter_context(cm(", "")
    namespace: dict = {}
    exec(compile(flat, "t.py", "exec"), namespace)
    namespace["test_deep"]()
    log = namespace["LOG"]
    assert log[:25] == [f"enter c{i}" for i in range(25)]  # entered in order
    assert log[25:] == [f"exit c{i}" for i in reversed(range(25))]  # exited in reverse order
    assert flatten_nested_with(_deeply_nested_test(3)) is None  # shallow chains are left alone


def test_a_flattened_test_passes_the_static_gate_that_rejected_the_original():
    from maf_test_harness.skills.contracts import flatten_nested_with

    def py_payload(code):
        return {
            "files": [{"path": "test_deep.py", "content": "# CASE_ID: TC-1\n" + code, "case_ids": ["TC-1"]}]
        }

    source = _deeply_nested_test(25)
    with pytest.raises(ValueError, match="nested blocks"):
        validate_generated_files(py_payload(source), {"TC-1"})
    assert (
        validate_generated_files(py_payload(flatten_nested_with(source)), {"TC-1"})["test_declarations"] == 1
    )


def test_local_app_url_helper_accepts_only_localhost_http_urls():
    from maf_test_harness.settings import is_local_app_url

    assert is_local_app_url(" http://localhost:8080/app ") and is_local_app_url("https://127.0.0.1")
    assert not is_local_app_url("http://evil.example.com")
    assert not is_local_app_url("http://localhost.evil.com")
    assert not is_local_app_url("") and not is_local_app_url("ftp://localhost")


def py_payload_for(path):
    code = "# CASE_ID: TC-1\nimport calc\n\n\ndef test_add():\n    assert calc.add(1, 2) == 3\n"
    return {"files": [{"path": path, "content": code, "case_ids": ["TC-1"]}]}


@pytest.mark.parametrize(
    "path",
    [
        "-x/test_a.py",
        "tests/-rf.py",
        ".github/workflows/test_a.py",
        ".harness-venv/lib/site-packages/test_a.py",
        "tests/a&b.py",
        "tests/a%PATH%.py",
        "tests/a b.py",
        "tests/test_a.yml",
        "pytest.ini",
        "tests/../test_a.py",
    ],
)
def test_generated_paths_are_restricted_to_plain_test_source_files(path):
    with pytest.raises(ValueError, match="unsafe generated path"):
        validate_generated_files(py_payload_for(path), {"TC-1"})


def test_plain_generated_test_paths_are_accepted():
    assert validate_generated_files(py_payload_for("tests/unit/test_add-2.py"), {"TC-1"})

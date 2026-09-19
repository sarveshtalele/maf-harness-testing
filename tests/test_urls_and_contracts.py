import pytest

from maf_test_harness.repo_analyzer import RepoInputError, validate_github_url
from maf_test_harness.skill_contracts import (
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
    from maf_test_harness.skill_contracts import check_python_imports

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
    from maf_test_harness.skill_contracts import check_python_imports

    with pytest.raises(ValueError, match=needle):
        check_python_imports(code, REPO, "requests\n")


def test_non_importable_module_paths_get_actionable_guidance_and_path_aware_tests_pass():
    from maf_test_harness.skill_contracts import check_python_imports

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
    from maf_test_harness.skill_contracts import check_java_imports

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
    from maf_test_harness.skill_contracts import check_java_imports

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

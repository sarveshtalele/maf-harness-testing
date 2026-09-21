"""Error taxonomy: every code has a rule, every fixture reaches the documented diagnosis, tables are consistent."""

from __future__ import annotations

import pytest
from error_cases import CASES

from maf_test_harness import errors
from maf_test_harness.errors import classifiers, playbook
from maf_test_harness.errors.taxonomy import CODES


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_fixture_reaches_documented_diagnosis(case):
    context = errors.Context(missing_packages=case.get("missing", []))
    if "result" in case:
        found = errors.classify_result(case["result"], case.get("text", ""), case["ecosystem"], context)
    else:
        found = errors.classify(case["text"], case["ecosystem"], context)
    assert found, f"nothing matched: {case['name']}"
    assert found[0].code == case["expect"], [d.code for d in found]
    assert found[0].category == CODES[case["expect"]].category


def test_at_least_forty_fixtures_across_the_three_languages():
    assert len(CASES) >= 40
    assert {c["ecosystem"] for c in CASES} >= {"python", "maven", "gradle", "dotnet", "node", None}
    assert any(c["source"] == "recorded" for c in CASES)


def test_every_error_code_is_reachable_by_a_rule_or_a_context_resolution():
    ruled = {
        code
        for table in [*classifiers.ALL_LANGUAGES, classifiers.COMMON, *classifiers.EXECUTION]
        for code, _ in table
    }
    contextual = {"ENV-PY-007"}  # decided from context, not from text alone
    assert set(CODES) - ruled - contextual == set()
    assert {c["expect"] for c in CASES} == set(CODES)  # every code has at least one fixture


def test_every_rule_points_at_a_known_code_and_every_recipe_exists():
    for table in [*classifiers.ALL_LANGUAGES, classifiers.COMMON, *classifiers.EXECUTION]:
        for code, _ in table:
            assert code in CODES
    assert playbook.validate() == []


def test_environment_and_infra_outrank_test_defects_and_are_environmental():
    text = "E   ModuleNotFoundError: No module named 'x'\nerror: Microsoft Visual C++ 14.0 or greater is required"
    found = errors.classify(text, "python")
    assert [d.category for d in found] == ["environment", "test_defect"]
    assert errors.is_environmental(found)
    assert not errors.is_environmental(errors.classify("E   AssertionError: assert 1 == 2", "python"))
    assert not errors.is_environmental([])


def test_declared_dependency_import_error_is_the_environments_fault_only_with_context():
    text = "ModuleNotFoundError: No module named 'fastapi'"
    assert errors.classify(text, "python")[0].code == "EXEC-101"
    resolved = errors.classify(text, "python", errors.Context(missing_packages=["fastapi"]))[0]
    assert (resolved.code, resolved.category, resolved.detail["package"]) == (
        "ENV-PY-007",
        "environment",
        "fastapi",
    )


def test_diagnosis_carries_evidence_hint_recipes_and_autonomy():
    found = errors.classify("...\nERROR: No matching distribution found for foo==1\n...", "python")[0]
    assert found.evidence == "ERROR: No matching distribution found for foo==1"
    assert found.autonomy == "auto_once" and "relax-pin" in found.recipes and found.hint
    assert found.to_dict()["recipes"] == list(found.recipes)


def test_downloading_recipes_are_flagged_and_the_catalogue_renders():
    assert all(
        playbook.RECIPES[r].downloads for r in ("provision-jdk", "provision-dotnet", "install-browser")
    )
    assert "ENV-JAVA-001" in playbook.render_markdown()
    for (
        code
    ) in CODES.values():  # anything that downloads software must not run without the human (except browsers)
        if any(playbook.RECIPES[r].downloads for r in code.recipes):
            assert code.autonomy in {"ask", "auto"} and (
                code.autonomy == "ask" or code.code == "ENV-COMMON-005"
            )

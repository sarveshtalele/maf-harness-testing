import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from maf_test_harness.skills.contracts import (
    SKILL_NAMES,
    design_schema,
    ensure_generated_case_markers,
    finalize_design,
    finalize_discovered_requirements,
    validate_generated_files,
)

SKILLS = Path(__file__).parents[1] / "skills"


def _source():
    return {
        "requirement_model": {
            "requirements": [{"id": "REQ-1", "text": "User can sign in", "evidence": "Story line 1"}],
            "validation_rules": [{"id": "VAL-1", "text": "Email is required", "evidence": "Rule 1"}],
            "acceptance_criteria": [{"id": "AC-1", "text": "Dashboard appears", "requirement_id": "REQ-1"}],
        },
        "api_contracts": [{"id": "API-1", "path": "/login", "method": "POST", "status_codes": ["200"]}],
    }


def _document():
    def case(title, kind, link_type, link_id):
        return {
            "title": title,
            "type": kind,
            "priority": "P1",
            "expected_result": "Documented outcome occurs",
            "oracle": {"kind": "characterization", "source": None, "relation": None},
            "traceability_links": [{"type": link_type, "id": link_id}],
        }

    return {
        "metadata": {
            "generated_at": "2026-09-18T00:00:00Z",
            "generator_version": "1",
            "input_sources": ["requirements.json"],
            "tool": "test-design-generator",
        },
        "summary": {
            "total_tests_generated": 999,
            "positive_tests_count": 999,
            "negative_tests_count": 0,
            "boundary_tests_count": 0,
            "validation_tests_count": 0,
            "contract_tests_count": 0,
            "coverage_percentage": 0,
            "requirements_covered": 0,
            "total_requirements": 0,
            "test_distribution": {},
        },
        "positive_tests": [case("Dashboard shown", "positive", "acceptance_criterion", "AC-1")],
        "negative_tests": [],
        "boundary_tests": [],
        "validation_tests": [case("Missing email rejected", "validation", "validation", "VAL-1")],
        "contract_tests": [case("Login success", "contract", "api_contract", "API-1")],
        "traceability_matrix": {"requirement_to_tests": {}, "validation_to_tests": {}, "api_to_tests": {}},
        "coverage_analysis": {"requirement_coverage": {}, "validation_coverage": {}, "api_coverage": {}},
        "gaps_report": {
            "missing_validations": [],
            "incomplete_requirements": [],
            "coverage_gaps": [],
            "uncovered_requirements": [],
            "uncovered_validations": [],
            "notes": "",
        },
    }


def test_marketplace_mapping_and_schema_load():
    assert SKILL_NAMES == {
        "requirements": "repository-requirements",
        "test_planning": "test-planning",
        "test_design": "test-design-generator",
        "test_generation": "generic-test-generator",
    }
    assert design_schema(SKILLS)["required"][:2] == ["metadata", "summary"]


def test_finalize_discovered_requirements_assigns_stable_ids_and_preserves_evidence():
    draft = {
        "app_name": "Demo",
        "summary": "Static repository behavior",
        "requirements": [
            {
                "text": "Parser reads Python files",
                "evidence": "parsers.py handles .py",
                "source_files": ["engine/parsers.py"],
            }
        ],
        "exclusions": ["third-party code"],
        "assumptions": [],
        "gaps": [],
    }
    first = finalize_discovered_requirements(draft, "https://github.com/acme/demo")
    second = finalize_discovered_requirements(draft, "https://github.com/acme/demo")
    assert (
        first["requirement_model"]["requirements"][0]["id"]
        == second["requirement_model"]["requirements"][0]["id"]
    )
    assert first["requirement_model"]["requirements"][0]["source_files"] == ["engine/parsers.py"]
    assert first["requirement_model"]["requirements"][0]["id"].startswith("REQ-PARSER-READS-PYTHON-FILES-")


def test_finalize_design_assigns_order_independent_ids_and_rebuilds_derived_data():
    document = _document()
    result = finalize_design(document, _source(), "https://example.test/repo", SKILLS)
    reordered = deepcopy(document)
    reordered["positive_tests"].reverse()
    second = finalize_design(reordered, _source(), "https://example.test/repo", SKILLS)
    assert result["positive_tests"][0]["id"] == second["positive_tests"][0]["id"]
    assert result["summary"]["total_tests_generated"] == 3
    assert result["summary"]["test_distribution"] == {
        "boundary": 0,
        "contract": 1,
        "negative": 0,
        "positive": 1,
        "validation": 1,
    }
    positive_id = result["positive_tests"][0]["id"]
    assert positive_id.startswith("TC-POS-")
    assert "DASHBOARD-SHOWN" in positive_id
    assert result["traceability_matrix"]["requirement_to_tests"]["AC-1"] == [positive_id]
    assert result["coverage_analysis"]["requirement_coverage"]["REQ-1"]["covered"] is True
    assert result["summary"]["coverage_percentage"] == 100.0
    assert result["metadata"]["repository_url"] == "https://example.test/repo"


def test_finalize_design_ids_sort_links_ignore_volatile_case_metadata_and_include_repo():
    document = _document()
    case = document["positive_tests"][0]
    case["traceability_links"] = [
        {"type": "requirement", "id": "REQ-1"},
        {"type": "acceptance_criterion", "id": "AC-1"},
    ]
    case["metadata"] = {"generated_at": "first-run"}
    first = finalize_design(document, _source(), "https://example.test/repo", SKILLS)
    changed = deepcopy(document)
    changed["positive_tests"][0]["traceability_links"].reverse()
    changed["positive_tests"][0]["metadata"] = {"generated_at": "second-run"}
    second = finalize_design(changed, _source(), "https://example.test/repo", SKILLS)
    other_repo = finalize_design(document, _source(), "https://example.test/other", SKILLS)
    assert first["positive_tests"][0]["id"] == second["positive_tests"][0]["id"]
    assert first["positive_tests"][0]["traceability_links"] == sorted(
        first["positive_tests"][0]["traceability_links"], key=lambda link: (link["type"], link["id"])
    )
    assert first["positive_tests"][0]["id"] != other_repo["positive_tests"][0]["id"]


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d["positive_tests"][0]["traceability_links"][0].update(id="AC-404"), "unknown"),
        (lambda d: d["positive_tests"][0].update(expected_result=""), "schema"),
        (lambda d: d["positive_tests"][0].pop("title"), "schema"),
    ],
)
def test_finalize_design_rejects_unsupported_or_non_actionable_tests(mutate, match):
    document = _document()
    mutate(document)
    with pytest.raises(ValueError, match=match):
        finalize_design(document, _source(), "https://example.test/repo", SKILLS)


def test_finalize_design_rejects_contract_details_not_present_in_source():
    document = _document()
    document["contract_tests"][0].update(endpoint="/invented", method="DELETE")
    with pytest.raises(ValueError, match="unsupported contract endpoint"):
        finalize_design(document, _source(), "https://example.test/repo", SKILLS)


def test_finalize_design_rejects_empty_requirements_or_empty_design():
    source = _source()
    source["requirement_model"]["requirements"] = []
    source["requirement_model"]["acceptance_criteria"] = []
    with pytest.raises(ValueError, match="at least one requirement"):
        finalize_design(_document(), source, "https://example.test/repo", SKILLS)

    document = _document()
    for section in ("positive", "negative", "boundary", "validation", "contract"):
        document[f"{section}_tests"] = []
    with pytest.raises(ValueError, match="at least one actionable"):
        finalize_design(document, _source(), "https://example.test/repo", SKILLS)


def test_validate_generated_files_accepts_safe_complete_artifacts():
    result = validate_generated_files(
        {
            "files": [
                {
                    "path": "tests/login.spec.ts",
                    "content": "// CASE_ID: TC-1\ntest('login', async () => { expect(1).toBe(1); });",
                    "case_ids": ["TC-1"],
                }
            ]
        },
        {"TC-1"},
    )
    assert result == {
        "files": 1,
        "test_declarations": 1,
        "covered_case_ids": ["TC-1"],
        "uncovered_case_ids": [],
    }


def test_validate_design_helper_emits_finalized_json_without_writes(tmp_path):
    document_path = tmp_path / "document.json"
    source_path = tmp_path / "source.json"
    document_path.write_text(json.dumps(_document()), encoding="utf-8")
    source_path.write_text(json.dumps(_source()), encoding="utf-8")
    script = SKILLS / "test-design-generator" / "scripts" / "validate_design.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--input",
            str(document_path),
            "--source",
            str(source_path),
            "--repo-url",
            "https://example.test/repo",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["summary"]["total_tests_generated"] == 3
    assert sorted(path.name for path in tmp_path.iterdir()) == ["document.json", "source.json"]


@pytest.mark.parametrize(
    "file",
    [
        {"path": "../escape.py", "content": "pass", "case_ids": ["TC-1"]},
        {"path": ".env", "content": "TOKEN=x", "case_ids": ["TC-1"]},
        {"path": ".git/hooks/x", "content": "echo x", "case_ids": ["TC-1"]},
        {"path": "tests/x.py", "content": "# TODO implement test", "case_ids": ["TC-1"]},
        {
            "path": "tests/x.py",
            "content": "# CASE_ID: TC-404\ndef test_x():\n    assert actual_value() == 1\n",
            "case_ids": ["TC-404"],
        },
    ],
)
def test_validate_generated_files_rejects_unsafe_placeholder_or_unknown_cases(file):
    with pytest.raises(ValueError):
        validate_generated_files({"files": [file]}, {"TC-1"})


def test_generated_case_ids_must_be_implemented_in_source_not_only_metadata():
    payload = {
        "files": [
            {
                "path": "tests/test_claim.py",
                "content": "def test_test_harness_fixture():\n    assert True\n",
                "case_ids": ["TC-1", "TC-2"],
            }
        ]
    }
    with pytest.raises(ValueError):
        validate_generated_files(payload, {"TC-1", "TC-2"})


def test_host_injects_case_marker_for_one_case_per_file():
    payload = {
        "files": [
            {
                "path": "tests/test_clone.py",
                "content": "def test_clone():\n    assert actual_value() == 1\n",
                "case_ids": ["TC-POS-CLONE-ABC12345"],
            }
        ]
    }
    normalized = ensure_generated_case_markers(payload)
    assert "# CASE_ID: TC-POS-CLONE-ABC12345" in normalized["files"][0]["content"]
    result = validate_generated_files(normalized, {"TC-POS-CLONE-ABC12345"})
    assert result["uncovered_case_ids"] == []


def test_validate_generated_files_rejects_multiple_cases_claimed_by_one_file():
    payload = {
        "files": [
            {
                "path": "tests/test_claim.py",
                "content": "# CASE_ID: TC-1\n# CASE_ID: TC-2\ndef test_claim():\n    assert actual_value() == 1\n",
                "case_ids": ["TC-1", "TC-2"],
            }
        ]
    }
    with pytest.raises(ValueError, match="exactly one approved case ID"):
        validate_generated_files(payload, {"TC-1", "TC-2"})


def test_validate_generated_files_rejects_missing_case_marker():
    payload = {
        "files": [
            {
                "path": "tests/test_clone.py",
                "content": "def test_clone():\n    assert actual_value() == 1\n",
                "case_ids": ["TC-CLONE"],
            }
        ]
    }
    with pytest.raises(ValueError, match="missing CASE_ID markers"):
        validate_generated_files(payload, {"TC-CLONE"})


def test_validate_generated_files_rejects_invalid_python_source():
    payload = {
        "files": [
            {
                "path": "tests/test_invalid.py",
                "content": "# CASE_ID: TC-1\ndef test_bad(:\n    assert actual_value() == 1\n",
                "case_ids": ["TC-1"],
            }
        ]
    }
    with pytest.raises(ValueError, match="Python source is invalid"):
        validate_generated_files(payload, {"TC-1"})


def test_validate_generated_files_rejects_excessive_python_imports():
    imports = "".join(f"import module_{i}\n" for i in range(21))
    payload = {
        "files": [
            {
                "path": "tests/test_imports.py",
                "content": imports + "# CASE_ID: TC-1\ndef test_imports():\n    assert actual_value() == 1\n",
                "case_ids": ["TC-1"],
            }
        ]
    }
    with pytest.raises(ValueError, match="excessive imports"):
        validate_generated_files(payload, {"TC-1"})


def test_contract_cases_without_api_contracts_are_reclassified_to_validation(tmp_path):
    from maf_test_harness.skills.contracts import finalize_design

    source = {
        "app_name": "demo",
        "requirement_model": {
            "requirements": [
                {
                    "id": "REQ-CLI",
                    "text": "Expose CLI behavior",
                    "evidence": "README",
                    "source_files": ["README.md"],
                }
            ],
            "validation_rules": [],
            "acceptance_criteria": [],
        },
        "api_contracts": [],
    }
    design = {
        "positive_tests": [],
        "negative_tests": [],
        "boundary_tests": [],
        "validation_tests": [],
        "contract_tests": [
            {
                "title": "CLI behavior remains stable",
                "priority": "P1",
                "description": "Verify observable CLI behavior.",
                "expected_result": "CLI behavior matches requirement.",
                "oracle": {"kind": "characterization", "source": None, "relation": None},
                "steps": ["Invoke CLI", "Verify result"],
                "traceability_links": [{"type": "requirement", "id": "REQ-CLI"}],
            }
        ],
        "gaps": [],
    }

    # Use the real bundled schema location.
    skills_root = Path(__file__).resolve().parents[1] / "skills"
    result = finalize_design(design, source, "https://github.com/example/demo", skills_root)

    assert result["contract_tests"] == []
    assert len(result["validation_tests"]) == 1
    assert result["validation_tests"][0]["type"] == "validation"
    assert result["summary"]["contract_tests_count"] == 0
    assert result["summary"]["validation_tests_count"] == 1
    assert "reclassified 1 contract test" in result["gaps_report"]["notes"]


def test_contract_case_with_explicit_api_contract_remains_contract():
    from maf_test_harness.skills.contracts import finalize_design

    source = {
        "app_name": "demo",
        "requirement_model": {
            "requirements": [
                {
                    "id": "REQ-API",
                    "text": "Expose health API",
                    "evidence": "OpenAPI",
                    "source_files": ["openapi.yaml"],
                }
            ],
            "validation_rules": [],
            "acceptance_criteria": [],
        },
        "api_contracts": [{"id": "API-HEALTH", "path": "/health", "method": "GET", "status_codes": [200]}],
    }
    design = {
        "positive_tests": [],
        "negative_tests": [],
        "boundary_tests": [],
        "validation_tests": [],
        "contract_tests": [
            {
                "title": "Health endpoint contract",
                "priority": "P1",
                "description": "Verify documented health endpoint.",
                "expected_result": "GET /health returns 200.",
                "oracle": {"kind": "characterization", "source": None, "relation": None},
                "steps": ["Call GET /health", "Verify 200"],
                "traceability_links": [
                    {"type": "requirement", "id": "REQ-API"},
                    {"type": "api_contract", "id": "API-HEALTH"},
                ],
                "endpoint": "/health",
                "method": "GET",
                "expected_status_codes": ["200"],
            }
        ],
        "gaps": [],
    }

    skills_root = Path(__file__).resolve().parents[1] / "skills"
    result = finalize_design(design, source, "https://github.com/example/demo", skills_root)

    assert len(result["contract_tests"]) == 1
    assert result["contract_tests"][0]["type"] == "contract"
    assert result["summary"]["contract_tests_count"] == 1

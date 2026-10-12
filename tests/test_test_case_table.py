from maf_test_harness.qa.report import (
    CASE_COLUMNS,
    build_qa_report,
    case_requirement_ids,
    case_table_rows,
    render_case_table,
)
from maf_test_harness.ui.evidence_api import render_test_design_markdown

HEADER = "| Sno | Requirement ID | Test Case ID | Test Case Description | Steps | Expected Result |"


def _design():
    return {
        "positive_tests": [
            {
                "id": "TC-1",
                "priority": "P1",
                "level": "unit",
                "actor": "system",
                "target": "login",
                "title": "Login works",
                "description": "Valid credentials reach the dashboard",
                "steps": ["Open login", "Submit valid password"],
                "expected_result": "Dashboard shown",
                "traceability_links": [
                    {"type": "requirement", "id": "REQ-1"},
                    {"type": "requirement", "id": "REQ-2"},
                    {"type": "validation", "id": "VAL-1"},
                ],
            },
            {
                "id": "TC-2",
                "title": "Second",
                "description": "Second",
                "expected_result": "Ok",
                "traceability_links": [{"type": "requirement", "id": "REQ-3"}],
            },
        ],
        "negative_tests": [
            {
                "id": "TC-3",
                "title": "Bad password",
                "description": "Bad password",
                "steps": ["Submit wrong | password"],
                "expected_result": "Error",
                "traceability_links": [{"type": "requirement", "id": "REQ-1"}],
            }
        ],
        "boundary_tests": [],
        "gaps_report": {},
    }


def test_columns_are_the_six_required_in_order():
    assert CASE_COLUMNS == (
        "Sno",
        "Requirement ID",
        "Test Case ID",
        "Test Case Description",
        "Steps",
        "Expected Result",
    )


def test_rows_group_by_category_and_restart_sno():
    rows = case_table_rows(_design())
    assert [(r["group"], r["sno"], r["id"]) for r in rows] == [
        ("positive", 1, "TC-1"),
        ("positive", 2, "TC-2"),
        ("negative", 1, "TC-3"),
    ]


REQUIREMENTS = {
    "requirement_model": {
        "acceptance_criteria": [{"id": "AC-1", "text": "x", "requirement_id": "REQ-9"}],
    }
}


def test_requirement_ids_come_only_from_requirement_links_and_are_comma_joined():
    rows = case_table_rows(_design())
    assert rows[0]["requirement_ids"] == ["REQ-1", "REQ-2"]
    assert rows[1]["requirement_ids"] == ["REQ-3"]


def test_acceptance_criterion_only_case_resolves_to_its_parent_requirement():
    case = {"traceability_links": [{"type": "acceptance_criterion", "id": "AC-1"}]}
    assert case_requirement_ids(case, REQUIREMENTS) == ["REQ-9"]
    assert case_requirement_ids(case) == ["AC-1"]  # no artifact: raw link id, never a blank cell


def test_direct_requirement_and_criterion_parent_are_merged_without_duplicates():
    case = {
        "traceability_links": [
            {"type": "requirement", "id": "REQ-9"},
            {"type": "acceptance_criterion", "id": "AC-1"},
        ]
    }
    assert case_requirement_ids(case, REQUIREMENTS) == ["REQ-9"]


def test_validation_and_api_contract_fall_back_to_raw_link_ids():
    case = {
        "traceability_links": [
            {"type": "validation", "id": "VAL-4"},
            {"type": "api_contract", "id": "API-2"},
        ]
    }
    assert case_requirement_ids(case, REQUIREMENTS) == ["VAL-4", "API-2"]


def test_links_without_ids_do_not_crash_or_render_none():
    case = {"traceability_links": [{"type": "requirement"}, {"type": "validation", "id": ""}, "junk"]}
    assert case_requirement_ids(case, REQUIREMENTS) == []


def test_details_list_keeps_reviewer_fields():
    design = _design()
    design["positive_tests"][0]["preconditions"] = ["Account exists"]
    design["positive_tests"][0]["data_notes"] = "user@example.test"
    text = render_case_table(case_table_rows(design))
    assert (
        "- TC-1: priority P1, level unit, actor system, target login. Preconditions: Account exists." in text
    )
    assert "Preconditions: Account exists." in text
    assert "Data: user@example.test." in text
    assert "Links: requirement:REQ-1, requirement:REQ-2, validation:VAL-1." in text


def test_missing_steps_from_older_runs_render_as_dash():
    rows = case_table_rows(_design())
    assert rows[1]["steps"] == []
    assert "| 2 | REQ-3 | TC-2 | Second | — | Ok |" in render_case_table(rows)


def test_markdown_table_order_skips_empty_groups_and_numbers_steps():
    text = render_case_table(case_table_rows(_design()))
    assert text.count(HEADER) == 2  # positive and negative only; boundary is empty
    assert "Boundary" not in text
    assert (
        "| 1 | REQ-1, REQ-2 | TC-1 | Login works — Valid credentials reach the dashboard | 1) Open login 2) Submit valid password | Dashboard shown |"
        in text
    )
    # Pipes in steps cannot split the row.
    assert "1) Submit wrong \\| password" in text


def test_design_export_puts_the_case_table_first_and_keeps_every_field():
    design = _design()
    design["contract_tests"] = [{"id": "TC-9", "endpoint": "/orders", "traceability_links": []}]
    text = render_test_design_markdown(design, REQUIREMENTS)
    assert (
        text.index("## Test cases") < text.index("### Positive cases") < text.index("## Full design record")
    )
    assert "/orders" in text  # fields outside the table stay in the full record
    assert "Positive Tests" in text  # the full record keeps every section


def test_qa_report_carries_test_cases_and_renders_the_table():
    report = build_qa_report(
        repo={"name": "demo"},
        requirements={},
        design=_design(),
        generation={},
        execution=None,
    )
    assert [r["id"] for r in report["test_cases"]] == ["TC-1", "TC-2", "TC-3"]
    from maf_test_harness.qa.report import render_markdown

    markdown = render_markdown(report)
    assert "## Test cases" in markdown
    assert HEADER in markdown

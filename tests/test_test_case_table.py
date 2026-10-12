from maf_test_harness.qa.report import CASE_COLUMNS, build_qa_report, case_table_rows, render_case_table
from maf_test_harness.ui.evidence_api import render_test_design_markdown

HEADER = "| Sno | Requirement ID | Test Case ID | Test Case Description | Steps | Expected Result |"


def _design():
    return {
        "positive_tests": [
            {
                "id": "TC-1",
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


def test_requirement_ids_come_only_from_requirement_links_and_are_comma_joined():
    rows = case_table_rows(_design())
    assert rows[0]["requirement_id"] == "REQ-1, REQ-2"
    assert rows[1]["requirement_id"] == "REQ-3"


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


def test_design_export_puts_the_case_table_first_and_keeps_other_sections():
    text = render_test_design_markdown(_design())
    assert text.index("## Test cases") < text.index("### Positive cases") < text.index("## Gaps Report")
    assert "positive_tests" not in text


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

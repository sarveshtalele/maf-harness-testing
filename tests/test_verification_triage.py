"""Verification triage (accuracy 7.4): deterministic product / test / environment / unresolved classes, the verified
definition and the ledger record. No model is called."""

from maf_test_harness.qa import ledger
from maf_test_harness.qa.report import build_qa_report, oracle_summary, render_markdown
from maf_test_harness.qa.verification import (
    ENVIRONMENT,
    PRODUCT_DEFECT,
    TEST_DEFECT,
    UNRESOLVED,
    classify_failure,
    documented_quote,
    is_verified,
    summarize_triage,
    triage_cases,
)

ASSERT = "AssertionError: assert 3 == 4\nE       assert 3 == 4\n"
DOC = "add(a, b) returns the sum of its two integer arguments, so add(1, 2) is 3."


def test_documented_failure_with_a_quote_stating_the_expected_value_is_a_product_defect():
    record = classify_failure(ASSERT, oracle="specified", quote=DOC.replace("is 3", "is 4"))
    assert record["classification"] == PRODUCT_DEFECT
    assert (record["actual"], record["expected"]) == ("3", "4")
    assert record["documented_quote"]


def test_golden_oracle_counts_as_documented():
    quote = "The fixture answer for add(1, 2) is 4."
    assert classify_failure(ASSERT, oracle="golden", quote=quote)["classification"] == PRODUCT_DEFECT


def test_a_documented_oracle_whose_quote_states_neither_value_is_unresolved_not_a_test_defect():
    record = classify_failure(ASSERT, oracle="specified", quote="add sums two numbers.")
    assert record["classification"] == UNRESOLVED


def test_only_a_derived_expectation_is_a_test_defect_without_a_documented_value():
    assert (
        classify_failure(ASSERT, oracle="derived", quote="add sums two numbers.")["classification"]
        == TEST_DEFECT
    )
    for oracle in ("characterization", ""):
        assert classify_failure(ASSERT, oracle=oracle, quote="")["classification"] == UNRESOLVED


def test_a_derived_expectation_that_conflicts_with_a_quote_is_unresolved():
    assert classify_failure(ASSERT, oracle="derived", quote="add(1, 2) is 4")["classification"] == UNRESOLVED


def test_environment_causes_come_from_the_error_taxonomy_first():
    message = "E   RuntimeError: The starlette.testclient module requires the httpx2 package to be installed."
    record = classify_failure(message, oracle="specified", quote=DOC, ecosystem="python")
    assert record["classification"] == ENVIRONMENT
    assert record["diagnoses"]


def test_failures_without_an_actual_and_expected_pair_are_unresolved():
    record = classify_failure("ValueError: boom inside the application", oracle="specified", quote=DOC)
    assert record["classification"] == UNRESOLVED


def test_other_oracle_kinds_are_not_decided_by_the_rules():
    assert classify_failure(ASSERT, oracle="metamorphic", quote=DOC)["classification"] == UNRESOLVED
    assert classify_failure(ASSERT, oracle="property", quote=DOC)["classification"] == UNRESOLVED


def test_only_executed_failures_are_triaged_and_counted():
    cases = [
        {"id": "TC-1", "status": "failed", "message": ASSERT, "oracle": "derived"},
        {"id": "TC-2", "status": "passed", "message": ""},
    ]
    rows = triage_cases(cases)
    assert [r["case_id"] for r in rows] == ["TC-1"]
    assert summarize_triage(rows)[TEST_DEFECT] == 1


def test_verified_needs_both_review_acceptance_and_execution_pass():
    assert is_verified("accepted", "passed")
    assert not is_verified("accepted", "failed")
    assert not is_verified("rejected", "passed")
    assert not is_verified("not_reviewed", "passed")


def test_documented_quote_reads_the_first_known_key_and_tolerates_old_cases():
    assert documented_quote({"oracle_quote": "  a   b "}) == "a b"
    assert documented_quote({"expectation_quote": "x"}) == "x"
    assert documented_quote({"title": "old case"}) == ""


def _design(**extra):
    case = {
        "id": "TC-1",
        "title": "add sums",
        "type": "positive",
        "traceability_links": [],
        **extra,
    }
    return {"positive_tests": [case], "traceability_matrix": {}}


def _report(design, status="failed", message=ASSERT, review="accepted"):
    gen = {
        "files": [{"path": "tests/test_x.py", "content": "x", "case_ids": ["TC-1"]}],
        "case_reviews": [
            {
                "case_id": "TC-1",
                "verdict": "supported" if review == "accepted" else "needs_revision",
                "confidence": 97 if review == "accepted" else 40,
                "reason": "r",
            }
        ],
    }
    execution = {
        "status": "failed" if status == "failed" else "passed",
        "adapter": "python-pytest",
        "cases": [
            {
                "name": "test_x",
                "classname": "tests.test_x",
                "status": status,
                "message": message,
                "file": "tests/test_x.py",
            }
        ],
    }
    return build_qa_report(
        repo={"source_files": ["service.py"]},
        requirements={},
        design=design,
        generation=gen,
        execution=execution,
    )


def test_report_carries_triage_per_case_and_separate_verification_counts():
    qa = _report(_design(oracle="specified", oracle_quote=DOC.replace("is 3", "is 4"), tier="product"))
    case = qa["cases"][0]
    assert case["triage"]["classification"] == PRODUCT_DEFECT
    assert case["tier"] == "product" and case["oracle"] == "specified"
    assert case["verified"] is False  # failed execution is never verified
    assert qa["verification_counts"] == {
        "review_accepted": 1,
        "execution_passed": 0,
        "verified": 0,
        "characterization": 0,
    }
    assert qa["triage_summary"][PRODUCT_DEFECT] == 1
    assert qa["case_verification"]["TC-1"]["verified"] is False


def test_passing_and_accepted_case_is_verified_and_not_triaged():
    qa = _report(_design(oracle="derived"), status="passed", message="")
    case = qa["cases"][0]
    assert case["triage"] is None and case["verified"] is True
    assert qa["verification_counts"] == {
        "review_accepted": 1,
        "execution_passed": 1,
        "verified": 1,
        "characterization": 0,
    }


def test_review_rejected_pass_is_not_verified():
    qa = _report(_design(oracle="derived"), status="passed", message="", review="rejected")
    assert qa["cases"][0]["verified"] is False
    assert qa["verification_counts"]["verified"] == 0


def test_old_runs_without_oracle_fields_still_render():
    qa = _report(_design())
    assert qa["cases"][0]["oracle"] is None and qa["cases"][0]["tier"] is None
    assert "Verdict" in render_markdown(qa)


def test_execution_triage_records_go_to_the_ledger(tmp_path):
    record = ledger.append_event(
        tmp_path,
        kind="execution_triage",
        subject_id="TC-1",
        epoch=0,
        data={"classification": TEST_DEFECT, "reason": "r" * 900, "attempt": 1},
    )
    lines = (tmp_path / ledger.LEDGER).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert record["kind"] == "execution_triage" and record["classification"] == TEST_DEFECT
    assert len(record["reason"]) <= ledger.TEXT_LIMIT
    # Event records carry no score, so they never count as a gate subject.
    assert ledger.entries(tmp_path)[0]["subject_id"] == "TC-1"


class _Ctx:
    def __init__(self):
        self.sent = []

    async def send_message(self, state):
        self.sent.append(state)


def _heal_state(qa):
    from maf_test_harness.workflow_support import EXECUTION_STAGE

    return {
        "heal_rounds": 0,
        "artifacts": {EXECUTION_STAGE: {"qa_report": qa}, "test_generation": {"files": []}},
        "approved": {},
    }


def _heal(state):
    import asyncio

    from maf_test_harness.workflow_steps.execution import ExecutionSteps

    class Host(ExecutionSteps):
        def audit(self, event, **details):
            return None

    return asyncio.run(Host().heal(state, "", _Ctx()))


def test_heal_regenerates_only_test_defects_and_records_the_round():
    qa = {
        "cases": [
            {"id": "TC-1", "title": "guessed", "status": "failed", "message": ASSERT, "file": "t.py"},
            {"id": "TC-2", "title": "documented", "status": "failed", "message": ASSERT, "file": "u.py"},
        ],
        "triage": [
            {"case_id": "TC-1", "status": "failed", "classification": TEST_DEFECT, "reason": "guessed"},
            {"case_id": "TC-2", "status": "failed", "classification": PRODUCT_DEFECT, "reason": "documented"},
        ],
    }
    state = _heal_state(qa)
    _heal(state)
    assert state["heal"]["failed_case_ids"] == ["TC-1"]
    assert state["heal_history"] == [["TC-1"]]
    assert "TC-2" not in state["feedback"]["test_generation"]
    assert "TRIAGE: test defect" in state["feedback"]["test_generation"]


def test_heal_refuses_when_only_product_defects_or_unresolved_failures_remain():
    import pytest

    qa = {
        "cases": [
            {"id": "TC-2", "title": "documented", "status": "failed", "message": ASSERT, "file": "u.py"}
        ],
        "triage": [{"case_id": "TC-2", "status": "failed", "classification": PRODUCT_DEFECT, "reason": "d"}],
    }
    with pytest.raises(ValueError, match="No test defects to regenerate"):
        _heal(_heal_state(qa))


def test_old_runs_without_triage_keep_the_previous_heal_selection():
    qa = {"cases": [{"id": "TC-1", "title": "old", "status": "failed", "message": ASSERT, "file": "t.py"}]}
    state = _heal_state(qa)
    _heal(state)
    assert state["heal"]["failed_case_ids"] == ["TC-1"]


def test_behaviour_sections_keep_product_component_and_characterization_apart():
    design = {
        "positive_tests": [
            {
                "id": "TC-1",
                "title": "product",
                "type": "positive",
                "traceability_links": [],
                "tier": "product",
                "oracle": "specified",
            },
            {
                "id": "TC-2",
                "title": "component",
                "type": "positive",
                "traceability_links": [],
                "tier": "component",
                "oracle": "derived",
            },
            {
                "id": "TC-3",
                "title": "today",
                "type": "positive",
                "traceability_links": [],
                "tier": "product",
                "oracle": "characterization",
            },
        ],
        "traceability_matrix": {},
    }
    gen = {
        "files": [{"path": "tests/test_x.py", "content": "x", "case_ids": ["TC-1", "TC-2", "TC-3"]}],
        "case_reviews": [
            {"case_id": cid, "verdict": "supported", "confidence": 97, "reason": "r"}
            for cid in ("TC-1", "TC-2", "TC-3")
        ],
    }
    execution = {
        "status": "passed",
        "adapter": "python-pytest",
        "cases": [
            {
                "name": f"test_{cid}",
                "classname": "tests.test_x",
                "status": "passed",
                "message": "",
                "file": "tests/test_x.py",
            }
            for cid in ("TC-1", "TC-2", "TC-3")
        ],
    }
    qa = build_qa_report(
        repo={"source_files": []}, requirements={}, design=design, generation=gen, execution=execution
    )
    ids = {section["id"]: [c["case_id"] for c in section["cases"]] for section in qa["behaviour"]}
    assert list(ids) == ["product", "component", "characterization"]
    assert ids["product"] == ["TC-1"] and ids["component"] == ["TC-2"] and ids["characterization"] == ["TC-3"]
    characterization = qa["behaviour"][2]["cases"][0]
    assert characterization["verified"] is False and "never verified" in characterization["label"]
    assert qa["behaviour"][0]["cases"][0]["verified"] is True
    md = render_markdown(qa)
    assert (
        md.index("### Product behaviour")
        < md.index("### Component behaviour")
        < md.index("### Characterization")
    )
    assert "## Behaviour" in md


def test_failure_split_names_product_defects_and_repaired_or_still_failing_test_defects():
    qa = _report(_design(oracle="specified", oracle_quote="add(1, 2) is 4", tier="product"))
    split = qa["failure_split"]
    assert [p["case_id"] for p in split["product_defects"]] == ["TC-1"]
    assert split["product_defects"][0]["expected"] == "4" and split["product_defects"][0]["actual"] == "3"
    assert split["test_defects"] == {"auto_repaired": [], "still_failing": []}
    md = render_markdown(qa)
    assert "### Product defects (with documented evidence)" in md
    assert "### Test defects (auto-repaired / still failing)" in md


def test_auto_repaired_test_defects_are_listed_once_they_pass():
    qa_before = _report(_design(oracle="derived"))
    assert qa_before["failure_split"]["test_defects"]["still_failing"][0]["case_id"] == "TC-1"
    gen = {
        "files": [{"path": "tests/test_x.py", "content": "x", "case_ids": ["TC-1"]}],
        "case_reviews": [{"case_id": "TC-1", "verdict": "supported", "confidence": 97, "reason": "r"}],
    }
    execution = {
        "status": "passed",
        "adapter": "python-pytest",
        "cases": [
            {
                "name": "test_x",
                "classname": "tests.test_x",
                "status": "passed",
                "message": "",
                "file": "tests/test_x.py",
            }
        ],
    }
    qa = build_qa_report(
        repo={"source_files": []},
        requirements={},
        design=_design(oracle="derived"),
        generation=gen,
        execution=execution,
        repaired=["TC-1"],
    )
    assert [r["case_id"] for r in qa["failure_split"]["test_defects"]["auto_repaired"]] == ["TC-1"]
    assert qa["failure_split"]["test_defects"]["still_failing"] == []


def test_oracle_summary_without_oracle_fields_counts_every_case_as_missing():
    design = {
        "positive_tests": [{"id": "TC-1", "tier": "product"}],
        "negative_tests": [{"id": "TC-2"}],
        "traceability_matrix": {},
    }
    summary = oracle_summary(design, {})
    assert summary["total_cases"] == 2 and summary["by_kind"]["missing"] == 2
    assert summary["verified_cases"] == 0 and summary["characterization_cases"] == []


def test_dict_shaped_oracle_is_read_and_its_verified_flag_counts():
    from maf_test_harness.qa.verification import counts_as_verified, oracle_kind

    case = {
        "id": "TC-9",
        "oracle": {"kind": "specified", "source": "README.md:4", "verified": True, "quote": DOC},
    }
    assert oracle_kind(case) == "specified"
    assert counts_as_verified(case) is True
    assert documented_quote(case) == " ".join(DOC.split())
    characterization = {"id": "TC-8", "oracle": {"kind": "characterization", "verified": True}}
    assert counts_as_verified(characterization) is False


def test_characterization_is_excluded_from_verified_and_passed_counts_and_listed_apart():
    design = {
        "positive_tests": [
            {
                "id": "TC-1",
                "title": "documented",
                "type": "positive",
                "traceability_links": [],
                "oracle": {"kind": "specified", "verified": True},
            },
            {
                "id": "TC-2",
                "title": "today",
                "type": "positive",
                "traceability_links": [],
                "oracle": {"kind": "characterization", "verified": True},
            },
        ],
        "traceability_matrix": {},
    }
    gen = {
        "files": [{"path": "tests/test_x.py", "content": "x", "case_ids": ["TC-1", "TC-2"]}],
        "case_reviews": [
            {"case_id": cid, "verdict": "supported", "confidence": 97, "reason": "r"}
            for cid in ("TC-1", "TC-2")
        ],
    }
    execution = {
        "status": "passed",
        "adapter": "python-pytest",
        "cases": [
            {
                "name": f"test_{cid}",
                "classname": "tests.test_x",
                "status": "passed",
                "message": "",
                "file": "tests/test_x.py",
            }
            for cid in ("TC-1", "TC-2")
        ],
    }
    qa = build_qa_report(
        repo={"source_files": []}, requirements={}, design=design, generation=gen, execution=execution
    )
    assert qa["verification_counts"] == {
        "review_accepted": 1,
        "execution_passed": 1,
        "verified": 1,
        "characterization": 1,
    }
    assert qa["case_verification"]["TC-2"]["verified"] is False
    characterization = qa["behaviour"][2]
    assert characterization["title"] == "Characterization (current behaviour only)"
    assert [c["case_id"] for c in characterization["cases"]] == ["TC-2"]
    assert "### Characterization (current behaviour only)" in render_markdown(qa)


def test_report_oracle_summary_is_the_qa_oracles_summary():
    requirements = {
        "requirement_model": {
            "requirements": [
                {"id": "REQ-1", "tier": "product", "status": "gap"},
                {"id": "REQ-2", "tier": "product"},
                {"id": "REQ-3"},
            ],
            "acceptance_criteria": [],
        }
    }
    design = {
        "positive_tests": [
            {
                "id": "TC-1",
                "traceability_links": [{"type": "requirement", "id": "REQ-2"}],
                "oracle": {"kind": "specified", "verified": True},
            },
            {
                "id": "TC-2",
                "traceability_links": [{"type": "requirement", "id": "REQ-3"}],
                "oracle": {"kind": "characterization", "verified": True},
            },
        ],
        "negative_tests": [{"id": "TC-3", "traceability_links": [{"type": "requirement", "id": "REQ-3"}]}],
        "traceability_matrix": {},
    }
    summary = oracle_summary(design, requirements)
    assert summary["total_cases"] == 3
    assert set(summary["by_kind"]) == {
        "specified",
        "derived",
        "metamorphic",
        "golden",
        "property",
        "characterization",
        "missing",
    }
    assert summary["by_kind"]["specified"] == 1 and summary["by_kind"]["missing"] == 1
    assert summary["verified_cases"] == 1
    assert summary["characterization_cases"] == ["TC-2"]
    assert summary["requirements_by_tier"] == {"product": 2, "component": 1}
    assert summary["cases_by_tier"] == {"product": 1, "component": 2}
    assert summary["product_requirements"] == {
        "total": 2,
        "with_cases": ["REQ-2"],
        "without_cases": [],
        "gap": ["REQ-1"],
    }


def test_documented_quote_reads_the_oracle_source_quote():
    from maf_test_harness.qa.verification import documented_quote

    case = {"oracle": {"kind": "specified", "source": {"file": "README.md", "quote": "returns  3"}}}
    assert documented_quote(case) == "returns 3"

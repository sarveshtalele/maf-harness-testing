"""Test oracles: every design case states an oracle; the host verifies quotes; characterization is never verified."""

import copy
from pathlib import Path

import pytest

from maf_test_harness.qa.oracles import (
    ORACLE_KINDS,
    check_oracles,
    counts_as_verified,
    file_reader,
    oracle_shape_problems,
    oracle_summary,
)
from maf_test_harness.skills.contracts import finalize_design
from maf_test_harness.workflow_steps.design_review import score_case

SKILLS = Path(__file__).parents[1] / "skills"
URL = "https://example.test/repo"
SOURCE_QUOTE = "return a + b"
README_QUOTE = "The tool should report every finding with a line number."


def _source():
    return {
        "requirement_model": {
            "requirements": [
                {
                    "id": "REQ-1",
                    "text": "add sums",
                    "evidence": "service.py",
                    "tier": "component",
                    "status": "verified",
                },
                {
                    "id": "REQ-2",
                    "text": "report findings",
                    "evidence": "README.md",
                    "tier": "product",
                    "status": "verified",
                },
            ],
            "validation_rules": [],
            "acceptance_criteria": [],
        },
        "api_contracts": [],
    }


def _oracle(kind="derived", source=None, relation=None):
    if source is None and kind in ("specified", "derived", "golden"):
        source = {"file": "service.py", "line": 2, "quote": SOURCE_QUOTE}
    return {"kind": kind, "source": source, "relation": relation}


def _case(oracle, title="add returns sum", link="REQ-1"):
    case = {
        "title": title,
        "priority": "P1",
        "level": "unit",
        "actor": "System",
        "target": "service.add",
        "description": "d",
        "preconditions": [],
        "data_notes": "1 and 2",
        "expected_result": "the sum of the two inputs is returned",
        "steps": ["call"],
        "traceability_links": [{"type": "requirement", "id": link}],
    }
    if oracle is not None:
        case["oracle"] = oracle
    return case


def _design(positive=(), negative=()):
    return {
        "positive_tests": list(positive),
        "negative_tests": list(negative),
        "boundary_tests": [],
        "validation_tests": [],
        "contract_tests": [],
        "gaps": [],
    }


def _repo(tmp_path):
    (tmp_path / "service.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(f"# Demo\n\n{README_QUOTE}\n", encoding="utf-8")
    return file_reader(tmp_path)


def test_every_kind_is_accepted_by_design_validation(tmp_path):
    cases = [
        _case(_oracle("derived"), "derived"),
        _case(_oracle("specified", {"file": "README.md", "line": 3, "quote": README_QUOTE}), "specified"),
        _case(
            _oracle("metamorphic", relation="adding an irrelevant file does not change the top candidate"),
            "meta",
        ),
        _case(
            _oracle("golden", relation="fixture of two files; score 3 by the documented weights"), "golden"
        ),
        _case(_oracle("property", relation="every score is between 0 and 1"), "property"),
        _case(_oracle("characterization"), "characterization"),
    ]
    assert {c["oracle"]["kind"] for c in cases} == set(ORACLE_KINDS)
    for case in cases:
        assert oracle_shape_problems(case) == [], case["title"]
    result = finalize_design(_design(positive=cases), _source(), URL, SKILLS)
    assert len(result["positive_tests"]) == len(ORACLE_KINDS)


def test_design_rejects_missing_oracle():
    with pytest.raises(ValueError, match="oracle"):
        finalize_design(_design(positive=[_case(None)]), _source(), URL, SKILLS)


@pytest.mark.parametrize(
    "oracle, message",
    [
        ({"kind": "specified", "source": None, "relation": None}, "source file and a verbatim quote"),
        (
            _oracle("derived", source={"file": "service.py", "line": 2, "quote": ""}),
            "source file and a verbatim quote",
        ),
        (_oracle("metamorphic", relation=None), "stated relation"),
        (
            _oracle("golden", source={"file": "README.md", "line": 1, "quote": "rule"}, relation="short"),
            "stated relation",
        ),
        (_oracle("property", relation=""), "stated relation"),
        ({"kind": "guessed", "source": None, "relation": None}, "not one of"),
    ],
)
def test_design_rejects_oracle_without_required_source_or_relation(oracle, message):
    with pytest.raises(ValueError, match=message):
        finalize_design(_design(positive=[_case(oracle)]), _source(), URL, SKILLS)


def test_quote_must_be_verbatim_in_its_file(tmp_path):
    read = _repo(tmp_path)
    cases = [
        _case(_oracle("derived")),
        _case(_oracle("derived", {"file": "service.py", "line": 2, "quote": "return a - b"})),
        _case(_oracle("specified", {"file": "README.md", "line": 3, "quote": README_QUOTE})),
        _case(
            _oracle("specified", {"file": "README.md", "line": 3, "quote": "a rule nobody wrote"}), "bad spec"
        ),
    ]
    problems = check_oracles(cases, read)
    assert [c["oracle"]["verified"] for c in cases] == [True, False, True, False]
    assert len(problems) == 2 and all("not verbatim" in p for p in problems)


def test_specified_may_quote_the_approved_requirement_corpus(tmp_path):
    read = _repo(tmp_path)
    spec = _case(_oracle("specified", {"file": "spec.md", "line": 1, "quote": "Totals are rounded half up"}))
    assert check_oracles([spec], read, corpus="Totals are rounded half up.") == []
    assert spec["oracle"]["verified"] is True
    derived = _case(_oracle("derived", {"file": "spec.md", "line": 1, "quote": "Totals are rounded half up"}))
    assert check_oracles([derived], read, corpus="Totals are rounded half up.") != []
    assert derived["oracle"]["verified"] is False


def test_characterization_is_never_verified(tmp_path):
    read = _repo(tmp_path)
    case = _case(_oracle("characterization"))
    assert check_oracles([case], read) == []
    assert case["oracle"]["verified"] is False
    assert counts_as_verified(case) is False
    golden = _case(_oracle("derived"))
    check_oracles([golden], read)
    assert counts_as_verified(golden) is True


def test_reviewer_counts_verified_oracle_as_derivable(tmp_path):
    read = _repo(tmp_path)
    case = _case(_oracle("derived"))
    check_oracles([case], read)
    review = {
        "case_id": "C1",
        "scores": {
            "source_proven": 30,
            "correct_target": 25,
            "expected_derivable": 25,
            "fixture_validity": 20,
        },
        "verdict": "approved",
        "derivation": "returns a + b",
        "source_quote": "",
        "reason": "ok",
    }
    source = "def add(a, b):\n    return a + b\n"
    scored = score_case(review, case, source=source, threshold=95)
    assert scored["oracle"] == "derived"
    assert scored["confidence"] == 100

    characterization = _case(_oracle("characterization"))
    check_oracles([characterization], read)
    scored = score_case(review, characterization, source=source, threshold=95)
    assert any(note["rule"] == "characterization" for note in scored["notes"])
    assert scored["confidence"] < 100 and scored["accepted"] is False


def test_oracle_summary_counts_kinds_tiers_and_product_coverage():
    cases = [
        {**_case(_oracle("derived"), "a", "REQ-1"), "id": "A", "type": "positive"},
        {**_case(_oracle("characterization"), "b", "REQ-1"), "id": "B", "type": "positive"},
        {
            **_case({"kind": "specified", "source": None, "relation": None}, "c", "REQ-2"),
            "id": "C",
            "type": "negative",
        },
    ]
    cases[2]["oracle"]["verified"] = True
    design = _design(positive=cases[:2], negative=cases[2:])
    summary = oracle_summary(design, _source())

    assert summary["total_cases"] == 3
    assert summary["by_kind"]["derived"] == 1 and summary["by_kind"]["characterization"] == 1
    assert summary["by_kind"]["missing"] == 0
    assert summary["characterization_cases"] == ["B"]
    assert (
        summary["verified_cases"] == 1
    )  # only the specified case is marked verified; the derived case was never checked
    assert summary["requirements_by_tier"] == {"product": 1, "component": 1}
    assert summary["cases_by_tier"] == {"product": 1, "component": 2}
    assert summary["product_requirements"] == {
        "total": 1,
        "with_cases": ["REQ-2"],
        "without_cases": [],
        "gap": [],
    }


def test_oracle_summary_handles_product_requirement_without_cases():
    source = copy.deepcopy(_source())
    summary = oracle_summary(_design(), source)
    assert summary["product_requirements"]["without_cases"] == ["REQ-2"]

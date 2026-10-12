"""Test oracles: every design case states an oracle; the host verifies quotes; characterization is never verified."""

import copy
from pathlib import Path

import pytest

from maf_test_harness.qa.oracles import (
    ORACLE_KINDS,
    check_oracles,
    counts_as_verified,
    file_reader,
    oracle_label,
    oracle_repair_message,
    oracle_summary,
    quote_in_text,
)
from maf_test_harness.repo.product_requirements import extract_product_requirements
from maf_test_harness.skills.contracts import finalize_design
from maf_test_harness.workflow_steps.design import requirement_batches
from maf_test_harness.workflow_steps.design_review import score_case
from maf_test_harness.workflow_support import _compact_requirements

SKILLS = Path(__file__).parents[1] / "skills"
URL = "https://example.test/repo"
GREEN_URL = "greenfield:specs"
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
                    "source_files": ["service.py"],
                    "tier": "component",
                    "status": "verified",
                },
                {
                    "id": "REQ-2",
                    "text": "report findings",
                    "evidence": "README.md",
                    "source_files": ["README.md"],
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
    if source is None and kind in ("derived",):
        source = {"file": "service.py", "line": 2, "quote": SOURCE_QUOTE}
    if source is None and kind == "specified":
        source = {"file": "README.md", "line": 3, "quote": README_QUOTE}
    return {"kind": kind, "source": source, "relation": relation}


def _case(oracle, title="add returns sum", link="REQ-1", target="service.add"):
    case = {
        "title": title,
        "priority": "P1",
        "level": "unit",
        "actor": "System",
        "target": target,
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


# --- every kind and the structural rules -------------------------------------------------------------------------


def test_every_kind_is_accepted_by_design_validation():
    cases = [
        _case(_oracle("derived"), "derived"),
        _case(_oracle("specified"), "specified"),
        _case(
            _oracle("metamorphic", relation="adding an irrelevant file does not change the add result"),
            "metamorphic",
        ),
        _case(
            _oracle(
                "golden",
                source={"file": "README.md", "line": 3, "quote": README_QUOTE},
                relation="fixture of two inputs 1 and 2; add returns 3 by the documented sum rule",
            ),
            "golden",
        ),
        _case(_oracle("property", relation="add always returns the sum of its two inputs"), "property"),
        _case(_oracle("characterization"), "characterization"),
    ]
    assert {c["oracle"]["kind"] for c in cases} == set(ORACLE_KINDS)
    result = finalize_design(_design(positive=cases), _source(), URL, SKILLS)
    assert len(result["positive_tests"]) == len(ORACLE_KINDS)


def test_design_rejects_missing_oracle():
    with pytest.raises(ValueError, match="no oracle"):
        finalize_design(_design(positive=[_case(None)]), _source(), URL, SKILLS)


@pytest.mark.parametrize(
    "oracle, message",
    [
        ({"kind": "specified", "source": None, "relation": None}, "needs source"),
        (
            {"kind": "derived", "source": {"file": "service.py", "line": 2, "quote": ""}, "relation": None},
            "needs source",
        ),
        ({"kind": "metamorphic", "source": None, "relation": None}, "stated relation"),
        (
            {
                "kind": "golden",
                "source": {"file": "README.md", "line": 1, "quote": "rule"},
                "relation": "short",
            },
            "stated relation",
        ),
        ({"kind": "property", "source": None, "relation": ""}, "stated relation"),
        ({"kind": "guessed", "source": None, "relation": None}, "not one of"),
    ],
)
def test_design_rejects_oracle_without_required_source_or_relation(oracle, message):
    with pytest.raises(ValueError, match=message):
        finalize_design(_design(positive=[_case(oracle)]), _source(), URL, SKILLS)


def test_greenfield_rejects_derived_oracle():
    case = _case(_oracle("derived"))
    with pytest.raises(ValueError, match="derived is unavailable in greenfield"):
        finalize_design(_design(positive=[case]), _source(), GREEN_URL, SKILLS)


def test_multi_case_repair_lists_every_problem(tmp_path):
    read = _repo(tmp_path)
    bad = [
        _case(_oracle("derived", {"file": "service.py", "line": 2, "quote": "return a - b"}), "first bad"),
        _case(_oracle("derived", {"file": "missing.py", "line": 1, "quote": "def add(a, b):"}), "second bad"),
        _case(
            {
                "kind": "metamorphic",
                "source": None,
                "relation": "removing irrelevant files keeps the output unchanged",
            },
            "third bad",
        ),
    ]
    problems = check_oracles(bad, read)
    assert len(problems) == 3
    message = oracle_repair_message(problems)
    for title in ("first bad", "second bad", "third bad"):
        assert title in message
    assert "Copy the exact source line" in message and "does not exist" in message
    assert "name the target service.add" in message


def test_repair_message_keeps_up_to_forty_cases():
    problems = [f"case {i}: problem" for i in range(55)]
    message = oracle_repair_message(problems)
    assert message.count("- case ") == 40 and "and 15 more" in message


def test_finalize_collects_every_shape_problem_before_raising():
    cases = [_case(None, "no oracle one"), _case(_oracle("metamorphic"), "meta no relation")]
    with pytest.raises(ValueError) as exc:
        finalize_design(_design(positive=cases), _source(), URL, SKILLS)
    assert "no oracle one" in str(exc.value) and "meta no relation" in str(exc.value)


# --- quotes: normalisation and forms -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "quote, text",
    [
        ("‘quoted’ and “curly”", "'quoted' and \"curly\""),  # curly to straight
        ("the `add` function and **bold** text", "the add function and bold text"),  # markup both sides
        ("service.py:2: return a + b", "def add(a, b):\n    return a + b"),  # path:line: locator
        ("2: return a + b", "def add(a, b):\n    return a + b"),  # line: locator
        ("The tool should report every finding ...", README_QUOTE),  # trailing ... prefix (>= 20 chars)
        ("return a + b", "def add(a, b):\r\n    return a + b\r\n"),  # CRLF
    ],
)
def test_quote_forms_verify(quote, text):
    assert quote_in_text(quote, text)


@pytest.mark.parametrize(
    "quote, text",
    [
        ("return a - b", "def add(a, b):\n    return a + b"),
        ("The tool should ... ", README_QUOTE),  # prefix shorter than 20 characters
        ("return", "def add(a, b):\n    return a + b"),  # too short to verify
        ("", "anything at all here"),
    ],
)
def test_quote_forms_reject(quote, text):
    assert not quote_in_text(quote, text)


def test_prefix_ellipsis_needs_twenty_characters():
    assert quote_in_text("The tool should report every ...", README_QUOTE)
    assert not quote_in_text("The tool should ...", README_QUOTE)


def test_reader_accepts_dot_slash_and_repo_prefixes_and_bom_crlf(tmp_path):
    (tmp_path / "service.py").write_bytes(b"\xef\xbb\xbfdef add(a, b):\r\n    return a + b\r\n")
    read = file_reader(tmp_path)
    assert read("./service.py") is not None and read("repo/service.py") is not None
    assert quote_in_text("return a + b", read("service.py"))
    case = _case(_oracle("derived", {"file": "repo/service.py", "line": 2, "quote": SOURCE_QUOTE}))
    assert check_oracles([case], read) == []
    assert counts_as_verified(case)


def test_reader_refuses_symlink_escape_and_binary(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("return a + b\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.py").symlink_to(outside / "secret.py")
    (root / "blob.bin").write_bytes(b"\x00\x01return a + b\x00")
    read = file_reader(root)
    assert read("link.py") is None
    assert read("blob.bin") is None
    assert read("../outside/secret.py") is None


def test_reader_accepts_large_files_up_to_five_megabytes(tmp_path):
    big = tmp_path / "big.md"
    big.write_text(
        "filler line\n" * 300_000 + "The tool should report every finding with a line number.\n",
        encoding="utf-8",
    )
    assert quote_in_text(README_QUOTE, file_reader(tmp_path)("big.md"))
    (tmp_path / "huge.md").write_bytes(b"a" * 5_000_001)
    assert file_reader(tmp_path)("huge.md") is None


# --- verified semantics --------------------------------------------------------------------------------------------


def test_specified_must_come_from_repository_file_not_requirement_text(tmp_path):
    read = _repo(tmp_path)
    case = _case(
        _oracle("specified", {"file": "README.md", "line": 3, "quote": "Totals are rounded half up"})
    )
    # The phrase exists only in model-written requirement text (not in the repository, not in a greenfield document).
    assert check_oracles([case], read, corpus="") != []
    assert case["oracle"]["verified"] is False and not counts_as_verified(case)


def test_greenfield_documents_are_the_only_non_repository_quote_source(tmp_path):
    read = file_reader(tmp_path)
    spec = _case(_oracle("specified", {"file": "req.md", "line": 1, "quote": "Totals are rounded half up"}))
    assert check_oracles([spec], read, corpus="Totals are rounded half up.", derived_allowed=False) == []
    assert counts_as_verified(spec)


def test_metamorphic_and_property_are_stated_and_count_only_with_a_test(tmp_path):
    read = _repo(tmp_path)
    case = _case(_oracle("metamorphic", relation="adding an irrelevant file does not change the add result"))
    assert check_oracles([case], read) == []
    assert case["oracle"]["stated"] is True and case["oracle"]["verified"] is False
    assert not counts_as_verified(case)  # no test yet
    case["test_exists"] = True
    assert counts_as_verified(case)
    assert oracle_label(case) == "metamorphic"
    no_target = _case(_oracle("property", relation="every output is between zero and one"))
    assert check_oracles([no_target], read) != []  # relation must name service.add
    assert no_target["oracle"]["stated"] is False


def test_golden_needs_verified_rule_quote_and_fixture(tmp_path):
    read = _repo(tmp_path)
    good = _case(
        _oracle(
            "golden",
            source={"file": "README.md", "line": 3, "quote": README_QUOTE},
            relation="fixture of inputs 1 and 2; add returns 3 by the documented sum rule",
        )
    )
    bad = _case(
        _oracle(
            "golden",
            source={"file": "README.md", "line": 3, "quote": "a rule nobody wrote here"},
            relation="fixture of inputs 1 and 2; add returns 3 by the documented sum rule",
        ),
        "golden bad",
    )
    assert len(check_oracles([good, bad], read)) == 1
    assert counts_as_verified(good) and not counts_as_verified(bad)


def test_characterization_is_never_verified(tmp_path):
    read = _repo(tmp_path)
    case = _case(_oracle("characterization"))
    assert check_oracles([case], read) == []
    assert case["oracle"]["verified"] is False and not counts_as_verified(case)
    assert oracle_label(case) == "characterization"


def test_reviewer_needs_its_own_quote_even_with_a_verified_oracle(tmp_path):
    read = _repo(tmp_path)
    case = _case(_oracle("derived"))
    check_oracles([case], read)
    source = "def add(a, b):\n    return a + b\n"
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
    scored = score_case(review, case, source=source, threshold=95)
    assert scored["oracle"] == "derived" and scored["confidence"] < 100  # no quote: not derivable
    review["source_quote"] = "return a + b"
    assert score_case(review, case, source=source, threshold=95)["confidence"] == 100
    characterization = _case(_oracle("characterization"))
    check_oracles([characterization], read)
    scored = score_case(review, characterization, source=source, threshold=95)
    assert any(note["rule"] == "characterization" for note in scored["notes"])


def test_planning_uses_the_same_checks_on_realistic_derived_quotes(tmp_path):
    """A fake that quotes the repository file (with a ./ prefix, line number and markup) verifies end to end."""
    read = _repo(tmp_path)
    case = _case(_oracle("derived", {"file": "./service.py", "line": 2, "quote": "2: `return a + b`"}))
    assert check_oracles([case], read) == []
    assert counts_as_verified(case)


def test_oracle_summary_counts_kinds_labels_tiers_and_product_coverage():
    cases = [
        {**_case(_oracle("derived"), "a", "REQ-1"), "id": "A", "type": "positive"},
        {**_case(_oracle("characterization"), "b", "REQ-1"), "id": "B", "type": "positive"},
        {
            **_case(
                {
                    "kind": "specified",
                    "source": {"file": "README.md", "line": 3, "quote": README_QUOTE},
                    "relation": None,
                },
                "c",
                "REQ-2",
            ),
            "id": "C",
            "type": "negative",
        },
    ]
    cases[2]["oracle"]["verified"] = True
    summary = oracle_summary(_design(positive=cases[:2], negative=cases[2:]), _source())
    assert summary["total_cases"] == 3
    assert summary["by_kind"]["derived"] == 1 and summary["by_kind"]["characterization"] == 1
    assert summary["by_kind"]["missing"] == 0
    assert summary["characterization_cases"] == ["B"]
    assert summary["verified_cases"] == 1
    assert summary["requirements_by_tier"] == {"product": 1, "component": 1}
    assert summary["cases_by_tier"] == {"product": 1, "component": 2}
    assert summary["product_requirements"] == {
        "total": 1,
        "with_cases": ["REQ-2"],
        "without_cases": [],
        "gap": [],
    }


def test_oracle_summary_handles_product_requirement_without_cases():
    summary = oracle_summary(_design(), copy.deepcopy(_source()))
    assert summary["product_requirements"]["without_cases"] == ["REQ-2"]


# --- gap requirements need no cases ----------------------------------------------------------------------------


def test_gap_requirements_are_not_batched_or_offered_as_targets_and_are_listed():
    source = copy.deepcopy(_source())
    source["requirement_model"]["requirements"][1]["status"] = "gap"
    batches = [
        subset for _, subset in requirement_batches({"requirement_model": source["requirement_model"]})
    ]
    assert all(r["id"] != "REQ-2" for b in batches for r in b["requirement_model"]["requirements"])
    compact = _compact_requirements({"requirement_model": source["requirement_model"]})
    assert [r["id"] for r in compact["requirements"]] == ["REQ-1"]
    design = finalize_design(_design(positive=[_case(_oracle("derived"))]), source, URL, SKILLS)
    assert "requirement:REQ-2" not in design["gaps_report"]["coverage_gaps"]
    assert "REQ-2" not in design["gaps_report"]["uncovered_requirements"]


# --- extraction on this repository ----------------------------------------------------------------------------------


def test_repository_readme_yields_product_items_not_index_rows():
    root = Path(__file__).resolve().parents[1]
    items = extract_product_requirements(root, ["README.md"])
    assert items, "the README has product statements"
    for item in items:
        quote = item["evidence_quotes"][0]["quote"]
        cells = [c.strip() for c in quote.strip().strip("|").split("|")]
        assert not (
            cells and ("/" in cells[0] or "." in cells[0].replace("`", "")[-6:] and " " not in cells[0])
        ), quote
        assert "](" not in quote and "docs/" not in cells[0]

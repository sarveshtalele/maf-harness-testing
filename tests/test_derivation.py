"""Deterministic expectation checks (qa/derivation.py): D1 count_assumes_no_filter and D2 expectation_contradicts_source."""

import ast
from pathlib import Path

from maf_test_harness.qa import derivation
from maf_test_harness.qa.derivation import (
    count_assumes_no_filter,
    deterministic_findings,
    expectation_contradicts_source,
)

FILTER_SOURCE = """--- SOURCE: location.py :: LocationResolver (lines 1-9) ---
1: class LocationResolver:
2:     def __init__(self, places):
3:         self.places = list(places)
6:     def resolve(self, query):
7:         scored = [(len(p), p) for p in self.places]
8:         return [place for points, place in scored if points > 0]
"""
PLAIN_SOURCE = """--- SOURCE: service.py :: add (lines 1-2) ---
1: def add(a, b):
2:     return a + b
"""
COUNT_SOURCE = """--- SOURCE: indexer.py :: CodebaseIndexer (lines 1-9) ---
1: class CodebaseIndexer:
2:     def build(self):
3:         index = {}
4:         for path in self.paths:
5:             content = path.read_text(encoding="utf-8")
6:             index[path.name] = {"line_count": content.count("\\n") + 1}
7:         return index
"""
LENGTH_TEST = """from location import LocationResolver

def test_resolve():
    assert len(LocationResolver(["Berlin", "Paris"]).resolve("Bx")) == {n}
"""
LINES_TEST = """from indexer import CodebaseIndexer

def test_build(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\\ny = 2\\n", encoding="utf-8")
    index = CodebaseIndexer(tmp_path).build()
    assert index["app.py"]["line_count"] == {n}
"""


def test_d1_flags_a_count_equal_to_the_fixture_size_when_the_target_filters():
    finding = count_assumes_no_filter(LENGTH_TEST.format(n=2), FILTER_SOURCE, "LocationResolver.resolve")
    assert finding["rule"] == "count_assumes_no_filter" and finding["cap"] == 60


def test_d1_accepts_a_count_that_differs_from_the_fixture_size():
    assert count_assumes_no_filter(LENGTH_TEST.format(n=1), FILTER_SOURCE, "LocationResolver.resolve") is None


def test_d1_is_silent_when_the_target_does_not_filter():
    test = """def test_add():\n    assert len(add([1, 2])) == 2\n"""
    assert count_assumes_no_filter(test, PLAIN_SOURCE.replace("add", "add"), "add") is None


def test_d1_is_silent_without_a_literal_fixture_of_that_size():
    test = """from location import LocationResolver\n\ndef test_resolve():\n    places = load()\n    assert len(LocationResolver(places).resolve("Bx")) == 2\n"""
    assert count_assumes_no_filter(test, FILTER_SOURCE, "LocationResolver.resolve") is None


def test_d1_supports_assert_equal_form():
    test = """import unittest\nfrom location import LocationResolver\n\nclass T(unittest.TestCase):\n    def test_resolve(self):\n        self.assertEqual(len(LocationResolver(['a', 'b']).resolve('x')), 2)\n"""
    assert count_assumes_no_filter(test, FILTER_SOURCE, "LocationResolver.resolve")["cap"] == 60


def test_d2_flags_a_line_count_that_the_source_contradicts():
    finding = expectation_contradicts_source(LINES_TEST.format(n=2), COUNT_SOURCE, "CodebaseIndexer.build")
    assert finding == {
        "rule": "expectation_contradicts_source",
        "cap": 0,
        "reason": finding["reason"],
    }
    assert "= 3" in finding["reason"] and "expects 2" in finding["reason"]


def test_d2_accepts_the_value_the_source_computes():
    assert (
        expectation_contradicts_source(LINES_TEST.format(n=3), COUNT_SOURCE, "CodebaseIndexer.build") is None
    )


def test_d2_is_ambiguous_when_the_test_binds_two_string_inputs():
    test = LINES_TEST.format(n=2).replace(
        "    index = ", '    (tmp_path / "b.py").write_text("one\\n", encoding="utf-8")\n    index = '
    )
    finding = expectation_contradicts_source(test, COUNT_SOURCE, "CodebaseIndexer.build")
    assert finding == {"rule": "derivation_required", "cap": None, "reason": finding["reason"]}


def test_d2_is_ambiguous_for_an_expression_outside_the_whitelist():
    source = COUNT_SOURCE.replace('content.count("\\n") + 1', 'len(re.findall("\\n", content)) + 1')
    assert "re.findall" in source
    finding = expectation_contradicts_source(LINES_TEST.format(n=2), source, "CodebaseIndexer.build")
    assert finding["rule"] == "derivation_required" and finding["cap"] is None


def test_d2_is_ambiguous_when_the_key_is_assigned_twice():
    source = COUNT_SOURCE.replace(
        "        return index",
        '        index["app.py"] = {"line_count": 9}\n        return index',
    )
    finding = expectation_contradicts_source(LINES_TEST.format(n=2), source, "CodebaseIndexer.build")
    assert finding["rule"] == "derivation_required"


def test_d2_is_silent_without_a_target_function():
    assert expectation_contradicts_source(LINES_TEST.format(n=2), COUNT_SOURCE, "Missing.method") is None


def test_deterministic_findings_collects_both_checks_for_one_file():
    test = LENGTH_TEST.format(n=2)
    rules = [f["rule"] for f in deterministic_findings(test, FILTER_SOURCE, "LocationResolver.resolve")]
    assert rules == ["count_assumes_no_filter"]


def test_checks_never_execute_repository_or_test_code():
    tree = ast.parse(Path(derivation.__file__).read_text(encoding="utf-8"))
    banned = {"eval", "exec", "compile", "__import__"}
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & banned


# --- Blind derivation: masking and the three host caps --------------------------------------------------------

from maf_test_harness.qa.confidence import derivation_findings, score_review  # noqa: E402
from maf_test_harness.qa.derivation import mask_expected_literals  # noqa: E402

MASK_SOURCE = (
    'def test_b(tmp_path):\n    assert idx["a"]["line_count"] == 2; assert x == "hi"\n'
    "    self.assertEqual(len(r), 3)\n"
)


def test_masking_replaces_right_hand_assertion_literals_in_source_order():
    masked, slots = mask_expected_literals(
        [{"path": "tests/test_a.py", "case_ids": ["TC-1"], "content": MASK_SOURCE}]
    )
    assert "== «E1»" in masked[0]["content"] and "== «E2»" in masked[0]["content"]
    assert "len(r), «E3»)" in masked[0]["content"]
    assert [(s["slot"], s["value"], s["case_ids"]) for s in slots] == [
        ("E1", "2", ["TC-1"]),
        ("E2", "hi", ["TC-1"]),
        ("E3", "3", ["TC-1"]),
    ]


def test_masking_numbers_slots_across_files_and_leaves_non_python_alone():
    files = [
        {"path": "tests/test_a.py", "case_ids": ["TC-1"], "content": "assert f() == 1\n"},
        {"path": "tests/a.spec.ts", "case_ids": ["TC-2"], "content": "expect(f()).toBe(2);\n"},
        {"path": "tests/test_b.py", "case_ids": ["TC-2"], "content": "assert g() == 5\n"},
    ]
    masked, slots = mask_expected_literals(files)
    assert masked[1]["content"] == files[1]["content"]
    assert [s["slot"] for s in slots] == ["E1", "E2"] and slots[1]["file"] == 2


def test_masking_is_utf8_safe_on_the_line_of_a_literal():
    masked, _ = mask_expected_literals(
        [{"path": "t.py", "case_ids": ["TC-1"], "content": 'x = "é"; assert y == 4\n'}]
    )
    assert masked[0]["content"] == 'x = "é"; assert y == «E1»\n'


SLOTS = [{"slot": "E1", "value": "3", "case_ids": ["TC-1"]}]
SOURCE = '--- SOURCE: indexer.py :: build (lines 1-3) ---\n2: def build(self):\n3:     return content.count("\\n") + 1\n'
TEST = 'write_text("x\\ny\\n")\nassert idx.line_count == 3\n'


def _derivation(**changes):
    base = {
        "slot": "E1",
        "value": "3",
        "steps": "count newlines then add one",
        "source_quote": 'return content.count("\\n") + 1',
        "fixture_quote": 'write_text("x\\ny\\n")',
    }
    return {**base, **changes}


def test_answered_verbatim_matching_derivation_has_no_caps():
    assert (
        derivation_findings(slots=SLOTS, derivations=[_derivation()], target_source=SOURCE, test_text=TEST)
        == []
    )


def test_missing_derivation_caps_at_60():
    (finding,) = derivation_findings(slots=SLOTS, derivations=[], target_source=SOURCE, test_text=TEST)
    assert finding["rule"] == "derivation_missing" and finding["cap"] == 60


def test_quote_not_verbatim_in_the_source_caps_at_50():
    (finding,) = derivation_findings(
        slots=SLOTS,
        derivations=[_derivation(source_quote="return content.count('\\n') + 2")],
        target_source=SOURCE,
        test_text=TEST,
    )
    assert finding["rule"] == "derivation_unverifiable" and finding["cap"] == 50


def test_whitespace_differences_in_quotes_are_tolerated():
    assert (
        derivation_findings(
            slots=SLOTS,
            derivations=[_derivation(source_quote='return   content.count("\\n")   + 1')],
            target_source=SOURCE,
            test_text=TEST,
        )
        == []
    )


def test_wrong_derived_value_caps_at_40_and_forces_needs_revision():
    findings = derivation_findings(
        slots=SLOTS, derivations=[_derivation(value="2")], target_source=SOURCE, test_text=TEST
    )
    assert findings[0]["rule"] == "blind_derivation_mismatch" and findings[0]["cap"] == 40
    review = {
        "case_id": "TC-1",
        "scores": {
            "source_and_api_match": 25,
            "assertion_checks_real_behavior": 30,
            "fixture_validity": 20,
            "isolation_and_determinism": 15,
            "traceability": 10,
        },
        "confidence": 100,
        "verdict": "supported",
        "reason": "r",
        "source_files": ["indexer.py"],
        "derivations": [],
    }
    scored = score_review(
        review, target="CodebaseIndexer.build", content=TEST, source_found=True, findings=findings
    )
    assert scored["confidence"] == 40 and scored["verdict"] == "needs_revision"


def test_a_case_without_slots_has_nothing_to_derive():
    assert derivation_findings(slots=[], derivations=[], target_source="", test_text=TEST) == []

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
    assert finding["rule"] == "expectation_contradicts_source" and finding["cap"] == 0
    assert finding["value"] == 3 and finding["expected"] == 2
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


SLOTS = [{"slot": "E1", "value": "3", "case_ids": ["TC-1"], "literal": 3}]
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


# --- Evidence rules and false-rejection cases (review of 2026-10-12) ----------------------------------------

from maf_test_harness.models import TestCaseReview as ReviewModel  # noqa: E402
from maf_test_harness.qa.confidence import literal_matches, settle_deterministic  # noqa: E402
from maf_test_harness.qa.derivation import split_lines  # noqa: E402

KEEPS_ALL_TEST = """from items import Pick

def test_run():
    assert len(Pick().run(["a", "b"])) == 2
"""
KEEPS_ALL_SOURCE = "class Pick:\n    def run(self, items):\n        return [item for item in items if item]\n"


def test_d1_fires_on_a_filter_that_may_keep_everything_and_asks_for_evidence():
    finding = count_assumes_no_filter(KEEPS_ALL_TEST, KEEPS_ALL_SOURCE, "Pick.run")
    assert finding["cap"] == 60 and finding["tokens"] == ["item"] and finding["expected"] == 2


def _verified_count(steps, source_quote):
    slots = [{"slot": "E1", "value": "2", "case_ids": ["TC-1"], "literal": 2}]
    derivations = [
        {
            "slot": "E1",
            "value": "2",
            "steps": steps,
            "source_quote": source_quote,
            "fixture_quote": 'Pick().run(["a", "b"])',
        }
    ]
    return slots, derivations


def test_d1_is_lifted_only_when_the_verified_derivation_cites_the_filter():
    finding = count_assumes_no_filter(KEEPS_ALL_TEST, KEEPS_ALL_SOURCE, "Pick.run")
    slots, derivations = _verified_count("each item is truthy, so the filter keeps both", "class Pick:")
    (lifted,) = settle_deterministic(
        [finding],
        slots=slots,
        derivations=derivations,
        target_source=KEEPS_ALL_SOURCE,
        test_text=KEEPS_ALL_TEST,
    )
    assert lifted["cap"] is None and lifted["rule"] == "count_assumes_no_filter"
    slots, derivations = _verified_count("two items were passed", "class Pick:")
    (capped,) = settle_deterministic(
        [finding],
        slots=slots,
        derivations=derivations,
        target_source=KEEPS_ALL_SOURCE,
        test_text=KEEPS_ALL_TEST,
    )
    assert capped["cap"] == 60


def test_d1_is_lifted_when_the_filter_line_is_quoted():
    finding = count_assumes_no_filter(KEEPS_ALL_TEST, KEEPS_ALL_SOURCE, "Pick.run")
    slots, derivations = _verified_count("two items", "return [item for item in items if item]")
    (lifted,) = settle_deterministic(
        [finding],
        slots=slots,
        derivations=derivations,
        target_source="1: " + KEEPS_ALL_SOURCE.replace("\n", "\n"),
        test_text=KEEPS_ALL_TEST,
    )
    assert lifted["cap"] is None


def test_d1_is_silent_when_the_literal_does_not_flow_into_the_target_call():
    test = "def test_run():\n    items = ['a', 'b']\n    assert len(Pick().run(items[:1])) == 2\n"
    assert count_assumes_no_filter(test, KEEPS_ALL_SOURCE, "Pick.run") is None


def test_d1_ignores_unrelated_filters_such_as_is_none_checks():
    source = "class Pick:\n    def run(self, items):\n        if items is None:\n            return []\n        return list(items)\n"
    assert count_assumes_no_filter(KEEPS_ALL_TEST, source, "Pick.run") is None


def test_d2_does_not_apply_when_the_parameter_is_rebound():
    source = 'def build(text):\n    text = text.upper()\n    return {"n": len(text)}\n'
    test = (
        'from m import build\n\ndef test_build():\n    result = build("abc")\n    assert result["n"] == 4\n'
    )
    finding = expectation_contradicts_source(test, source, "build")
    assert finding is None or finding["cap"] is None


def test_d2_does_not_apply_to_an_augmented_assignment_on_the_key():
    source = COUNT_SOURCE.replace(
        "        return index",
        '        index["app.py"]["line_count"] += 1\n        return index',
    )
    finding = expectation_contradicts_source(LINES_TEST.format(n=2), source, "CodebaseIndexer.build")
    assert finding["rule"] == "derivation_required" and finding["cap"] is None


def test_d2_does_not_apply_to_a_test_local_dict():
    test = 'def test_build():\n    local = {"line_count": 2}\n    assert local["line_count"] == 2\n'
    assert expectation_contradicts_source(test, COUNT_SOURCE, "CodebaseIndexer.build") is None


def test_d2_does_not_apply_when_the_key_is_assigned_under_a_branch():
    source = COUNT_SOURCE.replace(
        '            index[path.name] = {"line_count": content.count("\\n") + 1}',
        '            if content:\n                index[path.name] = {"line_count": content.count("\\n") + 1}',
    )
    finding = expectation_contradicts_source(LINES_TEST.format(n=2), source, "CodebaseIndexer.build")
    assert finding["rule"] == "derivation_required"


def test_huge_repetition_is_refused_not_computed():
    source = 'def build(text):\n    return {"line_count": len(text * 1000000)}\n'
    test = 'from m import build\n\ndef test_build():\n    assert build("x")["line_count"] == 1\n'
    finding = expectation_contradicts_source(test, source, "build")
    assert finding is None or finding["cap"] is None


def test_deep_nesting_cannot_crash_the_checks():
    source = "def build(text):\n    return " + "(" * 5000 + "1" + ")" * 5000 + "\n"
    test = "x = " + "[" * 5000 + "]" * 5000 + "\nassert build('a')['n'] == 1\n"
    assert deterministic_findings(test, source, "build") == []  # refused, not raised
    assert deterministic_findings("def test_x():\n    assert build('a') == 1\n", source, "build") == []


def test_oversized_input_is_not_parsed():
    assert count_assumes_no_filter("x = 1\n" * 60_000, KEEPS_ALL_SOURCE, "Pick.run") is None


def test_control_characters_do_not_shift_the_masked_line():
    content = 'def test_x():\n    s = "a\x0cb\u2028c"\n    assert s == 3\n'
    masked, slots = mask_expected_literals([{"path": "t.py", "case_ids": ["TC-1"], "content": content}])
    assert masked[0]["content"] == 'def test_x():\n    s = "a\x0cb\u2028c"\n    assert s == «E1»\n'
    assert [(s["slot"], s["value"], s["line"]) for s in slots] == [("E1", "3", 3)]


def test_split_lines_follows_the_parser_not_str_splitlines():
    assert split_lines("a\x0cb c\nd") == ["a\x0cb c\n", "d"]
    assert split_lines("a\r\nb\rc") == ["a\r\n", "b\r", "c"]


MULTI_CASE = (
    "# CASE_ID: TC-1 | RUN: r | REQ: R1 | TITLE: one\n"
    "def test_one():\n    assert f() == 1\n\n"
    "# CASE_ID: TC-2 | RUN: r | REQ: R2 | TITLE: two\n"
    "def test_two():\n    assert f() == 2\n    assert g() == 3\n"
)


def test_a_multi_case_file_assigns_each_slot_to_the_enclosing_functions_case():
    _, slots = mask_expected_literals(
        [{"path": "tests/test_many.py", "case_ids": ["TC-1", "TC-2"], "content": MULTI_CASE}]
    )
    assert [(s["value"], s["case_ids"]) for s in slots] == [("1", ["TC-1"]), ("2", ["TC-2"]), ("3", ["TC-2"])]


def test_a_file_with_more_than_forty_assertions_has_one_slot_each_and_validates():
    body = "\n".join(f"    assert f({i}) == {i}" for i in range(45))
    content = f"# CASE_ID: TC-1 | RUN: r | REQ: R1 | TITLE: many\ndef test_many():\n{body}\n"
    _, slots = mask_expected_literals(
        [{"path": "tests/test_big.py", "case_ids": ["TC-1"], "content": content}]
    )
    assert len(slots) == 45 and all(s["case_ids"] == ["TC-1"] for s in slots)
    review = ReviewModel.model_validate(
        {
            "case_id": "TC-1",
            "scores": {
                "source_and_api_match": 25,
                "assertion_checks_real_behavior": 30,
                "fixture_validity": 20,
                "isolation_and_determinism": 15,
                "traceability": 10,
            },
            "verdict": "supported",
            "reason": "r",
            "source_files": ["service.py"],
            "derivations": [
                {
                    "slot": s["slot"],
                    "value": s["value"],
                    "steps": "s",
                    "source_quote": "q",
                    "fixture_quote": "f",
                }
                for s in slots
            ],
        }
    )
    assert len(review.derivations) == 45


def test_literal_values_compare_by_value_and_unescape():
    assert literal_matches("2.0", 2)
    assert literal_matches("'hi'", "hi") and literal_matches('"hi"', "hi")
    assert literal_matches("'a\\nb'", "a\nb")
    assert not literal_matches("True", 1)
    assert not literal_matches("3", 2)


def test_line_number_prefixes_are_stripped_inside_quotes_too():
    from maf_test_harness.qa.confidence import derivation_status

    source = "--- SOURCE: m.py :: f (lines 1-2) ---\n1: def f(a):\n2:     return a + 1\n"
    slots = [{"slot": "E1", "value": "3", "case_ids": ["TC-1"], "literal": 3}]
    derivation = {
        "slot": "E1",
        "value": "3",
        "steps": "s",
        "source_quote": "1: def f(a): 2: return a + 1",
        "fixture_quote": "f(2)",
    }
    status = derivation_status(
        slots=slots,
        derivations=[derivation],
        target_source=source,
        test_text="assert f(2) == 3",
        require_source=True,
    )
    assert status["verified"] == ["E1"]


def test_the_fixture_quote_may_come_from_the_masked_text():
    from maf_test_harness.qa.confidence import derivation_status

    slots = [{"slot": "E1", "value": "3", "case_ids": ["TC-1"], "literal": 3}]
    derivation = {
        "slot": "E1",
        "value": "3",
        "steps": "s",
        "source_quote": "return a + 1",
        "fixture_quote": "assert f(2) == «E1»",
    }
    status = derivation_status(
        slots=slots,
        derivations=[derivation],
        target_source="return a + 1",
        test_text="assert f(2) == 3",
        masked_text="assert f(2) == «E1»",
        require_source=True,
    )
    assert status["verified"] == ["E1"]

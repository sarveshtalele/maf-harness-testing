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

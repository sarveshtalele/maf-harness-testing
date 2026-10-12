"""Triage policy (review of task-verify-run): a failure is regenerated only with positive evidence; comparisons use
normalised values and boundary matches; runner formats; heal guard; verdict; coverage reach; ledger and API split."""

import asyncio
import json
import sys

import pytest

from maf_test_harness.qa import coverage, ledger
from maf_test_harness.qa.report import build_qa_report
from maf_test_harness.qa.verification import (
    ENVIRONMENT,
    PRODUCT_DEFECT,
    TEST_DEFECT,
    UNRESOLVED,
    classify_failure,
    heal_guard_violations,
    parse_value,
    quote_states,
    repo_module_names,
    same,
    triage_cases,
)
from maf_test_harness.ui.confidence_api import ConfidenceAPI
from maf_test_harness.workflow_steps.execution import ExecutionSteps
from maf_test_harness.workflow_support import EXECUTION_STAGE

DOC = "The documented sum: add(1, 2) returns 3, and the batch holds 10 files."


def pytest_message(left: str, right: str) -> str:
    return f"AssertionError: assert {left} == {right}\nE       assert {left} == {right}\n"


# ---------------------------------------------------------------- 1. comparison


def test_reversed_operands_are_product_defects_when_either_side_is_documented():
    # Operand order is not assumed: the quote states 3, the test asserts 3 == 4 or 4 == 3.
    assert (
        classify_failure(pytest_message("3", "4"), oracle="specified", quote=DOC)["classification"]
        == PRODUCT_DEFECT
    )
    assert (
        classify_failure(pytest_message("4", "3"), oracle="specified", quote=DOC)["classification"]
        == PRODUCT_DEFECT
    )


def test_neither_side_documented_is_unresolved_never_test_defect():
    record = classify_failure(pytest_message("5", "6"), oracle="golden", quote=DOC)
    assert record["classification"] == UNRESOLVED


def test_substring_false_positives_do_not_match_numbers():
    # "1" must not match "10 files"; the quote states 10 and 3 only.
    assert quote_states("the batch holds 10 files", 1) is False
    assert quote_states("the batch holds 10 files", 10) is True
    assert (
        classify_failure(pytest_message("1", "7"), oracle="specified", quote="the batch holds 10 files")[
            "classification"
        ]
        == UNRESOLVED
    )


def test_short_strings_need_an_exact_token():
    assert quote_states("the abc case", "ab") is False
    assert quote_states("the ab case", "ab") is True


def test_decimal_list_and_dict_reprs_are_compared_by_value_and_structure():
    assert parse_value("Decimal('2.5')") == 2.5
    assert same([1, [2, 3]], [1, [2, 3]]) and not same([1, 2], [1, 3])
    assert same({"a": 1, "b": [True]}, {"b": [True], "a": 1})
    quote = "The total is 2.5 units."
    record = classify_failure(pytest_message("Decimal('2.5')", "2.4"), oracle="specified", quote=quote)
    assert record["classification"] == PRODUCT_DEFECT
    lists = classify_failure(pytest_message("[1, 2]", "[1, 3]"), oracle="specified", quote="returns [1, 2]")
    assert lists["classification"] == PRODUCT_DEFECT
    dicts = classify_failure(
        pytest_message("{'a': 1}", "{'a': 2}"), oracle="specified", quote="gives {'a': 1}"
    )
    assert dicts["classification"] == PRODUCT_DEFECT


def test_true_is_never_equal_to_one():
    assert same(True, 1) is False and same(False, 0) is False
    assert quote_states("the flag is true", True) is True
    assert quote_states("the flag is 1", True) is False


def test_unparseable_values_are_unresolved_never_test_defect():
    record = classify_failure(pytest_message("add(1, 2)", "4"), oracle="derived", quote="")
    assert record["classification"] == UNRESOLVED
    with pytest.raises(ValueError):
        parse_value("object at 0x1")


def test_other_operators_and_multi_line_diffs_are_unresolved():
    for line in (
        "assert 1 != 2",
        "assert 1 in [2]",
        "assert 1 is 2",
        "assert 1 < 2",
        "assert 1 == approx(2)",
    ):
        message = f"AssertionError: {line}\nE       {line}\n"
        assert classify_failure(message, oracle="specified", quote=DOC)["classification"] == UNRESOLVED, line
    diff = "AssertionError: assert 'abc' == 'abd'\nE       assert 'abc' == 'abd'\nE         - abd\nE         + abc\n"
    assert classify_failure(diff, oracle="specified", quote="abc")["classification"] == UNRESOLVED


def test_pytest_where_explanations_are_not_mistaken_for_a_diff():
    message = "AssertionError: assert 3 == 4\nE       assert 3 == 4\nE        +  where 3 = add(1, 2)\n"
    record = classify_failure(message, oracle="specified", quote="add(1, 2) returns 3")
    assert record["classification"] == PRODUCT_DEFECT


def test_pytest_e_line_is_preferred_over_the_source_header():
    message = "def test_x():\n>       assert add(1, 2) == 4\nE       assert 3 == 4\n"
    record = classify_failure(message, oracle="specified", quote="add(1, 2) is 3")
    assert (record["actual"], record["expected"]) == ("3", "4")


# ---------------------------------------------------------------- 2. runner formats


def test_jest_expected_and_received_are_parsed():
    message = "expect(received).toBe(expected) // Object.is equality\n\nExpected: 4\nReceived: 3\n"
    record = classify_failure(message, oracle="specified", quote=DOC)
    assert record["classification"] == PRODUCT_DEFECT
    assert (record["actual"], record["expected"]) == ("3", "4")


def test_jest_string_values_and_js_literals_are_parsed():
    message = 'expect(received).toBe(expected)\n\nExpected: "abc"\nReceived: "abd"\n'
    assert (
        classify_failure(message, oracle="specified", quote="returns abc")["classification"] == PRODUCT_DEFECT
    )
    flags = "expect(received).toBe(expected)\n\nExpected: true\nReceived: null\n"
    record = classify_failure(flags, oracle="specified", quote="the flag is true")
    assert record["classification"] == PRODUCT_DEFECT


def test_junit_expected_but_was_with_inner_brackets_is_parsed():
    message = "junit.framework.ComparisonFailure: expected:<[a]bc> but was:<[a]bd>"
    record = classify_failure(message, oracle="specified", quote="the value is abc")
    assert record["classification"] == PRODUCT_DEFECT
    assert (record["expected"], record["actual"]) == ("[a]bc", "[a]bd")


def test_junit_plain_values_are_parsed():
    record = classify_failure("expected:<3> but was:<4>", oracle="specified", quote="add returns 3")
    assert record["classification"] == PRODUCT_DEFECT


# ---------------------------------------------------------------- 3. keyword rules


def test_assertion_messages_containing_import_error_text_are_not_keyword_test_defects():
    message = "AssertionError: assert 'ImportError: x' == 'ok'\nE       assert 'ImportError: x' == 'ok'\n"
    record = classify_failure(message, oracle="specified", quote="the answer is ok")
    assert record["classification"] == PRODUCT_DEFECT
    assert "collect" not in record["reason"]


def test_keyword_rules_apply_to_collection_or_compile_errors():
    syntax = "SyntaxError: invalid syntax"
    assert (
        classify_failure(syntax, oracle="specified", quote="", status="error")["classification"]
        == TEST_DEFECT
    )
    assert (
        classify_failure("cannot find symbol", oracle="", quote="", status="failed")["classification"]
        == UNRESOLVED
    )


# ---------------------------------------------------------------- 5. environment vs test defect


def test_missing_declared_package_is_environment_and_unknown_module_is_a_test_defect():
    context = {"modules": {"service"}, "declared": ["requests"]}
    env = classify_failure(
        "ModuleNotFoundError: No module named 'requests'",
        oracle="",
        quote="",
        status="error",
        context=context,
    )
    assert env["classification"] == ENVIRONMENT
    unknown = classify_failure(
        "ModuleNotFoundError: No module named 'service_helpers'",
        oracle="",
        quote="",
        status="error",
        context=context,
    )
    assert unknown["classification"] == TEST_DEFECT
    existing = classify_failure(
        "ModuleNotFoundError: No module named 'service.sub'",
        oracle="",
        quote="",
        status="error",
        context=context,
    )
    assert existing["classification"] == UNRESOLVED


def test_repository_module_names_include_packages_and_dotted_paths():
    names = repo_module_names(["service.py", "pkg/__init__.py", "pkg/core/engine.py", "README.md"])
    assert {"service", "pkg", "pkg.core", "pkg.core.engine"} <= names
    assert "readme" not in names


# ---------------------------------------------------------------- 6. untriaged failures


def test_failed_or_error_with_an_empty_message_is_unresolved_and_recorded():
    rows = triage_cases(
        [
            {"id": "TC-1", "status": "failed", "message": "", "oracle": "derived"},
            {"id": "TC-2", "status": "error", "message": ""},
        ]
    )
    assert [r["classification"] for r in rows] == [UNRESOLVED, UNRESOLVED]


def test_not_executed_collection_problem_is_a_test_defect_and_other_reasons_are_unresolved():
    context = {"modules": {"service"}, "declared": []}
    rows = triage_cases(
        [
            {
                "id": "TC-1",
                "status": "not_executed",
                "message": "SyntaxError: invalid syntax",
                "oracle": "derived",
            },
            {"id": "TC-2", "status": "not_executed", "message": "the runner timed out", "oracle": "derived"},
        ],
        context=context,
    )
    by_case = {r["case_id"]: r["classification"] for r in rows}
    assert by_case == {"TC-1": TEST_DEFECT, "TC-2": UNRESOLVED}


# ---------------------------------------------------------------- 7. heal guard


class _Ctx:
    def __init__(self):
        self.sent = []

    async def send_message(self, state):
        self.sent.append(state)


def _heal(state):
    class Host(ExecutionSteps):
        def audit(self, event, **details):
            return None

    asyncio.run(Host().heal(state, "", _Ctx()))


def _generated(content, case_ids=("TC-1",)):
    return [{"path": "tests/test_x.py", "content": content, "case_ids": list(case_ids)}]


def test_heal_selects_test_defects_including_not_executed_and_keeps_the_documented_instruction():
    qa = {
        "cases": [
            {
                "id": "TC-1",
                "title": "guessed",
                "status": "not_executed",
                "message": "SyntaxError",
                "file": "t.py",
            },
            {"id": "TC-2", "title": "documented", "status": "failed", "message": "x", "file": "u.py"},
            {"id": "TC-3", "title": "no message", "status": "failed", "message": "", "file": "v.py"},
        ],
        "triage": [
            {
                "case_id": "TC-1",
                "status": "not_executed",
                "classification": TEST_DEFECT,
                "reason": "syntax",
                "oracle": "derived",
                "documented_quote": None,
                "documented_matches": [],
            },
            {
                "case_id": "TC-2",
                "status": "failed",
                "classification": PRODUCT_DEFECT,
                "reason": "documented",
                "oracle": "specified",
                "documented_quote": "add(1, 2) returns 3",
                "documented_matches": ["3"],
            },
            {
                "case_id": "TC-3",
                "status": "failed",
                "classification": UNRESOLVED,
                "reason": "empty",
                "oracle": "derived",
                "documented_quote": None,
                "documented_matches": [],
            },
        ],
    }
    state = {
        "heal_rounds": 0,
        "artifacts": {
            EXECUTION_STAGE: {"qa_report": qa},
            "test_generation": {"files": _generated("x = 1\n")},
        },
        "approved": {},
    }
    _heal(state)
    assert state["heal"]["failed_case_ids"] == ["TC-1"]
    feedback = state["feedback"]["test_generation"]
    assert "TC-2" not in feedback and "TC-3" not in feedback


def test_heal_instruction_carries_the_documented_quote_for_specified_oracles():
    qa = {
        "cases": [{"id": "TC-2", "title": "documented", "status": "failed", "message": "E", "file": "u.py"}],
        "triage": [
            {
                "case_id": "TC-2",
                "status": "failed",
                "classification": TEST_DEFECT,
                "reason": "r",
                "oracle": "specified",
                "documented_quote": "add(1, 2) returns 3",
                "documented_matches": [],
            },
        ],
    }
    state = {
        "heal_rounds": 0,
        "artifacts": {
            EXECUTION_STAGE: {"qa_report": qa},
            "test_generation": {"files": _generated("x = 1\n", ("TC-2",))},
        },
        "approved": {},
    }
    _heal(state)
    feedback = state["feedback"]["test_generation"]
    assert "DOCUMENTED (specified): add(1, 2) returns 3" in feedback
    assert "NEVER CHANGE THIS EXPECTED VALUE; fix setup or inputs only." in feedback
    assert state["heal_guard"]["guards"]["TC-2"]["files"] == ["tests/test_x.py"]


def test_heal_guard_rejects_a_healed_file_that_changed_a_documented_value():
    previous = {"tests/test_x.py": "def test_x():\n    assert add(1, 2) == 3\n"}
    guards = {"TC-1": {"files": ["tests/test_x.py"], "documented": ["3"]}}
    changed = {"tests/test_x.py": "def test_x():\n    assert add(1, 2) == 4\n"}
    assert heal_guard_violations(previous, changed, guards) == ["TC-1: documented value 3 was changed"]
    kept = {"tests/test_x.py": "def test_x():\n    assert add(1, 2) == 3\n    assert True\n"}
    assert heal_guard_violations(previous, kept, guards) == []


# ---------------------------------------------------------------- 8. verdict


def _verdict_report(status, triage_oracle, message):
    design = {
        "positive_tests": [
            {
                "id": "TC-1",
                "title": "t",
                "type": "positive",
                "traceability_links": [],
                "oracle": triage_oracle,
                "oracle_quote": "add(1, 2) returns 3",
            }
        ],
        "traceability_matrix": {},
    }
    gen = {
        "files": [{"path": "tests/test_x.py", "content": "x", "case_ids": ["TC-1"]}],
        "case_reviews": [{"case_id": "TC-1", "verdict": "supported", "confidence": 97, "reason": "r"}],
    }
    execution = {
        "status": "failed" if status != "passed" else "passed",
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
        repo={"source_files": []}, requirements={}, design=design, generation=gen, execution=execution
    )


def test_a_product_defect_or_unresolved_failure_never_yields_a_pass_verdict():
    product = _verdict_report("failed", "specified", pytest_message("3", "4"))
    assert product["triage"][0]["classification"] == PRODUCT_DEFECT
    assert product["verdict"] == "FAIL"
    unresolved = _verdict_report("failed", "metamorphic", pytest_message("3", "4"))
    assert unresolved["triage"][0]["classification"] == UNRESOLVED
    assert not unresolved["verdict"].startswith("PASS")


# ---------------------------------------------------------------- 9. coverage reach


def test_omit_rules_are_workspace_relative_so_a_workspace_under_tests_is_still_measured(tmp_path):
    ws = tmp_path / "tests" / "project"
    (ws / "pkg").mkdir(parents=True)
    (ws / "tests").mkdir()
    (ws / "service.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "tests" / "test_service.py").write_text("x = 1\n", encoding="utf-8")
    assert coverage.is_omitted("service.py") is False
    assert coverage.is_omitted("pkg/core.py") is False
    assert coverage.is_omitted("tests/test_service.py") is True
    assert coverage.count_source_files(ws) == 2


def test_coverage_keys_are_repo_relative_and_components_merge_without_collisions():
    report = {
        "files": {
            "app.py": {
                "summary": {"covered_lines": 2, "num_statements": 2, "covered_branches": 0, "num_branches": 0}
            }
        }
    }
    backend = coverage.file_counts(report, prefix="backend")
    frontend = coverage.file_counts(report, prefix="web")
    assert set(coverage.merge(backend, frontend)) == {"backend/app.py", "web/app.py"}


def test_windows_paths_are_case_folded_for_matching(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert coverage._path("Pkg\\Core.py") == "pkg/core.py"


def test_env_ready_plans_still_install_the_pinned_coverage_tooling(tmp_path):
    from maf_test_harness.envs import runners

    steps = runners.setup_steps("python", tmp_path, str(tmp_path / "cache"), 60, env_ready=True)
    install = [s for s in steps if s.name == "install-coverage"]
    assert len(install) == 1 and install[0].argv[-2:] == list(runners.COVERAGE_PACKAGES)
    assert install[0].continue_on_failure and "install-pytest" not in [s.name for s in steps]


def test_report_states_the_measurement_reach_and_the_repository_total():
    cov = coverage.summarize(
        {"service.py": {"covered_lines": 1, "num_statements": 2, "covered_branches": 0, "num_branches": 0}},
        {},
        total_source_files=5,
    )
    assert "files imported during the tests (1 files)" in cov["scope"]
    assert "unimported files are not counted" in cov["scope"]
    assert "5 Python source files" in cov["scope"]


# ---------------------------------------------------------------- 10. ledger and API


class _Api(ConfidenceAPI):
    def __init__(self, root):
        self.root = root

    def run_path(self, key):
        return self.root


def test_triage_is_written_once_per_case_and_attempt_to_its_own_file(tmp_path):
    first = ledger.append_triage(
        tmp_path, case_id="TC-1", attempt=1, epoch=0, data={"classification": TEST_DEFECT}
    )
    again = ledger.append_triage(
        tmp_path, case_id="TC-1", attempt=1, epoch=0, data={"classification": TEST_DEFECT}
    )
    other_attempt = ledger.append_triage(tmp_path, case_id="TC-1", attempt=2, epoch=0, data={})
    assert first is not None and again is None and other_attempt is not None
    assert len((tmp_path / ledger.TRIAGE).read_text(encoding="utf-8").splitlines()) == 2
    assert not (tmp_path / ledger.LEDGER).exists()


def test_confidence_listing_defaults_to_gate_kinds_and_triage_is_asked_for_explicitly(tmp_path):
    (tmp_path / ledger.LEDGER).write_text(
        json.dumps({"kind": "design", "subject_id": "TC-1", "epoch": 0, "confidence": 97}) + "\n",
        encoding="utf-8",
    )
    ledger.append_triage(tmp_path, case_id="TC-1", attempt=1, epoch=0, data={"classification": TEST_DEFECT})
    api = _Api(tmp_path)
    default = api.confidence("run", {})
    assert [r["kind"] for r in default["records"]] == ["design"]
    triage = api.confidence("run", {"kind": ["execution_triage"]})
    assert [r["kind"] for r in triage["records"]] == ["execution_triage"]
    assert triage["ledger"] == ledger.TRIAGE
    with pytest.raises(ValueError):
        api.confidence("run", {"kind": ["bogus"]})


def test_triage_never_pushes_score_records_out_of_the_window(tmp_path):
    (tmp_path / ledger.LEDGER).write_text(
        json.dumps({"kind": "test", "subject_id": "TC-9", "epoch": 0, "confidence": 96}) + "\n",
        encoding="utf-8",
    )
    for index in range(5):
        ledger.append_triage(tmp_path, case_id=f"TC-{index}", attempt=1, epoch=0, data={})
    assert [r["subject_id"] for r in ledger.entries(tmp_path)] == ["TC-9"]

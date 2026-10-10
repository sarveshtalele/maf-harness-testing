"""Ensure the evaluator rejects false greens and broken fixtures."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_generation.py"
SPEC = importlib.util.spec_from_file_location("generation_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_reference_calibration_detects_all_defects(tmp_path):
    report = benchmark.evaluate(None, tmp_path / "results")
    assert report["mode"] == "reference_calibration"
    assert report["metrics"]["valid_tasks"] == 3
    assert report["metrics"]["killed"] == 5
    assert report["metrics"]["effective_mutation_score"] == 1
    assert json.loads((tmp_path / "results/report.json").read_text()) == report
    with pytest.raises(FileExistsError):
        benchmark.evaluate(None, tmp_path / "results")


def test_false_green_and_fixture_errors_receive_no_credit(tmp_path):
    candidates = tmp_path / "candidates"
    (candidates / "transcript").mkdir(parents=True)
    (candidates / "sqlite").mkdir()
    (candidates / "transcript/test_generated.py").write_text("def test_false_green():\n    assert True\n")
    (candidates / "sqlite/test_generated.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef database():\n"
        "    raise RuntimeError('invalid fixture')\n"
        "\ndef test_summary(database):\n    assert database\n"
    )
    report = benchmark.evaluate(candidates, tmp_path / "results")
    assert report["mode"] == "submitted_candidates"
    assert report["metrics"]["valid_tasks"] == 1
    assert report["metrics"]["mutations"] == 5
    assert report["metrics"]["killed"] == 0
    assert report["tasks"][1]["baseline"]["counts"]["error"] == 1
    assert report["tasks"][2]["reason"] == "missing_candidate_tests"


def test_mutant_setup_error_is_not_a_test_failure(tmp_path):
    tests = tmp_path / "suite"
    tests.mkdir()
    (tests / "test_generated.py").write_text(
        "import pytest\nfrom target import total_tokens\n\n"
        "@pytest.fixture\ndef value():\n"
        "    result = total_tokens({'message': {'usage': {'input_tokens': 10}}})\n"
        "    if result != 10:\n        raise RuntimeError('bad setup')\n"
        "    return result\n\ndef test_total(value):\n    assert value == 10\n"
    )
    baseline = benchmark.trial(benchmark.CORPUS / "transcript/target.py", tests, tmp_path / "baseline", 30)
    assert baseline["valid"]
    result = benchmark.trial(
        benchmark.CORPUS / "transcript/mutations/root_usage.py",
        tests,
        tmp_path / "trial",
        30,
    )
    assert result["counts"]["error"] == 1
    assert result["counts"]["failed"] == 0
    assert not benchmark.detected(baseline, result)


def test_malformed_evidence_cannot_pass(tmp_path, monkeypatch):
    tests = tmp_path / "suite"
    tests.mkdir()
    (tests / "test_generated.py").write_text("def test_ok():\n    assert True\n")

    def interrupted_report(argv, workspace, output, **kwargs):
        (workspace / "results.xml").write_text("<testsuite>")
        return {"exit_code": 0, "status": "command_succeeded"}

    monkeypatch.setattr(benchmark, "run_command", interrupted_report)
    observed = benchmark.trial(benchmark.CORPUS / "transcript/target.py", tests, tmp_path / "trial", 30)
    assert not observed["valid"]
    assert observed["report_error"]
    assert observed["tests"] == 0

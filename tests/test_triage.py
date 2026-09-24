import pytest

from maf_test_harness import triage
from maf_test_harness.qa_report import build_qa_report, render_markdown

PY_TRACE = """def test_x():
>       assert add(1, 2) == 4
E       AssertionError: assert 3 == 4
/w/.harness_runs/r1/workspace/tests/test_x.py:6: AssertionError
/w/.harness_runs/r1/workspace/service.py:2: in add
"""
JAVA = "[ERROR] /w/workspace/src/test/java/org/x/FooTest.java:[21,16] credentials has private access in org.x.Foo"


def test_locations_cover_python_java_js_go_and_dotnet():
    assert (
        ("tests/a.py", 6) in triage.locations("tests/a.py:6: AssertionError")
        and ("Foo.java", 10) in triage.locations("at org.Foo.run(Foo.java:10)")
        and ("src/T.java", 21) in triage.locations("src/T.java:[21,16] error")
        and ("a/b.test.ts", 4) in triage.locations("at Object.<anonymous> (a/b.test.ts:4:11)")
        and ("x_test.go", 8) in triage.locations("x_test.go:8: expected 1")
        and ("a.py", 3) in triage.locations('File "a.py", line 3, in f')
    )


def test_classification_separates_test_defects_from_application_signals():
    assert triage.classify("ModuleNotFoundError: No module named 'x'") == "test_defect"
    assert triage.classify(JAVA) == "test_defect"
    assert triage.classify("AssertionError: assert 3 == 4") == "check_application_then_expectation"
    assert triage.classify("something odd") == "needs_triage"


def test_updates_name_test_file_lines_and_source_file_lines():
    case = {
        "id": "TC-1",
        "title": "add sums",
        "status": "failed",
        "file": "tests/test_x.py",
        "message": PY_TRACE,
    }
    gen = [{"path": "tests/test_x.py", "content": "x", "case_ids": ["TC-1"]}]
    out = triage.build_updates([case], gen, ["service.py"], [], {})
    assert out["generated_test_files_to_update"][0]["file"] == "tests/test_x.py"
    assert out["generated_test_files_to_update"][0]["lines"] == [6]
    assert out["repository_files_to_review"] == [
        {**out["repository_files_to_review"][0], "file": "service.py", "line": 2}
    ]


def test_environment_and_uncovered_requirements_are_actionable():
    out = triage.build_updates(
        [],
        [],
        [],
        [{"id": "REQ-1", "text": "adds", "state": "untested"}],
        {"REQ-1": ["service.py"]},
        {"missing_prerequisites": ["mvn"]},
    )
    assert out["requirements_needing_tests"][0]["source_files_to_cover"] == ["service.py"]
    assert out["environment_actions"][0]["tool"] == "mvn"


def test_qa_markdown_has_a_files_to_update_section():
    design = {
        "positive_tests": [{"id": "TC-1", "title": "add sums", "type": "positive", "traceability_links": []}],
        "traceability_matrix": {},
    }
    gen = {"files": [{"path": "tests/test_x.py", "content": "x", "case_ids": ["TC-1"]}]}
    execution = {
        "status": "failed",
        "cases": [{"name": "test_x", "classname": "tests.test_x", "status": "failed", "message": PY_TRACE}],
    }
    qa = build_qa_report(
        repo={"source_files": ["service.py"]},
        requirements={},
        design=design,
        generation=gen,
        execution=execution,
    )
    md = render_markdown(qa)
    assert "## Files to update" in md and "`tests/test_x.py`" in md and "`service.py:2`" in md


def test_target_source_returns_complete_functions_and_their_helpers_not_a_truncated_head(tmp_path):
    from maf_test_harness.repo_analyzer import extract_target_sources

    (tmp_path / "engine").mkdir()
    filler = "".join(
        f"# filler line {i}\n" for i in range(3000)
    )  # pushes the scorers far past any head excerpt
    (tmp_path / "engine/evaluator.py").write_text(
        "def evaluate(x):\n    return _score_a(x) + _score_b(x)\n\n"
        + filler
        + "def _score_a(x):\n    return 10 if x else 0\n\ndef _score_b(x):\n    return 5\n\ndef unrelated():\n    return 1\n"
    )
    out = extract_target_sources(tmp_path, ["engine/evaluator.py evaluate()"], ["engine/evaluator.py"])
    assert "def _score_a" in out and "return 10 if x else 0" in out and "def _score_b" in out
    assert "unrelated" not in out and "filler line" not in out


def test_target_source_handles_java_and_missing_targets(tmp_path):
    from maf_test_harness.repo_analyzer import extract_target_sources

    (tmp_path / "A.java").write_text("class A {\n  public int total(int a) {\n    return a * 2;\n  }\n}\n")
    out = extract_target_sources(tmp_path, ["A.java total()"], ["A.java"])
    assert "return a * 2" in out
    assert extract_target_sources(tmp_path, ["nothing here"], ["A.java"]) == ""


@pytest.mark.parametrize(
    "message",
    [
        "java.lang.AssertionError: expected:<200> but was:<404>",
        "AssertionError: expected:<201> but was:<405>",
        "requests.exceptions.HTTPError: 405 Client Error: Method Not Allowed",
    ],
)
def test_wrong_url_or_method_failures_point_at_the_route_inventory(message):
    assert "live_app.routes" in triage.remedy_for(message)


def test_generic_catch_all_remedies_give_a_concrete_worked_example_not_just_a_category():
    assert "collaborator_signatures" in triage.remedy_for("cannot find symbol")
    assert "{'user': {'id': 1" in triage.remedy_for("KeyError: 'id'")
    assert "trace the actual" in triage.remedy_for("AssertionError: assert 3 == 4")
    assert "from app.rag_pipeline import get_chain" in triage.remedy_for("cannot import name 'x'")

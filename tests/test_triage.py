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

import pytest

from maf_test_harness.qa import triage
from maf_test_harness.qa.report import build_qa_report, render_markdown

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
    from maf_test_harness.repo.analyzer import extract_target_sources

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
    from maf_test_harness.repo.analyzer import extract_target_sources

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


def _hyphen_repo(init: bool) -> list[str]:
    files = ["skills/text-statistics/scripts/main.py", "app/core.py"]
    return files + (["skills/__init__.py"] if init else [])


@pytest.mark.parametrize("init", [False, True])
def test_hyphenated_skill_import_gets_precise_message_and_remedy(init):
    from maf_test_harness.skills.contracts import check_python_imports

    code = "from skills.text_statistics.scripts.main import run\n"
    with pytest.raises(ValueError) as err:
        check_python_imports(code, _hyphen_repo(init), "", path="tests/test_x.py")
    msg = str(err.value)
    assert "skills/text-statistics/scripts/main.py" in msg
    assert "'text-statistics' is not a valid Python identifier" in msg
    assert "Do not translate '-' to '_'" in msg
    assert "'-' to '_'" in triage.remedy_for(msg)


@pytest.mark.parametrize(
    ("message", "needle"),
    [
        ("import 'a.b' does not match any repository path", "registry"),
        ("Structured output failed schema validation: 1 validation error for X", "one JSON object"),
        ("java.lang.NoSuchFieldException: nameField", "declared in the source"),
        ("@InjectMocks failed: constructor calls getDatabaseConnection", "mockStatic"),
        ("reviewer: insufficient_evidence", "target_source"),
        ("IllegalStateException: Toolkit not initialized", "JFXPanel"),
        ("Error: Cannot find module './x'", "tsconfig"),
        ("error CS0246: The type or namespace name 'Foo' could not be found", "using"),
    ],
)
def test_new_remedies(message, needle):
    assert needle in triage.remedy_for(message)


@pytest.mark.parametrize(
    ("message", "needle"),
    [
        ("ImportError: cannot import name 'x'", "module level"),
        ("ModuleNotFoundError: No module named 'foo'", "module level"),
        ("cannot find symbol", "public API"),
        ("AssertionError: assert 3 == 4", "Recompute"),
    ],
)
def test_old_remedies_unchanged(message, needle):
    assert needle in triage.remedy_for(message)


def test_windows_style_reported_paths_map_to_repo_paths():
    from maf_test_harness.qa.triage import _match

    assert _match("C:\\ws\\.harness_runs\\r1\\src\\app\\x.py", ["src/app/x.py", "src/y.py"]) == "src/app/x.py"

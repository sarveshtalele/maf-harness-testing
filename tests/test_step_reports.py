import json
import zipfile

from maf_test_harness.qa.packaging import build_deliverable
from maf_test_harness.qa.report import build_qa_report, render_markdown
from maf_test_harness.qa.step_reports import STEPS, build_step_reports
from maf_test_harness.skills.config import skills_for
from maf_test_harness.ui.evidence import inventory


def _case(cid, req, level="unit"):
    return {
        "id": cid,
        "title": f"case {cid} | with pipe",
        "type": "positive",
        "level": level,
        "actor": "system",
        "priority": "P1",
        "traceability_links": [{"type": "requirement", "id": req}],
    }


def fixture(tmp_path, *, complete=True):
    reqs = [
        {
            "id": "REQ-1",
            "text": "adds",
            "category": "functional",
            "actor": "system",
            "source_files": ["a.py"],
        },
        {
            "id": "REQ-2",
            "text": "has test",
            "category": "functional",
            "actor": "system",
            "source_files": ["a.py"],
        },
        {
            "id": "REQ-3",
            "text": "excluded",
            "category": "functional",
            "actor": "system",
            "source_files": ["a.py"],
        },
        {
            "id": "REQ-4",
            "text": "gen failed",
            "category": "functional",
            "actor": "system",
            "source_files": ["a.py"],
        },
    ]
    design = {
        "positive_tests": [_case("TC-1", "REQ-1"), _case("TC-2", "REQ-4")],
        "traceability_matrix": {"requirement_to_tests": {"REQ-1": ["TC-1"], "REQ-4": ["TC-2"]}},
        "gaps_report": {
            "uncovered_requirements": ["REQ-2", "REQ-3"],
            "pre_existing_test_witnesses": {"REQ-2": [{"test_file": "t.py", "line": 3}]},
            "excluded_requirements": ["REQ-3"],
        },
        "design_reviews": [
            {"case_id": "TC-1", "verdict": "approved", "confidence": 90, "reason": "ok"},
            {"case_id": "TC-2", "verdict": "approved", "confidence": 80, "reason": "ok"},
        ],
        "host_notes": ["retargeted TC-1 from a to b: reason"],
    }
    generation = {
        "files": [{"path": "tests/test_a.py", "case_ids": ["TC-1"], "content": "def test_x(): pass\n"}],
        "generation_failures": [{"batch": 1, "case_ids": ["TC-2"], "attempts": 3, "last_error": "bad json"}],
        "case_reviews": [{"case_id": "TC-1", "verdict": "supported", "confidence": 88, "reason": "r"}],
        "validation": {"uncovered_case_ids": []},
    }
    execution = {
        "status": "passed",
        "adapter": "python-pytest",
        "cases": [{"name": "test_x", "classname": "tests.test_a", "status": "passed"}],
    }
    arts = {
        "repo_analysis": {
            "repo_name": "demo",
            "repo_url": "https://x/demo",
            "commit_sha": "a" * 40,
            "source_files": ["a.py"],
            "source_file_count": 1,
            "languages": {"Python": 1},
        },
        "requirements": {"requirement_model": {"requirements": reqs, "acceptance_criteria": []}},
        "test_planning": {
            "objective": "o",
            "areas": [{"name": "A", "level": "unit", "risk": "low", "scenarios": ["s"]}],
            "execution_order": ["run"],
        },
        "test_design": design,
        "test_generation": generation,
        "environment_setup": {"status": "completed", "ecosystem": "python", "report": {"steps": []}},
    }
    qa = build_qa_report(
        repo=arts["repo_analysis"],
        requirements=arts["requirements"],
        design=design,
        generation=generation,
        execution=execution,
        generated_at="2026-01-01T00:00:00+00:00",
    )
    arts["test_execution"] = {"qa_report": qa, "execution": execution, "markdown": render_markdown(qa)}
    if not complete:
        for k in ("test_design", "test_generation", "environment_setup", "test_execution"):
            arts.pop(k)
    (tmp_path / "audit.jsonl").write_text(
        '{"event":"a","at":"2026-01-01T00:00:00+00:00"}\n{"event":"b","at":"2026-01-02T03:04:05+00:00"}\n'
    )
    return {
        "repo_url": "https://x/demo",
        "run_name": "Nightly",
        "artifacts": arts,
        "stage_models": {"test_design": "m-1"},
        "budget": {"calls": 3, "reported_tokens": 10, "charged": 1},
    }


def test_complete_run_fills_every_template_deterministically(tmp_path):
    state = fixture(tmp_path)
    reports = build_step_reports(tmp_path, state)
    assert list(reports) == list(STEPS)
    assert reports == build_step_reports(tmp_path, state)
    assert not any("$meta" in r or "$checks" in r or "Not run: this step" in r for r in reports.values())
    design = reports["04-test-design"]
    assert "| Run name | Nightly |" in design and "2026-01-02T03:04:05+00:00" in design and "m-1" in design
    assert "retargeted TC-1" in design and "3 model calls" in design and "t.py:3" in design
    assert "case TC-1 \\| with pipe" in design  # pipes in cells are escaped
    gen = reports["05-test-generation"]
    assert (
        "bad json" in gen
        and "| Every case has exactly one file or a recorded generation failure | PASS" in gen
    )
    assert "| No generation failures | FAIL" in gen


def test_checks_are_computed_from_artifacts(tmp_path):
    state = fixture(tmp_path)
    assert "| Every requirement accounted for" in build_step_reports(tmp_path, state)["04-test-design"]
    assert (
        "REQ-3"
        not in build_step_reports(tmp_path, state)["04-test-design"]
        .split("Unexplained gaps")[1]
        .split("Design notes")[0]
    )
    state["artifacts"]["test_design"]["gaps_report"]["excluded_requirements"] = []
    design = build_step_reports(tmp_path, state)["04-test-design"]
    assert "| No unexplained gaps | FAIL | REQ-3 |" in design
    state["artifacts"]["test_generation"]["files"].append(
        {"path": "x.py", "case_ids": ["TC-1"], "content": "x"}
    )
    assert "| FAIL | TC-1 |" in build_step_reports(tmp_path, state)["05-test-generation"]


def test_missing_stages_render_not_run(tmp_path):
    reports = build_step_reports(tmp_path, fixture(tmp_path, complete=False))
    for name in (
        "04-test-design",
        "05-test-generation",
        "06-environment-setup",
        "07-test-execution",
        "08-qa-verdict",
    ):
        assert "| Step status | Not run |" in reports[name] and "_Not run" in reports[name]
    assert "| Step status | Completed |" in reports["02-requirements"]
    assert set(build_step_reports(tmp_path, {})) == set(STEPS)  # empty state never crashes


def test_python_run_never_mentions_junit(tmp_path):
    state = fixture(tmp_path)
    text = (
        "\n".join(build_step_reports(tmp_path, state).values())
        + state["artifacts"]["test_execution"]["markdown"]
    )
    assert "junit" not in text.lower()


def test_report_states_are_accurate():
    design = {
        "positive_tests": [_case("TC-1", "R1"), _case("TC-2", "R2"), _case("TC-3", "R3")],
        "traceability_matrix": {"requirement_to_tests": {"R1": ["TC-1"], "R2": ["TC-2"], "R3": ["TC-3"]}},
        "gaps_report": {"excluded_requirements": ["R4"]},
    }
    reqs = {"requirement_model": {"requirements": [{"id": r, "text": r} for r in ("R1", "R2", "R3", "R4")]}}
    gen = {
        "files": [
            {"path": "a.py", "case_ids": ["TC-1", "TC-3"], "content": "x"},
        ],
        "generation_failures": [{"batch": 1, "case_ids": ["TC-2"], "attempts": 1, "last_error": "boom"}],
    }
    execution = {
        "status": "passed",
        "cases": [
            {"name": "t1", "classname": "a", "status": "passed"},
            {"name": "t3", "classname": "a", "status": "skipped"},
        ],
    }
    qa = build_qa_report(
        repo={}, requirements=reqs, design=design, generation=gen, execution=execution, generated_at="T"
    )
    states = {r["id"]: r["state"] for r in qa["requirement_coverage"]}
    assert states["R2"] == "generation_failed" and states["R4"] == "untested"
    assert {c["id"]: c["file"] for c in qa["cases"]}["TC-3"] == "a.py"  # all case ids of a file are mapped
    assert next(c for c in qa["cases"] if c["id"] == "TC-2")["message"].endswith("boom")
    assert qa["generated_at"] == "T"
    assert any(f["id"] == "TC-2" for f in qa["failures"])


def test_junit_companion_only_for_java_and_kotlin():
    def companions(langs):
        state = {"artifacts": {"repo_analysis": {"languages": langs}, "test_design": {}}}
        return skills_for("test_generation", state).companions

    assert "junit-generator" not in companions({"Python": 5})
    assert "junit-generator" in companions({"Java": 1}) and "junit-generator" in companions({"Kotlin": 1})


def test_evidence_lists_xml_only_for_maven_style_results(tmp_path):
    for rel in ("executions/a/TEST-x.xml", "executions/a/pytest-results.xml"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("<testsuite/>")
    kinds = {f["name"]: f["kind"] for f in inventory(tmp_path)["files"]}
    assert kinds == {"TEST-x.xml": "test-results"}


def test_zip_contains_step_reports(tmp_path):
    (tmp_path / "repo").mkdir()
    state = fixture(tmp_path)
    with zipfile.ZipFile(build_deliverable(tmp_path, state)) as archive:
        names = archive.namelist()
        assert "harness-report/qa_report.md" in names
        for name in STEPS:
            assert f"harness-report/steps/{name}.md" in names
        assert "Run summary" in archive.read("harness-report/steps/00-summary.md").decode()
        assert (
            json.loads(archive.read("harness-report/qa_report.json"))["generated_at"]
            == "2026-01-01T00:00:00+00:00"
        )

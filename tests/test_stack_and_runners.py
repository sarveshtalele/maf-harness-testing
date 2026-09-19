import json
import sys
from pathlib import Path

import pytest

from maf_test_harness import runners
from maf_test_harness.stack import StackProfile, detect_stack


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("files", "primary", "runner"),
    [
        ({"pyproject.toml": "[project]\nname='x'\n", "app/x.py": "print(1)\n"}, "python", "pytest"),
        (
            {"package.json": json.dumps({"devDependencies": {"vitest": "1"}}), "src/a.ts": "export {}\n"},
            "node",
            "vitest",
        ),
        ({"package.json": json.dumps({"devDependencies": {"@playwright/test": "1"}})}, "node", "playwright"),
        ({"pom.xml": "<project/>", "src/main/java/A.java": "class A {}\n"}, "maven", "maven-surefire"),
        ({"build.gradle": "", "src/A.java": "class A {}\n"}, "gradle", "gradle"),
        ({"go.mod": "module x\n", "a.go": "package a\n"}, "go", "go-test"),
        ({"App.csproj": "<Project/>", "P.cs": "class P {}\n"}, "dotnet", "dotnet-test"),
    ],
)
def test_detect_stack_primary_and_runner(tmp_path, files, primary, runner):
    profile = detect_stack(write(tmp_path, files))
    assert profile.primary == primary and profile.test_runner == runner


def test_detect_stack_flags_ui_service_and_specs(tmp_path):
    profile = detect_stack(
        write(
            tmp_path,
            {
                "pom.xml": "<project/>",
                "src/A.java": '@Path("/x") class A {}\n',
                "web/index.xhtml": "<html/>",
                "docs/openapi.yaml": "openapi: 3.0.0\n",
                ".github/workflows/ci.yml": "on: push\n",
                "Dockerfile": "FROM x\n",
            },
        )
    )
    assert profile.web_ui and profile.http_service
    assert profile.openapi_files == ["docs/openapi.yaml"] and profile.ci_files and profile.container_files


def test_bad_package_json_does_not_crash(tmp_path):
    assert detect_stack(write(tmp_path, {"package.json": "[1,2,3]"})).primary == "node"


def files(*paths, content="x"):
    return [{"path": p, "content": content, "case_ids": [f"TC-{i}"]} for i, p in enumerate(paths)]


def test_python_plan_installs_into_venv_and_scopes_pytest_to_generated_files(tmp_path):
    ws = write(tmp_path, {"requirements.txt": "requests\n"})
    plan = runners.build_plan(
        StackProfile(primary="python"), ws, files("tests/test_a.py"), cache_dir=tmp_path / "c"
    )
    names = [s.name for s in plan.steps]
    assert names == ["create-venv", "install-pytest", "install-requirements", "pytest"]
    assert plan.steps[-1].argv[-1] == "tests/test_a.py" and plan.steps[-1].parser == "junit"
    assert all(s.kind == "setup" for s in plan.steps[:-1])


def test_generated_file_type_beats_repo_ecosystem(tmp_path):
    js = files("e2e/a.spec.ts", content="import { test } from '@playwright/test'")
    plan = runners.build_plan(
        StackProfile(primary="maven"), write(tmp_path, {"pom.xml": ""}), js, cache_dir=tmp_path / "c"
    )
    if plan.blocked_reason:  # node/npm not installed on this host
        assert "not installed" in plan.blocked_reason
    else:
        assert plan.adapter == "node-playwright" and "install-playwright" in [s.name for s in plan.steps]


def test_missing_toolchain_is_reported_not_faked(tmp_path, monkeypatch):
    monkeypatch.setattr(runners.shutil, "which", lambda name: None)
    plan = runners.build_plan(
        StackProfile(primary="go"), tmp_path, files("a_test.go"), cache_dir=tmp_path / "c"
    )
    assert plan.steps == [] and "not installed" in plan.blocked_reason
    report = runners.execute_plan(plan, tmp_path, tmp_path / "out")
    assert report["status"] == "blocked"


def test_jest_go_and_trx_parsers(tmp_path):
    jest = tmp_path / "jest.json"
    jest.write_text(
        json.dumps(
            {
                "testResults": [
                    {
                        "name": "a.test.js",
                        "assertionResults": [
                            {"title": "ok", "status": "passed", "failureMessages": []},
                            {"title": "bad", "status": "failed", "failureMessages": ["boom"]},
                            {"title": "later", "status": "pending", "failureMessages": []},
                        ],
                    }
                ]
            }
        )
    )
    assert runners.summarize(runners.parse_jest_json(jest)) == {
        "tests": 3,
        "passed": 1,
        "failed": 1,
        "errors": 0,
        "skipped": 1,
    }
    go = tmp_path / "go.log"
    go.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"Action": "run", "Package": "p", "Test": "TestA"},
                {"Action": "output", "Package": "p", "Test": "TestA", "Output": "x"},
                {"Action": "pass", "Package": "p", "Test": "TestA"},
                {"Action": "fail", "Package": "p", "Test": "TestB"},
                {"Action": "fail", "Package": "p"},
            ]
        )
    )
    assert [c["status"] for c in runners.parse_go_json(go)] == ["passed", "failed"]
    trx = tmp_path / "r.trx"
    trx.write_text(
        '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>'
        '<UnitTestResult testName="A" outcome="Passed"/><UnitTestResult testName="B" outcome="Failed">'
        "<Output><ErrorInfo><Message>bad</Message></ErrorInfo></Output></UnitTestResult></Results></TestRun>"
    )
    assert [(c["name"], c["status"]) for c in runners.parse_trx(trx)] == [("A", "passed"), ("B", "failed")]


def test_results_map_to_approved_cases_by_declared_name():
    generated = [
        {"path": "tests/test_a.py", "content": "def test_alpha():\n    assert 1\n", "case_ids": ["TC-A"]},
        {
            "path": "src/test/java/BTest.java",
            "content": "class BTest {\n @Test\n public void beta() {}\n}",
            "case_ids": ["TC-B"],
        },
    ]
    mapped = runners.map_results_to_cases(
        [
            {"name": "test_alpha", "status": "passed", "classname": ""},
            {"name": "beta", "status": "failed", "classname": "BTest"},
            {"name": "other", "status": "passed", "classname": "X"},
        ],
        generated,
    )
    assert [m["case_id"] for m in mapped] == ["TC-A", "TC-B", None]


def test_execute_plan_never_passes_without_fresh_reports(tmp_path):
    (tmp_path / ".harness-reports").mkdir()
    stale = tmp_path / ".harness-reports" / "junit.xml"
    stale.write_text('<testsuite><testcase name="old_test"/></testsuite>')
    plan = runners.RunPlan(
        "t",
        [
            runners.Step(
                "noop",
                [sys.executable, "-c", "print('ran nothing')"],
                parser="junit",
                report_glob=".harness-reports/junit.xml",
            )
        ],
    )
    import os

    os.utime(stale, (1, 1))  # an old report left behind must not count as evidence
    report = runners.execute_plan(plan, tmp_path, tmp_path / "out")
    assert report["status"] == "no_test_evidence" and report["cases"] == []


def test_setup_failure_stops_before_tests_and_is_not_a_pass(tmp_path):
    plan = runners.RunPlan(
        "t",
        [
            runners.Step("install", [sys.executable, "-c", "raise SystemExit(3)"], kind="setup"),
            runners.Step(
                "tests", [sys.executable, "-c", "print('never')"], parser="junit", report_glob="x.xml"
            ),
        ],
    )
    report = runners.execute_plan(plan, tmp_path, tmp_path / "out")
    assert report["status"] == "setup_failed" and len(report["steps"]) == 1


def test_secrets_are_not_forwarded_to_test_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_must_not_leak")
    plan = runners.RunPlan(
        "t",
        [
            runners.Step(
                "probe",
                [
                    sys.executable,
                    "-c",
                    "import os; print(os.getenv('OPENAI_API_KEY'), os.getenv('GITHUB_TOKEN'), os.getenv('HOME'))",
                ],
                kind="setup",
            )
        ],
    )
    report = runners.execute_plan(plan, tmp_path, tmp_path / "out")
    out = Path(report["steps"][0]["stdout"]).read_text()
    assert out.startswith("None None") and "must-not-leak" not in out and str(tmp_path) in out


def test_python_adapter_installs_only_allow_listed_helpers_and_pins_rootdir(tmp_path):
    generated = files(
        "tests/test_a.py", content="def test_a(mocker):\n    import evilpkg\n    assert mocker\n"
    )
    plan = runners.build_plan(StackProfile(primary="python"), tmp_path, generated, cache_dir=tmp_path / "c")
    install = next(s for s in plan.steps if s.name == "install-test-helpers")
    assert (
        install.argv[-1] == "pytest-mock" and "evilpkg" not in install.argv
    )  # never installs model-named packages
    pytest_step = plan.steps[-1]
    assert "--rootdir" in pytest_step.argv and "--continue-on-collection-errors" in pytest_step.argv
    assert (tmp_path / ".harness-reports" / "pytest.ini").read_text() == "[pytest]\n"


def test_repository_pytest_config_is_respected(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts='-q'\n")
    plan = runners.build_plan(
        StackProfile(primary="python"), tmp_path, files("tests/test_a.py"), cache_dir=tmp_path / "c"
    )
    assert "--rootdir" not in plan.steps[-1].argv


def test_node_adapter_bootstraps_package_json_for_specs_in_non_node_repos(tmp_path):
    spec = files(
        "e2e/a.spec.ts", content="import { test } from '@playwright/test'\ntest('x', async () => {})\n"
    )
    plan = runners.build_plan(StackProfile(primary="maven"), tmp_path, spec, cache_dir=tmp_path / "c")
    if plan.blocked_reason:
        pytest.skip("node/npm not installed")
    assert json.loads((tmp_path / "package.json").read_text())["private"] is True
    assert plan.steps[0].argv[:2] == ["npm", "install"]


def test_build_failure_keeps_decisive_lines_and_qa_attributes_them_to_the_case(tmp_path):
    from maf_test_harness.qa_report import build_qa_report

    script = (
        "import sys; print('[ERROR] src/test/java/FooIT.java:[4,30] package io.restassured does not exist');"
        "print('noise'); sys.exit(1)"
    )
    plan = runners.RunPlan(
        "t", [runners.Step("mvn", [sys.executable, "-c", script], parser="junit", report_glob="none.xml")]
    )
    report = runners.execute_plan(plan, tmp_path, tmp_path / "out")
    assert report["status"] == "no_test_evidence" and "restassured" in report["diagnostics"][0]
    design = {
        "positive_tests": [{"id": "TC-1", "title": "foo", "type": "positive", "traceability_links": []}],
        "traceability_matrix": {},
    }
    generation = {"files": [{"path": "src/test/java/FooIT.java", "content": "x", "case_ids": ["TC-1"]}]}
    qa = build_qa_report(repo={}, requirements={}, design=design, generation=generation, execution=report)
    assert (
        qa["verdict"] == "INCONCLUSIVE"
        and "package io.restassured does not exist" in qa["cases"][0]["message"]
    )


def test_uncompilable_generated_files_are_quarantined_so_the_rest_still_run(tmp_path):
    """Compiled-language analogue of pytest's --continue-on-collection-errors."""
    from maf_test_harness.qa_report import build_qa_report

    (tmp_path / "src/test/java").mkdir(parents=True)
    bad, good = "src/test/java/BadTest.java", "src/test/java/GoodTest.java"
    (tmp_path / bad).write_text("broken")
    (tmp_path / good).write_text("fine")
    build = (
        "import pathlib, sys\n"
        "if pathlib.Path('src/test/java/BadTest.java').exists():\n"
        "    print('[ERROR] /w/src/test/java/BadTest.java:[3,1] cannot find symbol'); sys.exit(1)\n"
        "pathlib.Path('.harness-reports').mkdir(exist_ok=True)\n"
        "pathlib.Path('.harness-reports/junit.xml').write_text('<testsuite><testcase name=\"ok\" classname=\"GoodTest\"/></testsuite>')\n"
    )
    plan = runners.RunPlan(
        "java-maven",
        [
            runners.Step(
                "mvn", [sys.executable, "-c", build], parser="junit", report_glob=".harness-reports/junit.xml"
            )
        ],
    )
    report = runners.execute_plan(plan, tmp_path, tmp_path / "out", [bad, good])
    assert report["quarantined"] == [bad] and report["summary"]["passed"] == 1
    assert report["status"] == "failed"  # a quarantined file means the suite is not fully green
    assert (tmp_path / ".harness-quarantine" / bad).exists() and not (tmp_path / bad).exists()
    design = {
        "positive_tests": [
            {"id": "TC-B", "title": "bad", "type": "positive", "traceability_links": []},
            {"id": "TC-G", "title": "good", "type": "positive", "traceability_links": []},
        ],
        "traceability_matrix": {},
    }
    generation = {
        "files": [
            {"path": bad, "content": "x", "case_ids": ["TC-B"]},
            {"path": good, "content": "GoodTest {\n @Test public void ok() {}\n}", "case_ids": ["TC-G"]},
        ]
    }
    qa = build_qa_report(repo={}, requirements={}, design=design, generation=generation, execution=report)
    by = {c["id"]: c for c in qa["cases"]}
    assert by["TC-B"]["status"] == "error" and "cannot find symbol" in by["TC-B"]["message"]
    assert by["TC-G"]["status"] == "passed" and qa["verdict"] == "FAIL"


def test_collection_errors_map_back_to_their_case_by_dotted_module_name():
    """pytest reports collection failures with the dotted module path as the name and no class."""
    generated = [
        {
            "path": "tests/batch_07/test_x.py",
            "content": "def test_x():\n    assert 1\n",
            "case_ids": ["TC-X"],
        },
        {
            "path": "tests/batch_07/test_xy.py",
            "content": "def test_xy():\n    assert 1\n",
            "case_ids": ["TC-XY"],
        },
    ]
    mapped = runners.map_results_to_cases(
        [{"name": "tests.batch_07.test_xy", "classname": "", "status": "error", "message": "ImportError"}],
        generated,
    )
    assert mapped[0]["case_id"] == "TC-XY"  # not TC-X: matching is on the whole dotted stem, not a substring


def test_python_runner_adds_non_package_directories_to_the_import_path(tmp_path):
    (tmp_path / "skill-packages" / "03-agent").mkdir(parents=True)
    (tmp_path / "skill-packages" / "03-agent" / "agent.py").write_text("class Agent: ...\n")
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "core.py").write_text("x = 1\n")
    plan = runners.build_plan(
        StackProfile(primary="python"), tmp_path, files("tests/test_a.py"), cache_dir=tmp_path / "c"
    )
    path = plan.steps[-1].env["PYTHONPATH"].split(":")
    assert str(tmp_path / "skill-packages" / "03-agent") in path and str(tmp_path / "engine") not in path

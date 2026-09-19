"""End-to-end workflow including the host execution stage, QA report and human-approved heal loop.

Only the model call is scripted. Export, stack detection, planning, subprocess execution, JUnit parsing,
mapping to approved cases and the QA report are all real.
"""

import json
import sys
from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan

import maf_test_harness.workflow as wf
from maf_test_harness.runners import RunPlan, Step
from maf_test_harness.runtime_service import RuntimeService


def local_pytest_plan(stack, workspace, files, *, cache_dir, timeout=900, env_ready=False):
    """Same shape as the python adapter, minus venv/pip (no network in unit tests)."""
    step = Step(
        "pytest",
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "--junitxml=.harness-reports/junit.xml",
            *[f["path"] for f in files],
        ],
        env={"PYTHONPATH": str(workspace)},
        parser="junit",
        report_glob=".harness-reports/junit.xml",
    )
    return RunPlan("python-pytest", [step])


@pytest.fixture
def drive(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_clone := clone_fixture)
    monkeypatch.setattr(wf, "build_plan", local_pytest_plan)
    monkeypatch.setattr(wf, "build_environment_plan", local_env_plan)
    service = RuntimeService()

    def make(runtime):
        host = wf.Harness(
            tmp_path / "run", skills_root=Path("skills"), runtime=runtime, cache_dir=tmp_path / "cache"
        )
        service.submit(host.start("https://github.com/acme/demo")).result(15)

        def answer(**response):
            return service.submit(host.respond(host.view["pending"]["id"], response)).result(120)

        return host, answer

    yield make
    service.close()
    assert clone_clone is clone_fixture


def to_execution_gate(host, answer):
    # clone, repo, requirements, env install (tool), env review, plan, design, generation
    for _ in range(8):
        answer(decision="approve")
    assert host.view["pending"]["stage"] == "test_execution" and host.view["pending"]["kind"] == "tool"


def test_execution_stage_runs_real_tests_and_builds_pass_verdict(drive):
    host, answer = drive(ScriptedStageRuntime())
    to_execution_gate(host, answer)
    details = host.view["pending"]["details"]
    assert details["adapter"] == "python-pytest" and "pytest" in details["steps"][0]["command"]
    assert (Path(details["workspace"]) / "service.py").exists()  # repo copy, not the original checkout
    assert "ready" in details["environment"]
    assert not (host.run_dir / "repo" / "tests").exists()  # generated files never touch the clone
    answer(decision="approve")  # run the tests
    assert host.view["pending"]["kind"] == "review"
    qa = host.state["artifacts"]["test_execution"]["qa_report"]
    assert qa["verdict"] == "PASS" and qa["case_status_counts"] == {"passed": 1}
    assert qa["requirement_coverage"][0]["state"] == "verified"
    assert qa["cases"][0]["level"] == "unit" and qa["cases"][0]["actor"] == "Developer"
    final = answer(decision="approve")
    assert final["status"] == "complete" and "PASS" in host.state["completed_reason"]
    assert_deliverable(host)
    import json

    names = [json.loads(line)["event"] for line in (host.run_dir / "audit.jsonl").read_text().splitlines()]
    assert names.count("execution_started") == 1 and names.count("execution_completed") == 1


def test_failing_test_yields_fail_verdict_and_human_approved_heal_loop(drive):
    runtime = ScriptedStageRuntime(test_body="assert add(1, 2) == 4")
    host, answer = drive(runtime)
    to_execution_gate(host, answer)
    answer(decision="approve")
    qa = host.state["artifacts"]["test_execution"]["qa_report"]
    assert qa["verdict"] == "FAIL" and qa["failures"][0]["message"]
    assert qa["requirement_coverage"][0]["state"] == "failing"
    generations = runtime.calls.count("test_generation")
    runtime.test_body = "assert add(1, 2) == 3"  # the 'model' fixes the test from the failure evidence
    answer(decision="revise", feedback="Use the real add semantics.")
    assert host.view["pending"]["stage"] == "test_generation"  # human re-approves regenerated source
    assert runtime.calls.count("test_generation") == generations + 1
    assert "Fix ONLY these failing generated tests" in host.state["feedback"]["test_generation"]
    answer(decision="approve")
    assert host.view["pending"]["stage"] == "test_execution"
    answer(decision="approve")
    assert host.state["artifacts"]["test_execution"]["qa_report"]["verdict"] == "PASS"
    assert host.state["heal_rounds"] == 1


def test_passing_case_is_not_regenerated_during_heal(drive, monkeypatch):
    monkeypatch.setattr(wf, "TEST_GENERATION_BATCH_SIZE", 1)
    bad_case = {
        "title": "add rejects wrong total",
        "priority": "P1",
        "level": "unit",
        "actor": "Developer",
        "target": "service.add",
        "description": "Wrong expectation.",
        "preconditions": [],
        "data_notes": "2 and 2",
        "expected_result": "add(2, 2) is not 5",
        "steps": ["Call add", "Compare"],
    }
    runtime = ScriptedStageRuntime(extra_cases=[bad_case])
    host, answer = drive(runtime)
    to_execution_gate(host, answer)
    answer(decision="approve")
    qa = host.state["artifacts"]["test_execution"]["qa_report"]
    assert qa["case_status_counts"] == {"passed": 1, "failed": 1}
    good_id = next(c["id"] for c in qa["cases"] if c["status"] == "passed")
    seen = []
    original = runtime.run_stage

    async def spy(**kwargs):
        if kwargs["stage"] == "test_generation":
            import json

            seen.extend(c["id"] for c in json.loads(kwargs["prompt"])["approved_inputs"]["approved_cases"])
        return await original(**kwargs)

    runtime.run_stage = spy
    answer(decision="revise", feedback="")
    assert good_id not in seen and len(seen) == 1  # only the failing case went back to the model


def test_execution_can_be_skipped_and_reports_inconclusive(drive):
    host, answer = drive(ScriptedStageRuntime())
    to_execution_gate(host, answer)
    final = answer(decision="skip")
    assert final["status"] == "complete"
    qa = host.state["artifacts"]["test_execution"]["qa_report"]
    assert qa["verdict"] == "INCONCLUSIVE" and qa["execution_status"] == "not_run"
    assert qa["cases"][0]["status"] == "not_run"


def test_execution_plan_cannot_change_after_approval_was_shown(drive):
    host, answer = drive(ScriptedStageRuntime())
    to_execution_gate(host, answer)
    host.view["pending"]["state"]["execution_plan"]["hash"] = "tampered"
    answer(decision="approve")
    assert host.view["pending"]["kind"] == "error" and "plan changed" in host.view["pending"]["error"]


def test_app_url_must_be_local(tmp_path):
    service = RuntimeService()
    host = wf.Harness(tmp_path / "r", skills_root=Path("skills"))
    try:
        with pytest.raises(ValueError, match="local http"):
            service.submit(
                host.start("https://github.com/acme/demo", app_url="http://evil.example.com")
            ).result(5)
    finally:
        service.close()


def test_model_stage_is_repaired_with_the_host_error_instead_of_repeating_itself(tmp_path, monkeypatch):
    """Requirements citing a file that was never supplied are sent back with the reason (temperature 0 repeats)."""
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    runtime = ScriptedStageRuntime()
    original = runtime.run_stage
    prompts = []

    async def flaky(**kwargs):
        if kwargs["stage"] == "requirements":
            prompts.append(kwargs["prompt"])
            result = await original(**kwargs)
            if len(prompts) == 1:
                result["artifact"]["requirements"][0]["source_files"] = ["ghost/Nope.java"]
            return result
        return await original(**kwargs)

    runtime.run_stage = flaky
    service = RuntimeService()
    host = wf.Harness(tmp_path / "r", skills_root=Path("skills"), runtime=runtime, execute=False)
    try:
        service.submit(host.start("https://github.com/acme/demo")).result(15)
        for _ in range(2):
            service.submit(host.respond(host.view["pending"]["id"], {"decision": "approve"})).result(30)
    finally:
        service.close()
    assert host.view["pending"]["kind"] == "review" and host.view["pending"]["stage"] == "requirements"
    assert (
        len(prompts) == 2 and "ghost/Nope.java" in prompts[1] and "rejected by host validation" in prompts[1]
    )
    assert '"evidence_file_paths":["service.py"]' in prompts[0]


def test_design_must_cover_every_requirement_or_state_why(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    runtime = ScriptedStageRuntime()
    original = runtime.run_stage
    prompts = []

    async def two_requirements(**kwargs):
        result = await original(**kwargs)
        if kwargs["stage"] == "requirements":
            extra = dict(
                result["artifact"]["requirements"][0],
                text="add rejects non-numbers",
                evidence="service.py: no type check",
            )
            result["artifact"]["requirements"].append(extra)
        if kwargs["stage"] == "test_design":
            prompts.append(kwargs["prompt"])
            if (
                len(prompts) == 2
            ):  # the model reacts to the repair message by covering the missing requirement
                reqs = wf.json.loads(kwargs["prompt"])["approved_inputs"]["requirements"]["requirements"]
                case = dict(result["artifact"]["positive_tests"][0], title="add rejects strings")
                case["traceability_links"] = [{"type": "requirement", "id": reqs[1]["id"]}]
                result["artifact"]["negative_tests"] = [case]
        return result

    runtime.run_stage = two_requirements
    service = RuntimeService()
    host = wf.Harness(tmp_path / "r", skills_root=Path("skills"), runtime=runtime, execute=False)
    try:
        service.submit(host.start("https://github.com/acme/demo")).result(15)
        for _ in range(4):  # clone, repo, requirements, plan
            service.submit(host.respond(host.view["pending"]["id"], {"decision": "approve"})).result(30)
    finally:
        service.close()
    assert host.view["pending"]["stage"] == "test_design" and host.view["pending"]["kind"] == "review"
    assert len(prompts) == 2 and "have no test case" in prompts[1] and "add rejects non-numbers" in prompts[1]
    assert host.state["artifacts"]["test_design"]["summary"]["coverage_percentage"] == 100.0


def assert_deliverable(host):
    import json
    import zipfile

    archive = zipfile.ZipFile(host.state["deliverable_zip"])
    names = set(archive.namelist())
    generated = [f["path"] for f in host.state["artifacts"]["test_generation"]["files"]]
    assert "demo/service.py" in names  # the complete repository...
    assert all(f"demo/{path}" in names for path in generated)  # ...with every generated test at its real path
    assert {
        "harness-report/qa_report.md",
        "harness-report/qa_report.json",
        "harness-report/RUN_TESTS.md",
        "harness-report/requirements.json",
        "harness-report/test_design.json",
        "harness-report/audit.jsonl",
        "harness-report/environment.json",
    } <= names
    assert not any(".git/" in n or ".harness-venv" in n or "node_modules" in n for n in names)
    assert json.loads(archive.read("harness-report/qa_report.json"))["verdict"] == "PASS"
    assert "## Files to update" in archive.read("harness-report/qa_report.md").decode()
    assert "sk-" not in "".join(
        archive.read(n).decode("utf-8", "ignore") for n in names if n.endswith((".md", ".json", ".jsonl"))
    )


def test_environment_is_planned_then_installed_only_after_tool_approval(drive):
    host, answer = drive(ScriptedStageRuntime())
    for _ in range(3):  # clone, repo, requirements
        answer(decision="approve")
    assert host.state["artifacts"]["requirements"]["environment_plan"][
        "ecosystem"
    ]  # shown at requirements review
    pending = host.view["pending"]
    assert pending["stage"] == "environment_setup" and pending["kind"] == "tool"
    assert not (host.run_dir / "environment").exists()  # nothing installed before approval
    assert pending["details"]["steps"][0]["name"] == "noop-install"
    answer(decision="approve")
    assert (host.run_dir / "environment" / "attempt-1" / "report.json").exists()
    review = host.view["pending"]
    assert review["kind"] == "review" and review["details"]["artifact"]["status"] == "ready"
    answer(decision="approve")
    assert host.view["pending"]["stage"] == "test_planning"


def test_environment_install_can_be_skipped_and_plan_tampering_is_refused(drive):
    host, answer = drive(ScriptedStageRuntime())
    for _ in range(3):
        answer(decision="approve")
    host.view["pending"]["state"]["environment"]["hash"] = "tampered"
    answer(decision="approve")
    assert host.view["pending"]["kind"] == "error" and "plan changed" in host.view["pending"]["error"]
    answer(decision="retry")
    answer(decision="skip")
    assert host.view["pending"]["details"]["artifact"]["status"] == "skipped"


def test_missing_system_tools_are_reported_with_install_hints_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    fake = {
        "ecosystem": "maven",
        "isolation": "x",
        "steps": [],
        "notes": ["Install first: mvn"],
        "prerequisites": [
            {"tool": "mvn", "present": False, "version": "", "install_hint": "brew install maven"}
        ],
        "missing_prerequisites": ["mvn"],
    }
    monkeypatch.setattr(wf, "build_environment_plan", lambda *a, **k: fake)
    service = RuntimeService()
    host = wf.Harness(
        tmp_path / "r", skills_root=Path("skills"), runtime=ScriptedStageRuntime(), cache_dir=tmp_path / "c"
    )
    try:
        service.submit(host.start("https://github.com/acme/demo")).result(15)
        for _ in range(3):
            service.submit(host.respond(host.view["pending"]["id"], {"decision": "approve"})).result(30)
    finally:
        service.close()
    pending = host.view["pending"]
    assert pending["kind"] == "review" and pending["details"]["artifact"]["status"] == "blocked"
    assert pending["details"]["artifact"]["prerequisites"][0]["install_hint"] == "brew install maven"


def test_qa_outputs_follow_the_vendored_skill_contracts_and_are_validated_by_their_scripts(drive):
    import json
    import zipfile

    host, answer = drive(ScriptedStageRuntime())
    to_execution_gate(host, answer)
    answer(decision="approve")
    artifact = host.state["artifacts"]["test_execution"]
    checks = artifact["qa_report"]["skill_checks"]
    assert (
        checks["traceability"]["passed"]
        and checks["traceability"]["script"] == "scripts/validate_traceability.py"
    )
    assert (
        len(checks["traceability"]["sha256"]) == 64
    )  # the reviewed, hash-checked upstream script really ran
    assert checks["coverage"]["passed"] and checks["coverage"]["contract"] == "assets/coverage_templates.yaml"
    matrix = artifact["skill_reports"]["traceability"]["traceability_matrix"]
    assert matrix[0]["artifact_type"] == "generated_test" and matrix[0]["status"] == "covered"
    assert artifact["skill_reports"]["coverage"]["summary"]["requirements_covered"] == 1
    assert "## Skill contract checks" in artifact["markdown"]
    answer(decision="approve")
    names = set(zipfile.ZipFile(host.state["deliverable_zip"]).namelist())
    assert {"harness-report/traceability-matrix.json", "harness-report/coverage-summary.json"} <= names
    assert json.loads(
        zipfile.ZipFile(host.state["deliverable_zip"]).read("harness-report/coverage-summary.json")
    )["summary"]


def test_skills_are_selected_from_the_stack_and_the_approved_design():
    from maf_test_harness.skills_config import skills_for

    base = {"artifacts": {"repo_analysis": {"languages": {"Python": 3}}}, "stack": {}}
    assert skills_for("requirements", base).all == ["repository-requirements"]
    assert skills_for("requirements", {**base, "stack": {"openapi_files": ["api.yaml"]}}).all == [
        "repository-requirements",
        "api-contract-analyzer",
    ]
    assert skills_for("test_design", base).all == ["test-design-generator", "test-data-generator"]
    assert skills_for("test_generation", base).all == ["generic-test-generator", "test-data-generator"]
    java = {
        "artifacts": {
            "repo_analysis": {"languages": {"Java": 9}},
            "test_design": {"positive_tests": [{"level": "unit"}, {"level": "e2e"}]},
        }
    }
    assert skills_for("test_generation", java).all == [
        "generic-test-generator",
        "test-data-generator",
        "junit-generator",
        "playwright-test-generator",
    ]


def test_heal_regenerates_only_failing_cases_even_inside_a_shared_batch(drive):
    """Batch size is 3, both cases share one batch; the passing case's file must stay byte for byte identical."""
    import json

    bad_case = {
        "title": "add rejects wrong total",
        "priority": "P1",
        "level": "unit",
        "actor": "Developer",
        "target": "service.add",
        "description": "Wrong expectation.",
        "preconditions": [],
        "data_notes": "2 and 2",
        "expected_result": "add(2, 2) is not 5",
        "steps": ["Call add", "Compare"],
    }
    runtime = ScriptedStageRuntime(extra_cases=[bad_case])
    host, answer = drive(runtime)
    to_execution_gate(host, answer)
    answer(decision="approve")
    qa = host.state["artifacts"]["test_execution"]["qa_report"]
    assert qa["case_status_counts"] == {"passed": 1, "failed": 1}
    passing = {f["path"]: f["content"] for f in host.state["artifacts"]["test_generation"]["files"]}
    good_id = next(c["id"] for c in qa["cases"] if c["status"] == "passed")
    sent, original = [], runtime.run_stage

    async def spy(**kwargs):
        if kwargs["stage"] == "test_generation":
            body = json.loads(kwargs["prompt"])
            sent.append([c["id"] for c in body["approved_inputs"]["approved_cases"]])
            sent_requirements.append(
                [r["id"] for r in body["approved_inputs"]["requirements"]["requirements"]]
            )
        return await original(**kwargs)

    sent_requirements: list = []
    runtime.run_stage = spy
    answer(decision="revise", feedback="")
    assert sent == [[next(c["id"] for c in qa["cases"] if c["status"] == "failed")]]  # only the failing case
    assert (
        host.state["feedback"]["test_generation"].count("REMEDY") == 0
        or "Recompute the expected value" in host.state["feedback"]["test_generation"]
    )
    regenerated = {f["path"]: f["content"] for f in host.state["artifacts"]["test_generation"]["files"]}
    good_path = next(p for p, c in passing.items() if good_id in c)
    assert regenerated[good_path] == passing[good_path]  # untouched
    assert len(regenerated) == 2


def test_failure_remedies_are_concrete_and_reach_the_regeneration_feedback():
    from maf_test_harness.triage import remedy_for

    assert "builtins.input" in remedy_for("OSError: pytest: reading from stdin while output is captured")
    assert "Import or define" in remedy_for("NameError: name 'patch' is not defined")
    assert "monkeypatch.setattr" in remedy_for("AttributeError: readonly attribute")
    assert "full shape" in remedy_for("KeyError: 'name'")
    assert remedy_for("something unrelated") == ""


def test_generation_prompts_carry_only_the_requirements_the_batch_traces_to():
    from maf_test_harness.workflow import _requirements_for

    requirements = {
        "requirement_model": {
            "requirements": [{"id": "R1", "text": "a"}, {"id": "R2", "text": "b"}],
            "acceptance_criteria": [
                {"id": "AC1", "requirement_id": "R1", "text": "x"},
                {"id": "AC2", "requirement_id": "R2", "text": "y"},
            ],
            "validation_rules": [],
        },
        "api_contracts": [],
        "actors": [{"name": "Dev"}],
    }
    out = _requirements_for(requirements, {"R1"})
    assert [r["id"] for r in out["requirements"]] == ["R1"] and [
        c["id"] for c in out["acceptance_criteria"]
    ] == ["AC1"]
    assert out["actors"] == [{"name": "Dev"}]


def test_new_remedies_cover_fixtures_containers_validation_providers_and_deep_nesting():
    from maf_test_harness.triage import remedy_for

    assert "parameter of the test function" in remedy_for("NameError: name 'capsys' is not defined")
    assert "Bean Validation" in remedy_for(
        "Unable to create a Configuration, because no Bean Validation provider could be found"
    )
    assert "Arquillian" in remedy_for(
        "DeploymentScenario contains a target (_DEFAULT_) not matching any defined Container"
    )
    assert "nest more than 5" in remedy_for("SyntaxError: too many statically nested blocks")


def test_remedies_for_mocked_exit_and_patching_missing_module_attributes():
    from maf_test_harness.triage import remedy_for

    mocked_exit = "AttributeError: 'NoneType' object has no attribute 'rstrip'\n    mock_exit = mocker.patch.object(sys, \"exit\")\n"
    assert "pytest.raises(SystemExit)" in remedy_for(mocked_exit)
    assert "sys.modules" in remedy_for(
        "AttributeError: module 'engine.ai_analysis' has no attribute 'anthropic'"
    )


def test_missing_import_is_repaired_by_the_host_without_another_model_call(drive):
    runtime = ScriptedStageRuntime(test_body="assert add(1, 2) == 3 and os.sep")
    host, answer = drive(runtime)
    to_execution_gate(host, answer)
    source = host.state["artifacts"]["test_generation"]["files"][0]["content"]
    assert "import os" in source
    assert runtime.calls.count("test_generation") == 1  # accepted first time: no repair round trip
    audit = [json.loads(line) for line in (host.run_dir / "audit.jsonl").read_text().splitlines()]
    assert any(e["event"] == "autofix" and e["detail"] == "import os" for e in audit)
    assert host.state["tooling"]["autofix"] == 1
    answer(decision="approve")
    assert host.state["artifacts"]["test_execution"]["qa_report"]["verdict"] == "PASS"

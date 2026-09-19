"""End-to-end workflow including the host execution stage, QA report and human-approved heal loop.

Only the model call is scripted. Export, stack detection, planning, subprocess execution, JUnit parsing,
mapping to approved cases and the QA report are all real.
"""
import sys
from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime, clone_fixture

import maf_test_harness.workflow as wf
from maf_test_harness.runners import RunPlan, Step
from maf_test_harness.runtime_service import RuntimeService


def local_pytest_plan(stack, workspace, files, *, cache_dir, timeout=900):
    """Same shape as the python adapter, minus venv/pip (no network in unit tests)."""
    step = Step("pytest", [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q",
                           "--junitxml=.harness-reports/junit.xml", *[f["path"] for f in files]],
                env={"PYTHONPATH": str(workspace)}, parser="junit", report_glob=".harness-reports/junit.xml")
    return RunPlan("python-pytest", [step])


@pytest.fixture
def drive(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_clone := clone_fixture)
    monkeypatch.setattr(wf, "build_plan", local_pytest_plan)
    service = RuntimeService()

    def make(runtime):
        host = wf.Harness(tmp_path / "run", skills_root=Path("skills"), runtime=runtime, cache_dir=tmp_path / "cache")
        service.submit(host.start("https://github.com/acme/demo")).result(15)

        def answer(**response):
            return service.submit(host.respond(host.view["pending"]["id"], response)).result(120)

        return host, answer

    yield make
    service.close()
    assert clone_clone is clone_fixture


def to_execution_gate(host, answer):
    for _ in range(6):  # clone, repo, requirements, plan, design, generation
        answer(decision="approve")
    assert host.view["pending"]["stage"] == "test_execution" and host.view["pending"]["kind"] == "tool"


def test_execution_stage_runs_real_tests_and_builds_pass_verdict(drive):
    host, answer = drive(ScriptedStageRuntime())
    to_execution_gate(host, answer)
    details = host.view["pending"]["details"]
    assert details["adapter"] == "python-pytest" and "pytest" in details["steps"][0]["command"]
    assert (Path(details["workspace"]) / "service.py").exists()  # repo copy, not the original checkout
    assert not (host.run_dir / "repo" / "tests").exists()  # generated files never touch the clone
    answer(decision="approve")  # run the tests
    assert host.view["pending"]["kind"] == "review"
    qa = host.state["artifacts"]["test_execution"]["qa_report"]
    assert qa["verdict"] == "PASS" and qa["case_status_counts"] == {"passed": 1}
    assert qa["requirement_coverage"][0]["state"] == "verified"
    assert qa["cases"][0]["level"] == "unit" and qa["cases"][0]["actor"] == "Developer"
    final = answer(decision="approve")
    assert final["status"] == "complete" and "PASS" in host.state["completed_reason"]
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
    bad_case = {"title": "add rejects wrong total", "priority": "P1", "level": "unit", "actor": "Developer",
                "target": "service.add", "description": "Wrong expectation.", "preconditions": [],
                "data_notes": "2 and 2", "expected_result": "add(2, 2) is not 5", "steps": ["Call add", "Compare"]}
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
            service.submit(host.start("https://github.com/acme/demo", app_url="http://evil.example.com")).result(5)
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
    assert len(prompts) == 2 and "ghost/Nope.java" in prompts[1] and "rejected by host validation" in prompts[1]
    assert '"evidence_file_paths":["service.py"]' in prompts[0]


def test_design_must_cover_every_requirement_or_state_why(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "clone_repository", clone_fixture)
    runtime = ScriptedStageRuntime()
    original = runtime.run_stage
    prompts = []

    async def two_requirements(**kwargs):
        result = await original(**kwargs)
        if kwargs["stage"] == "requirements":
            extra = dict(result["artifact"]["requirements"][0], text="add rejects non-numbers",
                         evidence="service.py: no type check")
            result["artifact"]["requirements"].append(extra)
        if kwargs["stage"] == "test_design":
            prompts.append(kwargs["prompt"])
            if len(prompts) == 2:  # the model reacts to the repair message by covering the missing requirement
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

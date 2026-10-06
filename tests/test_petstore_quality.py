"""Regression checks for truncated Petstore intake and falsely claimed qualification."""

import asyncio
import json

import pytest

from maf_test_harness.envs.regression import maven_regression_steps
from maf_test_harness.qa.quality import require_complete_intake, validate_java_traceability
from maf_test_harness.qa.report import build_qa_report, render_markdown
from maf_test_harness.repo.requirements import requirements_from_facts
from maf_test_harness.skills.contracts import finalize_discovered_requirements
from maf_test_harness.workflow_steps.design import design_tests_batched, requirement_batches
from maf_test_harness.workflow_steps.generation import GenerationSteps


def inventory(n=223):
    facts = [
        {
            "source_file": f"src/main/java/component{i % 4}/Model.java",
            "line": i + 1,
            "symbol": f"method{i}",
            "confidence": "heuristic",
            "evidence": f"public int method{i}()",
        }
        for i in range(n)
    ]
    draft = requirements_from_facts({"facts": facts}, "Petstore")
    result = finalize_discovered_requirements(draft, "https://github.com/acme/demo")
    result["analysis_mode"] = "deterministic"
    return facts, result


def test_full_intake_and_component_batches_have_no_total_cap():
    facts, requirements = inventory()
    assert len(requirements["requirement_model"]["requirements"]) == 223
    assert requirements["intake"]["represented_declarations"] == 223
    batches = list(requirement_batches(requirements))
    assert sum(len(b["requirement_model"]["requirements"]) for _, b in batches) == 223
    assert max(len(b["requirement_model"]["requirements"]) for _, b in batches) <= 12
    state = {
        "artifacts": {
            "requirements": requirements,
            "repo_analysis": {"reverse_engineering": {"facts": facts}},
        }
    }
    require_complete_intake(state)
    requirements["requirement_model"]["requirements"] = requirements["requirement_model"]["requirements"][:30]
    with pytest.raises(ValueError, match="Incomplete deterministic intake"):
        require_complete_intake(state)


def test_design_batches_merge_all_outputs_and_retry_uses_saved_batches():
    _, requirements = inventory(40)
    state = {
        "repo_url": "https://github.com/acme/demo",
        "artifacts": {"requirements": requirements, "test_planning": {}},
    }

    class Host:
        calls = 0

        async def _model_artifact(self, stage, state, subset, prompt_batch):
            self.calls += 1
            state["draft"] = {"positive_tests": subset["requirement_model"]["requirements"]}

        async def validate(self, stage, state):
            assert len(state["draft"]["positive_tests"]) == 40
            return dict(state["draft"])

        def audit(self, *args, **kwargs):
            pass

        def save(self):
            pass

    host = Host()
    design = asyncio.run(design_tests_batched(host, state))
    calls = host.calls
    assert design["batching"]["approved_requirements"] == 40
    asyncio.run(design_tests_batched(host, state))
    assert host.calls == calls


def java_case(tmp_path, symbol="setCity"):
    source = tmp_path / "src/main/java/Address.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "public class Address {\n@Size(min=2, max=50)\nprivate String city;\npublic Address(String city) {}\npublic void setCity(String city) {}\n}"
    )
    req = {"id": "R1", "source_files": ["src/main/java/Address.java"], "source_symbol": symbol}
    case = {
        "id": "C1",
        "level": "unit",
        "type": "positive",
        "traceability_links": [{"type": "requirement", "id": "R1"}],
    }
    return {"requirement_model": {"requirements": [req]}}, [case]


def test_constructor_does_not_cover_setter_and_positive_fixture_must_be_valid(tmp_path):
    requirements, cases = java_case(tmp_path)

    def check(body):
        return validate_java_traceability(
            {"files": [{"path": "TestAddress.java", "case_ids": ["C1"], "content": body}]},
            cases,
            requirements,
            tmp_path,
        )

    with pytest.raises(ValueError, match="never invokes"):
        check('Address a = new Address("Paris"); assertEquals("Paris", a.getCity());')
    with pytest.raises(ValueError, match="violates source @Size"):
        check('Address a = new Address("A"); a.setCity("B"); assertEquals("B", a.getCity());')
    witness = check(
        'Address a = new Address("Paris"); a.setCity("London"); assertEquals("London", a.getCity()); // a.setCity("B")'
    )
    assert len(witness["java_invocation_witnesses"]) == 1


def test_exact_existing_test_duplicates_are_rejected(tmp_path):
    requirements, cases = java_case(tmp_path, "equals")
    existing = tmp_path / "src/test/java/AddressTest.java"
    existing.parent.mkdir(parents=True)
    existing.write_text("@Test\npublic void old() { EqualsVerifier.forClass(Address.class).verify(); }")
    with pytest.raises(ValueError, match="duplicates src/test/java/AddressTest.java"):
        validate_java_traceability(
            {
                "files": [
                    {
                        "path": "NewTest.java",
                        "case_ids": ["C1"],
                        "content": "@Test\npublic void renamed() { /* duplicated */ EqualsVerifier.forClass(Address.class).verify(); }",
                    }
                ]
            },
            cases,
            requirements,
            tmp_path,
        )


def test_missing_requirement_is_blocked_even_on_last_repair():
    host = GenerationSteps()
    state = {
        "design_attempt": 99,
        "draft": {"gaps": []},
        "artifacts": {"repo_analysis": {}, "requirements": {}},
    }
    with pytest.raises(ValueError, match="requirement.*no test case"):
        host._require_coverage_or_reason({"gaps_report": {"uncovered_requirements": ["R1"]}}, state)


def test_maven_regression_scopes_and_report_do_not_confuse_baseline_with_generated(tmp_path):
    testdir = tmp_path / "src/test/java"
    testdir.mkdir(parents=True)
    (testdir / "AddressTest.java").write_text("@Test public void test() {}")
    (testdir / "ServiceIT.java").write_text("@RunWith(Arquillian.class) @Test public void test() {}")
    (tmp_path / "pom.xml").write_text(
        "<project><profiles><profile><id>arquillian-wildfly-managed</id></profile></profiles></project>"
    )
    steps, expectations, _ = maven_regression_steps(tmp_path, [], ["mvn"], tmp_path / "cache", 60)
    assert [s["scope"] for s in steps] == ["baseline_unit", "baseline_integration"]
    assert "-Dtest=AddressTest" in steps[0]["argv"]
    assert "-Dit.test=ServiceIT" in steps[1]["argv"]
    file = {"path": "test_generated.py", "case_ids": ["C1"], "content": "def test_generated(): pass"}
    execution = {
        "status": "passed",
        "scope_expectations": expectations,
        "cases": [
            {"name": "test_generated", "status": "passed", "scope": "generated"},
            {
                "name": "test_old",
                "classname": "example.AddressTest",
                "status": "passed",
                "scope": "baseline_unit",
            },
        ],
    }
    qa = build_qa_report(
        repo={},
        requirements={},
        design={"positive_tests": [{"id": "C1", "title": "Generated"}]},
        generation={"files": [file]},
        execution=execution,
    )
    assert qa["generated_verdict"] == "PASS" and qa["verdict"] == "INCONCLUSIVE"
    assert qa["unmapped_results"] == []
    assert qa["baseline_regression"][1]["verdict"] == "INCONCLUSIVE"
    assert qa["baseline_regression"][0]["verdict"] == "PASS"
    expectations["baseline_unit"]["classes"].append("MissingTest")
    incomplete = build_qa_report(
        repo={}, requirements={}, design={}, generation={"files": []}, execution=execution
    )
    assert incomplete["baseline_regression"][0]["verdict"] == "INCONCLUSIVE"
    assert incomplete["baseline_regression"][0]["missing_classes"] == ["MissingTest"]
    assert qa["runtime_coverage"]["line"] == "not_measured"
    assert "not measured" in render_markdown(qa)


def test_bad_hyphenated_python_import_gets_source_backed_repair_guidance():
    from maf_test_harness.skills.contracts import check_python_imports

    with pytest.raises(ValueError, match="not a valid Python identifier.*Do not translate"):
        check_python_imports(
            "from skills.text_statistics.scripts.main import run",
            ["skills/text-statistics/scripts/main.py"],
            "",
            "tests/test_run.py",
        )


def test_independent_reviewer_scores_every_case_and_rejects_missing_evidence(tmp_path):
    from maf_test_harness.workflow_steps.test_review import review_generated_tests

    (tmp_path / "repo").mkdir()
    (tmp_path / "repo/service.py").write_text("def add(a, b): return a + b")

    class Host:
        run_dir = tmp_path
        verdict = "supported"
        bad_citation = False
        repairs = []

        async def _call_model(self, **kwargs):
            assert kwargs["role"] == "test_reviewer"
            citation = "service.py"
            if self.bad_citation:
                self.bad_citation = False
                citation = "tests/new_generated.py"
            if feedback := json.loads(kwargs["prompt"]).get("repair_feedback"):
                self.repairs.append(feedback)
            return {
                "status": "completed",
                "artifact": {
                    "reviews": [
                        {
                            "case_id": cid,
                            "confidence": score,
                            "verdict": self.verdict,
                            "reason": "Source supports actual add invocation and its result assertion.",
                            "source_files": [citation],
                        }
                        for cid, score in [("low", 60), ("high", 95)]
                    ]
                },
            }

        def audit(self, *args, **kwargs):
            pass

        def save(self):
            pass

    host = Host()
    state = {"artifacts": {"requirements": {}}}
    cases = [{"id": cid} for cid in ["low", "high"]]
    reviews = asyncio.run(review_generated_tests(host, state, {"files": []}, cases, 1))
    assert [r["case_id"] for r in reviews] == ["high", "low"]
    host.bad_citation = True
    reviews = asyncio.run(review_generated_tests(host, state, {"files": []}, cases, 1))
    assert reviews[0]["source_files"] == ["service.py"]
    assert "not generated test paths" in host.repairs[-1]
    host.verdict = "insufficient_evidence"
    with pytest.raises(ValueError, match="Independent test review requires repair"):
        asyncio.run(review_generated_tests(host, state, {"files": []}, cases, 1))

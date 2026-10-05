from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime, clone_fixture

from maf_test_harness.repo.requirements import requirements_from_facts
from maf_test_harness.repo.reverse_engineering import reverse_engineer
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow import Harness


def test_analysis_is_stable_contained_and_ignores_comments_and_private_symbols(tmp_path):
    (tmp_path / "service.py").write_text(
        "# def fake(): pass\ndef add(a, b):\n    return a + b\ndef _secret(): pass\n"
    )
    result = reverse_engineer(tmp_path, ["service.py", "../outside.py"])
    assert result == reverse_engineer(tmp_path, ["../outside.py", "service.py"])
    assert [f["symbol"] for f in result["facts"]] == ["add"]
    assert result["facts"][0]["line"] == 2
    assert result["model_calls"] == 0 and any("unsafe" in gap for gap in result["gaps"])
    draft = requirements_from_facts(result, "Demo")
    assert draft["requirements"][0]["source_files"] == ["service.py"]
    assert draft["api_contracts"] == [] and draft["acceptance_criteria"] == []
    with pytest.raises(ValueError, match="cannot be invented"):
        requirements_from_facts({"facts": []}, "Unknown")


def test_default_workflow_builds_requirements_without_calling_model(tmp_path, monkeypatch):
    import maf_test_harness.workflow as workflow

    monkeypatch.setattr(workflow, "clone_repository", clone_fixture)
    runtime = ScriptedStageRuntime()
    host = Harness(tmp_path, skills_root=Path("skills"), runtime=runtime)
    service = RuntimeService()
    try:
        service.submit(host.start("https://github.com/acme/demo")).result(30)
        for _ in range(2):
            service.submit(host.respond(host.view["pending"]["id"], {"decision": "approve"})).result(30)
        assert host.view["pending"]["stage"] == "requirements"
        artifact = host.state["artifacts"]["requirements"]
        assert artifact["analysis_mode"] == "deterministic" and artifact["model_calls"] == 0
        assert artifact["metadata"]["generator"] == "requirement-analysis"
        assert runtime.calls == [] and not host.workspace.exists()
        assert host.state["artifacts"]["repo_analysis"]["reverse_engineering"]["facts"]
    finally:
        service.close()


def test_heuristic_lines_and_unsupported_coverage_are_explicit(tmp_path):
    (tmp_path / "surface.ts").write_text("\n\nexport function listItems() { return []; }\n")
    (tmp_path / "surface.rs").write_text("pub fn list_items() {}\n")
    result = reverse_engineer(tmp_path, ["surface.ts", "surface.rs"])
    assert result["coverage"]["read_files"] == 2
    assert result["coverage"]["parsed_files"] == 1
    assert result["facts"][0]["line"] == 3
    assert result["facts"][0]["confidence"] == "heuristic"
    assert any("Unsupported declaration parser: surface.rs" in gap for gap in result["gaps"])
    draft = requirements_from_facts(result, "Demo")
    assert "candidate" in draft["requirements"][0]["text"]


def test_host_skills_do_not_claim_model_companion_execution():
    from maf_test_harness.skills.config import skills_for

    state = {"requirements_mode": "deterministic", "stack": {"openapi_files": ["openapi.json"]}}
    assert skills_for("requirements", state).all == ["requirement-analysis"]
    assert skills_for("repo_analysis", state).all == ["reverse-engineering"]

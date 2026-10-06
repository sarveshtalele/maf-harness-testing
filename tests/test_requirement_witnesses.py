import pytest

from maf_test_harness.qa.quality import existing_requirement_witnesses
from maf_test_harness.qa.report import build_qa_report
from maf_test_harness.workflow_steps.generation import GenerationSteps

SESSION = (
    "class MCPToolClient:\n    async def capabilities(self):\n        return {}\n\n"
    "    def openai_tools(self):\n        return []\n"
)
TEST = (
    "async def test_a(client):\n    caps = await client.capabilities()\n    assert caps == {}\n\n"
    "def test_b(client):\n    assert client.openai_tools() == []\n"
)


def _req(rid, symbol, file="backend/mcp_client/session.py"):
    return {"id": rid, "source_symbol": symbol, "source_files": [file]}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "backend/mcp_client").mkdir(parents=True)
    (root / "backend/tests").mkdir()
    (root / "backend/mcp_client/session.py").write_text(SESSION, encoding="utf-8")
    (root / "backend/tests/test_client.py").write_text(TEST, encoding="utf-8")
    return root


def _host(repo, gaps, requirements):
    host = GenerationSteps()
    host.run_dir = repo.parent
    state = {
        "draft": {"gaps": gaps},
        "artifacts": {
            "repo_analysis": {"test_files": ["backend/tests/test_client.py"]},
            "requirements": {"requirement_model": {"requirements": requirements}},
        },
    }
    return host, state


def test_witnesses_found_and_definitions_ignored(repo):
    reqs = [_req("REQ-1", "capabilities"), _req("REQ-2", "openai_tools")]
    w = existing_requirement_witnesses(repo, ["backend/tests/test_client.py"], reqs)
    assert [r["line"] for r in w["REQ-1"]] == [2] and w["REQ-2"][0]["line"] == 6
    assert existing_requirement_witnesses(repo, ["backend/mcp_client/session.py"], reqs) == {}


def test_generic_symbol_needs_qualifier(repo):
    (repo / "tests").mkdir()
    (repo / "tests/test_other.py").write_text("def test_x():\n    run(1)\n", encoding="utf-8")
    assert existing_requirement_witnesses(repo, ["tests/test_other.py"], [_req("REQ-9", "run")]) == {}


def test_gate_passes_for_witnessed_requirements(repo):
    reqs = [_req("REQ-1", "capabilities"), _req("REQ-2", "openai_tools")]
    host, state = _host(repo, [], reqs)
    design = {"gaps_report": {"uncovered_requirements": ["REQ-1", "REQ-2"]}}
    host._require_coverage_or_reason(design, state)
    assert set(design["gaps_report"]["pre_existing_test_witnesses"]) == {"REQ-1", "REQ-2"}


def test_gap_id_matching_is_exact_and_needs_reason(repo):
    host, state = _host(repo, ["REQ-10: not testable offline because it needs a network"], [])
    design = {"gaps_report": {"uncovered_requirements": ["REQ-1"]}}
    with pytest.raises(ValueError, match="no test case"):
        host._require_coverage_or_reason(design, state)
    state["draft"]["gaps"] = ["REQ-1"]
    with pytest.raises(ValueError, match="no test case"):
        host._require_coverage_or_reason(design, state)
    state["draft"]["gaps"] = ["REQ-1 excluded: needs a live network service"]
    host._require_coverage_or_reason(design, state)
    assert design["gaps_report"]["excluded_requirements"] == ["REQ-1"]


def test_report_shows_requirement_states():
    reqs = {"requirement_model": {"requirements": [{"id": r, "text": r} for r in ("A", "B", "C", "D")]}}
    design = {
        "positive_tests": [{"id": "TC-1", "title": "t", "type": "positive", "traceability_links": []}],
        "traceability_matrix": {"requirement_to_tests": {"A": ["TC-1"]}},
        "gaps_report": {"pre_existing_test_witnesses": {"B": []}, "excluded_requirements": ["C"]},
    }
    qa = build_qa_report(repo={}, requirements=reqs, design=design, generation={}, execution=None)
    assert qa["requirement_state_counts"] == {
        "new": 1,
        "pre_existing_witness": 1,
        "excluded": 1,
        "unexplained": 1,
    }
    assert "not measured" in qa["coverage_scope"]

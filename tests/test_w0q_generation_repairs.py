"""Offline regressions from run 17677c3b6d58 (batches 27/29, 33, 40/48, 39, 54/55)."""

from __future__ import annotations

import pytest

from maf_test_harness.repo.analyzer import collaborator_signatures
from maf_test_harness.skills.contracts import check_python_imports, resolve_id, validate_generated_files
from maf_test_harness.tools.autofix import drop_extra_tests
from maf_test_harness.workflow_support import _canonicalize_case_ids

MAINS = ["scripts/run_all.py"] + [
    f"skills/{n}/scripts/main.py" for n in ("change-impact-analysis", "jira-ticket", "text-statistics")
]


def test_hyphen_import_prefers_linked_source_file():
    code = "from scripts.main import run\n"
    with pytest.raises(ValueError, match=r"target is skills/text-statistics/scripts/main\.py"):
        check_python_imports(code, MAINS, "", "t.py", preferred=["skills/text-statistics/scripts/main.py"])


def test_hyphen_import_lists_candidates_without_hint():
    with pytest.raises(
        ValueError, match="one of: skills/change-impact-analysis.*jira-ticket.*text-statistics"
    ):
        check_python_imports("import scripts.main\n", MAINS, "", "t.py")


APPROVED = "TC-POS-CREATE-APP-BUILDS-A-FASTAPI-APPLICATION-WITH-ROUTERS-AND-BD99BC0B"
EXPANDED = "TC-POS-CREATE-APP-BUILDS-A-FASTAPI-APPLICATION-WITH-ROUTERS-AND-CORS-MIDDLEWARE-BD99BC0B"


def test_expanded_slug_maps_to_approved_id():
    known = {APPROVED, "TC-POS-GET-LOGGER-RETURNS-A-NAMED-LOGGER-INSTANCE-B9B1B2D0"}
    assert resolve_id(EXPANDED, known) == APPROVED
    out = _canonicalize_case_ids({"files": [{"case_ids": [EXPANDED]}]}, known)
    assert out["files"][0]["case_ids"] == [APPROVED]
    assert resolve_id("TC-POS-OTHER-THING-BD99BC0B", known) is None


def test_extra_positive_test_dropped_but_referenced_kept():
    src = (
        "def test_delete_agent_removes():\n    assert 1\n\n\n"
        "def test_delete_agent_unknown_raises_404():\n    assert 2\n"
    )
    item = {"path": "t.py", "case_ids": ["TC-POS-X-12345678"], "content": src}
    fixes = drop_extra_tests(item)
    assert [f["detail"] for f in fixes] == ["test_delete_agent_unknown_raises_404"]
    assert "unknown_raises" not in item["content"] and "def test_delete_agent_removes" in item["content"]
    neg = {"path": "t.py", "case_ids": ["TC-NEG-X-12345678"], "content": src}
    assert drop_extra_tests(neg) == []
    used = {
        "path": "t.py",
        "case_ids": ["TC-POS-X-12345678"],
        "content": src + "\nhelper = test_delete_agent_unknown_raises_404\n",
    }
    assert drop_extra_tests(used) == []


def test_truncated_file_error_names_file_and_last_line():
    content = "def test_a():\n    for entry in data:\n        assert 1\ncase_ids: [\n"
    payload = {"files": [{"path": "tests/t.py", "content": content, "case_ids": ["TC-POS-A-12345678"]}]}
    with pytest.raises(
        ValueError, match=r"tests/t\.py was truncated at line 4 .*regenerate this file completely"
    ):
        validate_generated_files(payload, {"TC-POS-A-12345678"})


def test_constructor_signature_included_when_class_is_target(tmp_path):
    (tmp_path / "registry.py").write_text(
        "class SkillRegistry:\n    def __init__(self, settings, loader, agent_loader=None):\n        pass\n"
        "    def reload(self): pass\n"
    )
    out = collaborator_signatures(tmp_path, "", ["registry.py"], targets=["SkillRegistry.list_agents"])
    assert "def __init__(self, settings, loader, agent_loader=None)" in out

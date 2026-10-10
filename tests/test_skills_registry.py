from pathlib import Path

import pytest

from maf_test_harness.skills.registry import lint_all, lint_skill, require_valid, tree_sha256

SKILLS = Path("skills")


def make(tmp_path, name, frontmatter, body="# T\n"):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
    return d


def test_all_bundled_skills_follow_the_agent_skills_spec():
    reports = lint_all(SKILLS)
    assert [r.name for r in reports] == [
        "api-contract-analyzer",
        "coverage-analyzer",
        "generic-test-generator",
        "junit-generator",
        "playwright-test-generator",
        "repository-requirements",
        "requirement-analysis",
        "reverse-engineering",
        "spec-authoring",
        "test-data-generator",
        "test-design-generator",
        "test-planning",
        "traceability-generator",
    ]
    assert [r.name for r in reports if not r.ok] == []


@pytest.mark.parametrize(
    ("frontmatter", "needle"),
    [
        ("name: Bad_Name\ndescription: d", "name must be"),
        ("name: a--b\ndescription: d", "name must be"),
        ("name: other\ndescription: d", "must match directory"),
        ("name: skill\ndescription: ''", "description is required"),
        ("name: skill\ndescription: " + "x" * 1025, "1024"),
        ("name: skill\ndescription: d\ncompatibility: " + "x" * 501, "compatibility"),
        ("name: skill\ndescription: [unclosed", "valid YAML"),
    ],
)
def test_spec_violations_are_reported(tmp_path, frontmatter, needle):
    report = lint_skill(make(tmp_path, "skill", frontmatter))
    assert not report.ok and any(needle in e for e in report.errors)


def test_missing_frontmatter_and_missing_file(tmp_path):
    (tmp_path / "nofm").mkdir()
    (tmp_path / "nofm" / "SKILL.md").write_text("# no frontmatter")
    (tmp_path / "empty").mkdir()
    assert "no YAML frontmatter" in lint_skill(tmp_path / "nofm").errors[0]
    assert "missing" in lint_skill(tmp_path / "empty").errors[0]


def test_skill_file_over_500_lines_is_an_error(tmp_path):
    report = lint_skill(make(tmp_path, "skill", "name: skill\ndescription: d", "line\n" * 600))
    assert not report.ok and "at most 500" in report.errors[0]
    assert lint_skill(make(tmp_path, "ok", "name: ok\ndescription: d", "line\n" * 400)).ok


def test_allowed_tools_must_be_a_space_delimited_string(tmp_path):
    bad = lint_skill(
        make(tmp_path, "skill", "name: skill\ndescription: d\nallowed-tools:\n  - Read\n  - Write")
    )
    assert not bad.ok and "allowed-tools" in bad.errors[0]
    assert lint_skill(make(tmp_path, "good", "name: good\ndescription: d\nallowed-tools: Read Write")).ok


def test_require_valid_returns_package_hashes_and_rejects_bad_assignments(tmp_path):
    hashes = require_valid(SKILLS, ["repository-requirements"])
    assert len(hashes["repository-requirements"]) == 64
    with pytest.raises(ValueError, match="not found"):
        require_valid(SKILLS, ["nope"])
    make(tmp_path, "broken", "name: wrong\ndescription: d")
    with pytest.raises(ValueError, match="violates"):
        require_valid(tmp_path, ["broken"])


def test_package_hash_changes_when_any_skill_file_changes(tmp_path):
    d = make(tmp_path, "skill", "name: skill\ndescription: d")
    before = tree_sha256(d)
    (d / "references").mkdir()
    (d / "references" / "guide.md").write_text("more")
    assert tree_sha256(d) != before


def test_vendored_upstream_skills_match_the_lock_and_tampering_is_detected(tmp_path):
    import shutil

    from maf_test_harness.skills.registry import load_lock, verify_lock

    lock = load_lock(SKILLS)
    assert (
        lock["upstream"]["url"].endswith("awesome-agent-skills.git") and len(lock["upstream"]["commit"]) == 40
    )
    assert set(lock["skills"]) == {
        "api-contract-analyzer",
        "coverage-analyzer",
        "junit-generator",
        "playwright-test-generator",
        "test-data-generator",
        "traceability-generator",
        "requirement-analysis",
        "reverse-engineering",
        "generic-test-generator",
        "repository-requirements",
        "test-design-generator",
        "test-planning",
        "spec-authoring",
    }
    assert all(
        v["audit"].get("security_findings") == 0
        if v["origin"] == "upstream"
        else v["audit"]["status"] in ("host-reviewed", "repo-reviewed")
        for v in lock["skills"].values()
    )
    assert verify_lock(SKILLS) == []
    copy = tmp_path / "skills"
    shutil.copytree(SKILLS, copy)
    (copy / "test-data-generator" / "SKILL.md").write_text("# tampered\n")
    (copy / "traceability-generator" / "scripts" / "extra.py").write_text("print(1)\n")
    problems = verify_lock(copy)
    assert any("test-data-generator/SKILL.md: modified" in p for p in problems)
    assert any("traceability-generator/scripts/extra.py: unexpected file" in p for p in problems)
    with pytest.raises(ValueError, match="modified after review"):
        require_valid(copy, ["test-data-generator"])

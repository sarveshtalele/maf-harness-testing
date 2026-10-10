import shutil
from pathlib import Path

import pytest

from maf_test_harness.skills import config, trust
from maf_test_harness.skills.config import SkillSet, skills_for
from maf_test_harness.skills.registry import discover_skills, lint_plugins, load_plugin

SKILLS = Path("skills")


def old_skills_for(stage, state):
    """The pre-manifest hard-coded selection, kept as the behaviour oracle."""
    names = {
        "requirements": "repository-requirements",
        "test_planning": "test-planning",
        "test_design": "test-design-generator",
        "test_generation": "generic-test-generator",
    }
    host = {"repo_analysis": "reverse-engineering", "requirements": "requirement-analysis"}
    if stage == "repo_analysis" or (
        stage == "requirements" and state.get("requirements_mode") == "deterministic"
    ):
        return SkillSet(host[stage])
    companions = []
    languages = set((state.get("artifacts", {}).get("repo_analysis") or {}).get("languages") or {})
    if stage == "requirements" and (state.get("stack") or {}).get("openapi_files"):
        companions.append("api-contract-analyzer")
    if stage in {"test_design", "test_generation"}:
        companions.append("test-data-generator")
    if stage == "test_generation":
        if languages & {"Java", "Kotlin"}:
            companions.append("junit-generator")
        design = state.get("artifacts", {}).get("test_design") or {}
        levels = {c.get("level") for k in ("positive", "negative") for c in design.get(f"{k}_tests", [])}
        if levels & {"ui", "e2e", "accessibility"}:
            companions.append("playwright-test-generator")
    return SkillSet(names[stage], tuple(companions))


def st(langs, openapi=False, levels=(), **repo):
    return {
        "stack": {"openapi_files": ["api.yml"]} if openapi else {},
        "artifacts": {
            "repo_analysis": {"languages": dict.fromkeys(langs, 1), **repo},
            "test_design": {"positive_tests": [{"level": lv} for lv in levels]},
        },
    }


STATES = {
    "python": st(["Python"]),
    "java": st(["Java"]),
    "kotlin_openapi": st(["Kotlin"], openapi=True),
    "ui": st(["TypeScript"], levels=["ui", "unit"]),
    "det": {**st(["Python"]), "requirements_mode": "deterministic"},
}
STAGES = ["repo_analysis", "requirements", "test_planning", "test_design", "test_generation"]


@pytest.mark.parametrize("state", STATES.values(), ids=STATES.keys())
@pytest.mark.parametrize("stage", STAGES)
def test_manifest_selection_matches_old_hard_coded_selection(stage, state):
    assert skills_for(stage, state) == old_skills_for(stage, state)


def test_derived_exports():
    assert config.SKILL_NAMES["test_generation"] == "generic-test-generator"
    assert config.HOST_SKILL_NAMES == {
        "repo_analysis": "reverse-engineering",
        "requirements": "requirement-analysis",
    }
    assert "junit-generator" in config.COMPANION_PURPOSE


def _drop(tmp_path, monkeypatch, body, name="go-test-helper"):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n# T\n", encoding="utf-8")
    (d / "plugin.yaml").write_text(body, encoding="utf-8")
    monkeypatch.setenv("HARNESS_PLUGIN_DIR", str(tmp_path))
    monkeypatch.setenv("HARNESS_PLUGIN_APPROVALS", str(tmp_path.parent / f"{tmp_path.name}-approvals.json"))
    trust.approve(config.ROOT / "skills", name)
    return d


def test_dropped_folder_becomes_companion_for_go_only(tmp_path, monkeypatch):
    _drop(
        tmp_path,
        monkeypatch,
        "name: go-test-helper\nstages: [test_generation]\nrole: companion\nkind: model\n"
        "purpose: follow Go testing idioms\napplies_when:\n  languages: [Go]\n",
    )
    assert "go-test-helper" in skills_for("test_generation", st(["Go"])).companions
    assert "go-test-helper" not in skills_for("test_generation", st(["Python"])).companions
    assert config.purpose_of("go-test-helper") == "follow Go testing idioms"


def test_files_glob_and_stack_flag(tmp_path, monkeypatch):
    _drop(
        tmp_path,
        monkeypatch,
        "name: go-test-helper\nstages: [requirements]\nrole: companion\nkind: model\npurpose: p\n"
        'applies_when:\n  files_glob: ["**/openapi*.{yml,yaml,json}"]\n',
    )
    hit = st(["Python"], source_files=["svc/api/openapi.yaml"])
    assert "go-test-helper" in skills_for("requirements", hit).companions
    assert (
        "go-test-helper" not in skills_for("requirements", st(["Python"], source_files=["a.py"])).companions
    )


@pytest.mark.parametrize(
    "body",
    [
        "name: wrong\nstages: [qa]\nrole: companion\nkind: model\npurpose: p\n",
        "name: go-test-helper\nstages: [nope]\nrole: companion\nkind: model\npurpose: p\n",
        "name: go-test-helper\nstages: [qa]\nrole: companion\nkind: model\npurpose: p\nextra: 1\n",
        "name: go-test-helper\nstages: [qa]\nrole: boss\nkind: model\npurpose: p\n",
        "name: go-test-helper\nstages: [qa]\nrole: companion\nkind: model\npurpose: p\napplies_when: {color: [red]}\n",
        "name: go-test-helper\nstages: [qa]\nrole: companion\nkind: host\npurpose: p\nentrypoint: no.such.mod:fn\n",
        "- not a mapping\n",
    ],
)
def test_invalid_plugin_yaml_rejected(tmp_path, body):
    d = tmp_path / "go-test-helper"
    d.mkdir()
    (d / "plugin.yaml").write_text(body, encoding="utf-8")
    plugin = load_plugin(d)
    assert lint_plugins([plugin])["go-test-helper"]


def test_two_primaries_for_a_stage_is_a_lint_error(tmp_path):
    for n in ("test-planning", "other-planner"):
        d = tmp_path / n
        d.mkdir()
        (d / "plugin.yaml").write_text(
            f"name: {n}\nstages: [test_planning]\nrole: primary\nkind: model\npurpose: p\n", encoding="utf-8"
        )
    problems = lint_plugins(discover_skills([tmp_path]))
    assert all("more than one" in p[0] for p in problems.values())


def test_repo_skills_are_clean_and_cache_invalidates(tmp_path):
    assert {k: v for k, v in lint_plugins(discover_skills([SKILLS])).items() if v} == {}
    shutil.copytree(SKILLS / "test-planning", tmp_path / "test-planning")
    assert discover_skills([tmp_path])[0].ok
    (tmp_path / "test-planning" / "plugin.yaml").write_text("name: test-planning\n", encoding="utf-8")
    assert not discover_skills([tmp_path])[0].ok

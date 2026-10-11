"""Run options: mode, test types, greenfield spec sources, level allowlist, design prompt, token never persisted."""

import base64
from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime
from test_ui_api import approve, local_api, wait  # noqa: F401

from maf_test_harness.run_options import parse_run_options, restrict_levels

URL = "https://github.com/acme/demo"
DOC = {"name": "spec.md", "content_base64": base64.b64encode(b"# Spec").decode()}
JIRA = {
    "base_url": "https://acme.atlassian.net",
    "issue_keys": ["ACME-1"],
    "email": "ba@acme.com",
    "api_token": "TOKEN-xyz",
}


def test_validation():
    assert parse_run_options({"repo_url": URL})["test_types"] == ["unit", "integration"]
    assert parse_run_options({"test_types": ["integration"]})["test_types"] == ["integration"]
    bad = [
        {"run_mode": "hybrid"},
        {"test_types": []},
        {"test_types": ["e2e"]},
        {"spec_sources": {"documents": [DOC]}},  # brownfield with specs
        {"run_mode": "greenfield"},  # no sources
        {"run_mode": "greenfield", "spec_sources": {"documents": [{**DOC, "name": "x.exe"}]}},
        {"run_mode": "greenfield", "spec_sources": {"documents": [DOC] * 11}},
        {"run_mode": "greenfield", "spec_sources": {"documents": [{**DOC, "content_base64": "!!"}]}},
        {
            "run_mode": "greenfield",
            "spec_sources": {"jira": {**JIRA, "base_url": "https://evil.example.com"}},
        },
        {
            "run_mode": "greenfield",
            "spec_sources": {"jira": {**JIRA, "base_url": "https://acme.atlassian.net.evil.io"}},
        },
        {"run_mode": "greenfield", "spec_sources": {"jira": {**JIRA, "issue_keys": []}}},
    ]
    for body in bad:
        with pytest.raises(ValueError):
            parse_run_options(body)
    big = base64.b64encode(b"x" * (6 * 1024 * 1024)).decode()
    with pytest.raises(ValueError, match="10 MB"):
        parse_run_options(
            {
                "run_mode": "greenfield",
                "spec_sources": {
                    "documents": [{**DOC, "name": f"{i}.txt", "content_base64": big} for i in range(2)]
                },
            }
        )
    ok = parse_run_options({"run_mode": "greenfield", "spec_sources": {"documents": [DOC], "jira": JIRA}})
    assert "api_token" not in str(ok["spec_sources"]) and ok["jira_token"] == "TOKEN-xyz"


def test_api_modes_and_token_not_persisted(local_api, monkeypatch):  # noqa: F811
    import maf_test_harness.workflow as wf

    api = local_api
    seen = {}

    def fake_ingest(documents, jira=None, fetch=None):
        seen["jira"] = jira
        return {
            "sources": [{"kind": "document", "name": "spec.md"}],
            "sections": [
                {
                    "anchor": "spec",
                    "title": "Spec",
                    "text": "x",
                    "source": {"doc": "spec.md", "section_anchor": "spec"},
                }
            ],
            "warnings": [],
        }

    monkeypatch.setattr(wf, "ingest_specs", fake_ingest)
    with pytest.raises(ValueError):
        api.start({"run_mode": "brownfield"})  # repo required
    with pytest.raises(ValueError):
        api.start({"repo_url": URL, "test_types": []})
    key = api.start(
        {"run_mode": "greenfield", "test_types": ["unit"], "spec_sources": {"documents": [DOC], "jira": JIRA}}
    )["id"]
    wait(api)
    run_dir = api.run_path(key)
    state = api.read(key)["state"]
    assert state["run_mode"] == "greenfield" and state["test_types"] == ["unit"]
    assert seen["jira"]["api_token"] == "TOKEN-xyz"  # handed to intake in memory
    assert api.read(key)["pending"]["stage"] == "requirements"
    assert (run_dir / "spec_inputs" / "spec.md").read_bytes() == b"# Spec"
    for path in Path(run_dir).rglob("*"):
        if path.is_file():
            assert b"TOKEN-xyz" not in path.read_bytes(), path
    key2 = api.start({"repo_url": URL})["id"]
    wait(api)
    state2 = api.read(key2)["state"]
    assert state2["run_mode"] == "brownfield" and state2["test_types"] == ["unit", "integration"]


def test_level_allowlist_conversion():
    def case(level, target="svc.run", title="t"):
        return {"title": title, "level": level, "target": target}

    draft = {
        "positive_tests": [
            case("api", title="a"),
            case("e2e", "/orders", "b"),
            case("ui", "Home page", "c"),
            case("unit", title="d"),
        ],
        "negative_tests": [case("cli", "GET /x", "e"), case("performance", title="f")],
    }
    notes = restrict_levels(draft, ["unit", "integration"])
    assert [(c["title"], c["level"]) for c in draft["positive_tests"]] == [
        ("a", "integration"),
        ("b", "integration"),
        ("d", "unit"),
    ]
    assert [c["title"] for c in draft["negative_tests"]] == ["e"]
    assert len(notes) == 2 and all("excluded by run test types" in n for n in notes)
    unit_only = {"positive_tests": [case("api"), case("integration"), case("unit")]}
    restrict_levels(unit_only, ["unit"])
    assert [c["level"] for c in unit_only["positive_tests"]] == ["unit"]


def test_design_prompt_has_restriction(local_api, monkeypatch):  # noqa: F811
    api = local_api
    prompts = []
    original = ScriptedStageRuntime.run_stage

    async def spy(self, *, stage, state, **kwargs):
        if stage == "test_design" and "prompt" in kwargs:
            prompts.append(kwargs["prompt"])
        return await original(self, stage=stage, state=state, **kwargs)

    monkeypatch.setattr(ScriptedStageRuntime, "run_stage", spy)
    key = api.start({"repo_url": URL, "test_types": ["unit"]})["id"]
    wait(api)
    while api.read(key)["pending"]["stage"] != "test_design":
        approve(api, key)
    assert any("Design ONLY unit cases (per run test_types)" in p for p in prompts)


def test_unsupported_documents_are_named_in_the_error():
    def doc(name):
        return {"name": name, "content_base64": base64.b64encode(b"x").decode()}

    with pytest.raises(ValueError, match=r"Unsupported document type: stories\.xlsx, notes\.zip\."):
        parse_run_options(
            {
                "run_mode": "greenfield",
                "spec_sources": {"documents": [doc("spec.md"), doc("stories.xlsx"), doc("notes.zip")]},
            }
        )
    with pytest.raises(ValueError, match="At most 10 specification documents"):
        parse_run_options(
            {"run_mode": "greenfield", "spec_sources": {"documents": [doc(f"s{i}.md") for i in range(11)]}}
        )

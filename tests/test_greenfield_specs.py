import io
import json
import zipfile
from pathlib import Path

import pytest
from fakes import ScriptedStageRuntime

from maf_test_harness.repo.specs import build_specs, ingest_specs, section_ref, specs_to_requirements
from maf_test_harness.ui.runtime_service import RuntimeService
from maf_test_harness.workflow import Harness

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MD = b"""Intro text before headings.

# Login
Users sign in with email and password.

```
# not a heading
```

## Acceptance Criteria
- Valid credentials open the dashboard
- Three failures lock the account

# Login
Duplicate title gets its own anchor.
"""


def docx_bytes(paragraphs):
    body = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
        if style
        else f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        for style, text in paragraphs
    )
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_markdown_and_docx_sections():
    sections = ingest_specs([{"name": "req.md", "content": MD}])["sections"]
    assert [s["anchor"] for s in sections] == ["preamble", "login", "acceptance-criteria", "login-2"]
    assert "# not a heading" in sections[1]["text"]
    assert sections[1]["source"] == {"doc": "req.md", "section_anchor": "login"}
    data = docx_bytes(
        [
            ("Heading1", "Checkout"),
            (None, "Cart total is shown."),
            ("Heading2", "Rules"),
            (None, "Max 10 items."),
        ]
    )
    result = ingest_specs(
        [
            {"name": "a.docx", "content": data},
            {"name": "x.pdf", "content": b"%PDF"},
            {"name": "y.exe", "content": b""},
        ]
    )
    assert [(s["title"], s["text"]) for s in result["sections"]] == [
        ("Checkout", "Cart total is shown."),
        ("Rules", "Max 10 items."),
    ]
    assert any("pypdf" in w or "PDF" in w for w in result["warnings"]) and any(
        "unsupported" in w for w in result["warnings"]
    )
    assert ingest_specs([{"name": "bad.docx", "content": b"nope"}])["warnings"]


def adf(*blocks):
    return {"type": "doc", "version": 1, "content": list(blocks)}


def para(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def issue(key, summary, description=None, **fields):
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": description,
            "issuetype": {"name": "Story"},
            "status": {"name": "To Do"},
            "labels": ["web"],
            **fields,
        },
    }


class FakeJira:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, json.loads(body), timeout))
        return self.pages[len(self.calls) - 1]


CREDS = {
    "base_url": "https://acme.atlassian.net",
    "jql": "project = PAY",
    "email": "ba@acme.com",
    "api_token": "s3cr3t-token",
}


def test_jira_adf_acceptance_criteria_and_no_secret_leak():
    ac_list = {
        "type": "bulletList",
        "content": [{"type": "listItem", "content": [para("Refund within 30 days")]}],
    }
    description = adf(
        para("Customers pay by card."),
        {
            "type": "heading",
            "attrs": {"level": 2},
            "content": [{"type": "text", "text": "Acceptance Criteria"}],
        },
        ac_list,
    )
    page = {
        "issues": [
            issue("PAY-1", "Card payment", description, customfield_10042=adf(para("Receipt is emailed")))
        ],
        "names": {"customfield_10042": "Acceptance Criteria"},
        "isLast": True,
    }
    fetch = FakeJira([(200, page)])
    result = ingest_specs([], CREDS, fetch=fetch)
    method, url, headers, payload, timeout = fetch.calls[0]
    assert (method, url, timeout) == ("POST", "https://acme.atlassian.net/rest/api/3/search/jql", 15)
    assert payload["jql"] == "project = PAY" and headers["Authorization"].startswith("Basic ")
    section = result["sections"][0]
    assert section["source"] == {"jira_key": "PAY-1"} and section["anchor"] == "PAY-1"
    assert section["acceptance_criteria"] == ["Receipt is emailed", "Refund within 30 days"]
    assert (
        "Customers pay by card." in section["text"] and "Acceptance Criteria:\n- Receipt" in section["text"]
    )
    assert "s3cr3t-token" not in json.dumps(result) and "ba@acme.com" not in json.dumps(result)


def test_jira_pagination_cap_and_url_rules():
    pages = [
        (
            200,
            {
                "issues": [issue(f"P-{i}", "s") for i in range(100 * n, 100 * n + 100)],
                "nextPageToken": f"t{n}",
            },
        )
        for n in range(3)
    ]
    fetch = FakeJira(pages)
    result = ingest_specs([], CREDS, fetch=fetch)
    assert len(result["sections"]) == 200 and len(fetch.calls) == 2
    assert fetch.calls[1][3]["nextPageToken"] == "t0" and fetch.calls[1][3]["maxResults"] == 100
    assert any("capped" in w for w in result["warnings"])
    for bad in (
        "https://evil.example.com",
        "http://acme.atlassian.net",
        "https://acme.atlassian.net.evil.com",
        "https://u:p@acme.atlassian.net",
        "https://acme.atlassian.net:8443",
    ):
        with pytest.raises(ValueError, match="atlassian.net"):
            ingest_specs([], {**CREDS, "base_url": bad}, fetch=FakeJira([]))
    with pytest.raises(ValueError, match="rejected") as exc:
        ingest_specs([], CREDS, fetch=FakeJira([(401, {})]))
    assert "s3cr3t-token" not in str(exc.value)

    def boom(*args):
        raise RuntimeError("Authorization: Basic leaked")

    with pytest.raises(ValueError) as exc:
        ingest_specs([], CREDS, fetch=boom)
    assert "leaked" not in str(exc.value)
    keys = FakeJira([(200, {"issues": [], "isLast": True})])
    ingest_specs([], {**CREDS, "jql": "", "issue_keys": ["pay-1", "PAY-2"]}, fetch=keys)
    assert keys.calls[0][3]["jql"].startswith("key in (PAY-1,PAY-2)")


def test_spec_ids_validation_and_requirements_traceability():
    sections = ingest_specs([{"name": "req.md", "content": MD}])["sections"]

    def draft(ref, **kw):
        return {
            "source_ref": ref,
            "title": "Login",
            "narrative": "n",
            "acceptance_criteria": ["A", "B"],
            "business_rules": ["R"],
            "data_examples": [],
            **kw,
        }

    first = build_specs([draft("req.md#login"), draft("req.md#login", title="Again")], sections)
    again = build_specs([draft("req.md#login")], sections)
    assert (
        first[0]["id"] == again[0]["id"] and first[0]["id"].endswith("-1") and first[1]["id"].endswith("-2")
    )
    assert first[0]["id"].split("-")[1] == first[1]["id"].split("-")[1]
    assert first[0]["source"] == {"doc": "req.md", "section_anchor": "login"}
    with pytest.raises(ValueError, match="not a supplied section"):
        build_specs([draft("invented.md#x")], sections)
    model = specs_to_requirements(first, "App")
    reqs = model["requirement_model"]["requirements"]
    assert len(reqs) == 6 and {r["id"] for r in reqs} == {
        r["id"] for r in specs_to_requirements(first, "App")["requirement_model"]["requirements"]
    }
    assert all(
        r["source_files"] == [] and r["traceability"]["spec_id"] in {s["id"] for s in first} for r in reqs
    )
    assert (
        reqs[0]["traceability"]["source"] == first[0]["source"] and section_ref(sections[1]) == "req.md#login"
    )


class GreenfieldRuntime(ScriptedStageRuntime):
    async def run_stage(self, *, stage, state, **kwargs):
        if stage == "spec_authoring":
            self.calls.append(stage)
            refs = [s["ref"] for s in json.loads(kwargs["prompt"])["approved_inputs"]["sections"]]
            return {
                "status": "completed",
                "artifact": {
                    "specs": [
                        {
                            "source_ref": refs[0],
                            "title": "Addition",
                            "narrative": "Adds numbers.",
                            "acceptance_criteria": ["add returns the sum"],
                            "business_rules": [],
                            "data_examples": ["1+2=3"],
                        }
                    ],
                    "assumptions": [],
                    "gaps": [],
                },
            }
        result = await super().run_stage(stage=stage, state=state, **kwargs)
        if stage == "test_design" and "positive_tests" in result["artifact"]:
            for case in result["artifact"]["positive_tests"]:
                case["actor"] = "User"
        return result


def run_greenfield(tmp_path, **start):
    runtime = GreenfieldRuntime()
    h = Harness(tmp_path, skills_root=Path("skills"), requirements_mode="model", runtime=runtime)
    svc = RuntimeService()

    def answer(**response):
        view = svc.submit(h.respond(h.view["pending"]["id"], response)).result(60)
        assert (view.get("pending") or {}).get("kind") != "error", view.get("pending")
        return view

    svc.submit(
        h.start("", run_mode="greenfield", documents=[{"name": "req.md", "content": MD}], **start)
    ).result(30)
    return h, runtime, answer, svc


def test_greenfield_without_repo_skips_analysis_and_execution(tmp_path):
    h, runtime, answer, svc = run_greenfield(tmp_path)
    try:
        assert h.view["pending"]["stage"] == "requirements"  # repo_analysis skipped, no clone approval
        assert h.state["artifacts"]["repo_analysis"]["scaffold"]["framework"] == "pytest"
        assert (tmp_path / "repo" / "pytest.ini").exists()
        artifact = h.view["pending"]["details"]["artifact"]
        assert (
            artifact["specs"][0]["id"].startswith("SPEC-") and artifact["spec_intake"]["section_count"] == 4
        )
        req = artifact["requirement_model"]["requirements"][0]
        assert req["traceability"]["spec_id"] == artifact["specs"][0]["id"] and req["source_files"] == []
        assert any("No spec was produced" in g for g in artifact["gaps"])
        answer(decision="approve")
        assert h.view["pending"]["stage"] == "test_planning"
        answer(decision="approve")
        answer(decision="approve")
        assert h.view["pending"]["stage"] == "test_generation"
        final = answer(decision="approve")
        assert final["status"] == "complete"
        assert h.state["environment"]["status"] == "skipped"
        qa = h.state["artifacts"]["test_execution"]["qa_report"]
        assert qa["execution_note"] == "skipped: no application code"
        assert "no application code" in h.state["completed_reason"]
        assert "spec_authoring" in runtime.calls
        assert "jira" not in json.dumps(h.state["spec_intake"]).lower()
    finally:
        svc.close()


def test_greenfield_javascript_scaffold_and_validation(tmp_path):
    h, _, _, svc = run_greenfield(tmp_path, target_language="javascript")
    try:
        assert (tmp_path / "repo" / "jest.config.js").exists()
        assert h.state["artifacts"]["repo_analysis"]["test_frameworks"] == ["jest"]
        with pytest.raises(ValueError, match="target_language"):
            svc.submit(
                Harness(tmp_path / "b", skills_root=Path("skills")).start(
                    "",
                    run_mode="greenfield",
                    target_language="cobol",
                    documents=[{"name": "a.md", "content": MD}],
                )
            ).result(10)
        with pytest.raises(ValueError, match="at least one"):
            svc.submit(
                Harness(tmp_path / "c", skills_root=Path("skills")).start("", run_mode="greenfield")
            ).result(10)
    finally:
        svc.close()

"""Shared scripted stage runtime for workflow tests (replaces only the model call)."""

import json
import sys
from pathlib import Path


def clone_fixture(url, destination):
    destination.mkdir()
    (destination / "service.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")


# The fixture clone's service.py contains "return a + b": a verbatim implementation quote (kind derived).
SCRIPTED_ORACLE = {
    "kind": "derived",
    "source": {"file": "service.py", "line": 2, "quote": "return a + b"},
    "relation": None,
}


def scripted_oracle(state):
    """Derived from the clone's service.py; greenfield runs have no source, so they quote the approved requirement
    evidence as a specified oracle (the host accepts approved evidence for specified kinds)."""
    if state.get("run_mode") == "greenfield":
        requirement = state["artifacts"]["requirements"]["requirement_model"]["requirements"][0]
        return {
            "kind": "specified",
            "source": {"file": "spec.md", "line": 1, "quote": requirement["evidence"][:200]},
            "relation": None,
        }
    return dict(SCRIPTED_ORACLE)


FULL_MARKS = {
    "source_and_api_match": 25,
    "assertion_checks_real_behavior": 30,
    "fixture_validity": 20,
    "isolation_and_determinism": 15,
    "traceability": 10,
}


# Generated test text by case ID, recorded from every scripted generation result (see tests/conftest.py). The scripted
# reviewer reads the literals it "derives" from here, because the host withholds them from the reviewer prompt.
GENERATED_TEXT: dict[str, str] = {}


def record_generated(files):
    """Remember the unmasked text of each case as its latest generation returned it."""
    by_case: dict[str, list[str]] = {}
    for item in files:
        text = item.get("content") or "\n".join(item.get("lines", []))
        for case_id in item.get("case_ids", []):
            by_case.setdefault(str(case_id), []).append(text)
    GENERATED_TEXT.update({case_id: "\n".join(texts) for case_id, texts in by_case.items()})


def scripted_derivations(prompt: dict, case_id: str) -> list[dict]:
    """Correct blind derivations for one case's masked slots, quoted verbatim from its recorded text and target_source."""
    from maf_test_harness.qa.derivation import SLOT_PATTERN, mask_expected_literals

    recorded = GENERATED_TEXT.get(case_id)
    if recorded is None:
        return []
    _, real = mask_expected_literals([{"path": "recorded.py", "content": recorded, "case_ids": [case_id]}])
    slot_ids = [
        slot
        for item in prompt.get("generated_source", [])
        if case_id in item.get("case_ids", [])
        for slot in SLOT_PATTERN.findall(item["content"])
    ]
    target = (prompt.get("case_target") or {}).get(case_id, "")
    sources = prompt.get("target_source", {})
    target_source = sources.get(target, "") if isinstance(sources, dict) else sources
    source_lines = [
        line.split(": ", 1)[-1].strip()
        for line in target_source.splitlines()
        if line.strip() and not line.startswith("--- SOURCE")
    ]
    source_quote = source_lines[0] if source_lines else ""
    fixture_quote = next((line.strip() for line in recorded.splitlines() if line.strip()), "")
    return [
        {
            "slot": slot,
            "value": literal["value"],
            "steps": "Scripted derivation: the fixture is evaluated by the implementation shown in source_quote.",
            "source_quote": source_quote,
            "fixture_quote": fixture_quote,
        }
        for slot, literal in zip(slot_ids, real, strict=False)
    ]


def review_response(kwargs, source="service.py", scores=None):
    from maf_test_harness.models import DerivationRecheckDraft, TestReviewDraft

    if kwargs.get("response_format") is DerivationRecheckDraft:
        prompt = json.loads(kwargs["prompt"])
        answers = []
        for item in prompt["slots_to_recheck"]:
            answers += [d for d in scripted_derivations(prompt, item["case_id"]) if d["slot"] == item["slot"]]
        return {"status": "completed", "artifact": {"derivations": answers}}
    if kwargs.get("response_format") is not TestReviewDraft:
        return None
    prompt = json.loads(kwargs["prompt"])
    cases = prompt["approved_inputs"]["approved_cases"]
    return {
        "status": "completed",
        "artifact": {
            "reviews": [
                {
                    "case_id": c["id"],
                    "scores": dict(scores or FULL_MARKS),
                    "confidence": 90,
                    "verdict": "supported",
                    "reason": "Scripted reviewer transport: source and assertions match the fixture.",
                    "source_files": [source],
                    "derivations": scripted_derivations(prompt, str(c["id"])),
                }
                for c in cases
            ]
        },
    }


FULL_DESIGN = {"source_proven": 30, "correct_target": 25, "expected_derivable": 25, "fixture_validity": 20}
FULL_PLAN = {"controls_covered": 40, "level_fit": 30, "scenarios_derivable": 30}


def first_source_line(source: str) -> str:
    """A verbatim code line of a target source block (line-number prefixes and the block header removed)."""
    for line in source.splitlines():
        text = line.split(": ", 1)[-1].strip() if line[:1].isdigit() else line.strip()
        if text and not text.startswith("--- SOURCE"):
            return text
    return ""


def design_review_response(
    kwargs, verdict="approved", reason="Scripted design reviewer.", scores=None, quote=None
):
    """Design confidence reviewer: full rubric marks and a verbatim quote from target_source, unless told otherwise."""
    from maf_test_harness.models import DesignReviewDraft

    if kwargs.get("response_format") is not DesignReviewDraft:
        return None
    prompt = json.loads(kwargs["prompt"])
    sources = prompt.get("target_source") or {}
    return {
        "status": "completed",
        "artifact": {
            "reviews": [
                {
                    "case_id": c["id"],
                    "scores": dict(scores or FULL_DESIGN),
                    "verdict": verdict,
                    "derivation": "Scripted derivation: the implementation returns the value the expectation states.",
                    "source_quote": quote
                    if quote is not None
                    else first_source_line(sources.get(c["target"], "")),
                    "reason": reason,
                }
                for c in prompt["cases_under_review"]
            ]
        },
    }


def plan_review_response(kwargs, verdict="approved", reason="Scripted plan reviewer.", scores=None):
    """Planning confidence reviewer: full rubric marks for every area it is asked about."""
    from maf_test_harness.models import PlanReviewDraft

    if kwargs.get("response_format") is not PlanReviewDraft:
        return None
    prompt = json.loads(kwargs["prompt"])
    return {
        "status": "completed",
        "artifact": {
            "reviews": [
                {
                    "area_id": a["area_id"],
                    "scores": dict(scores or FULL_PLAN),
                    "verdict": verdict,
                    "reason": reason,
                }
                for a in prompt["plan_areas"]
            ]
        },
    }


class ScriptedStageRuntime:
    """Returns schema-valid stage artifacts for the tiny `service.add` repository."""

    def __init__(self, test_body="assert add(1, 2) == 3", extra_cases=()):
        self.calls = []
        self.test_body = test_body
        self.extra_cases = list(extra_cases)
        self.tool_agent_calls = []
        self.tool_agent_note = {
            "summary": "pip could not build a dependency",
            "likely_cause": "no C compiler",
            "category": "environment",
            "suggested_fixes": ["install build tools"],
            "needs_human": True,
        }

    async def run_tool_agent(self, *, stage, persona, prompt, tools, state, persist, **kwargs):
        self.tool_agent_calls.append({"stage": stage, "tools": [t.name for t in tools], "prompt": prompt})
        if isinstance(self.tool_agent_note, Exception):
            raise self.tool_agent_note
        return self.tool_agent_note

    async def run_stage(self, *, stage, state, **kwargs):
        if review := review_response(kwargs):
            return review
        if review := design_review_response(kwargs):
            return review
        if review := plan_review_response(kwargs):
            return review
        self.calls.append(stage)
        if stage == "requirements":
            return {
                "status": "completed",
                "artifact": {
                    "app_name": "demo",
                    "summary": "Simple addition service",
                    "actors": [
                        {"name": "Developer", "description": "Calls add()", "source_files": ["service.py"]}
                    ],
                    "requirements": [
                        {
                            "text": "add returns the sum of two inputs",
                            "evidence": "service.py defines add(a, b) as a + b",
                            "source_files": ["service.py"],
                            "category": "functional",
                            "actor": "Developer",
                            "tier": "component",
                            "evidence_quotes": [],
                        }
                    ],
                    "acceptance_criteria": [],
                    "validation_rules": [],
                    "api_contracts": [],
                    "environment_needs": [],
                    "exclusions": [],
                    "assumptions": [],
                    "gaps": [],
                },
            }
        if stage == "test_planning":
            return {
                "status": "completed",
                "artifact": {
                    "objective": "Verify addition",
                    "recommended_frameworks": ["pytest"],
                    "setup_actions": [],
                    "areas": [
                        {
                            "name": "addition",
                            "objective": "verify sum",
                            "level": "unit",
                            "risk": "medium",
                            "evidence_files": ["service.py"],
                            "scenarios": ["add two integers"],
                        }
                    ],
                    "execution_order": ["addition unit tests"],
                    "assumptions": [],
                    "gaps": [],
                },
            }
        if stage == "test_design":
            req = state["artifacts"]["requirements"]["requirement_model"]["requirements"][0]["id"]
            case = {
                "title": "add returns sum",
                "priority": "P1",
                "level": "unit",
                "actor": "Developer",
                "target": "service.add",
                "description": "Two integers are added.",
                "preconditions": [],
                "data_notes": "1 and 2",
                "expected_result": "add(1, 2) returns 3",
                "oracle": scripted_oracle(state),
                "steps": ["Call add(1, 2)", "Compare with 3"],
                "traceability_links": [{"type": "requirement", "id": req}],
            }
            negative = [
                {
                    **c,
                    "oracle": c.get("oracle", scripted_oracle(state)),
                    "traceability_links": case["traceability_links"],
                }
                for c in self.extra_cases
            ]
            return {
                "status": "completed",
                "artifact": {
                    "positive_tests": [case],
                    "negative_tests": negative,
                    "boundary_tests": [],
                    "validation_tests": [],
                    "contract_tests": [],
                    "gaps": [],
                },
            }
        prompt = json.loads(kwargs["prompt"])
        files = []
        for case in prompt["approved_inputs"]["approved_cases"]:
            slug = "".join(ch if ch.isalnum() else "_" for ch in case["title"]).strip("_").lower()
            if case["title"] == "add returns sum":
                body = self.test_body
            elif case["title"].startswith("add returns sum"):
                body = "assert add(1, 2) == 3"  # extra case from the browser fixture: always passes
            else:
                body = "assert add(2, 2) == 5"
            files.append(
                {
                    "path": f"tests/test_{slug}.py",
                    "case_ids": [case["id"]],
                    "lines": ["from service import add", "", "", f"def test_{slug}():", f"    {body}"],
                }
            )
        return {
            "status": "completed",
            "artifact": {
                "selected_language": "Python",
                "selected_framework": "pytest",
                "rationale": "Python source.",
                "files": files,
            },
        }


def local_env_plan(stack, root, cache_dir, timeout=900, needs=None, **kwargs):
    """Environment plan whose only step is a harmless local command (unit tests have no network)."""
    return {
        "ecosystem": "python",
        "isolation": "test double",
        "prerequisites": [],
        "missing_prerequisites": [],
        "notes": [],
        "steps": [
            {
                "name": "noop-install",
                "kind": "setup",
                "argv": [sys.executable, "-c", "print('ok')"],
                "env": {},
                "cwd": ".",
                "timeout": 60,
                "continue_on_failure": False,
            }
        ],
    }


def children_import_this_checkout(monkeypatch) -> None:
    """Skill-script children get only BASIC_ENV, so without this they import whatever maf_test_harness is installed."""
    from maf_test_harness.skills import scripts

    src = str(Path(__file__).resolve().parents[1] / "src")
    real_run_command = scripts.run_command

    def run(argv, cwd, output_dir, **kwargs):
        kwargs["env"] = {**(kwargs.get("env") or {}), "PYTHONPATH": src}
        return real_run_command(argv, cwd, output_dir, **kwargs)

    monkeypatch.setattr(scripts, "run_command", run)

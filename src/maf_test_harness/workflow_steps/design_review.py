"""Confidence reviewer of recommended design cases: rubric points from the reviewer, host caps, a hard gate at 95.

The reviewer scores four dimensions and quotes its derivation from target_source. The host verifies the quote
(whitespace-normalised substring, as in test review), applies the caps, and records every pass in the ledger. A case
below the gate is repaired up to MODEL_REPAIR_ATTEMPTS times; when the gate is on and a case is still below it,
design raises `Confidence gate: test design ...` and generation never starts. Gate off (HARNESS_REQUIRE_FULL_ACCURACY=0,
or a greenfield run without code) keeps the review advisory. HARNESS_DESIGN_REVIEW=0 turns the reviewer,
and with it this gate, off.
"""

import os
import re

from ..agent.personas import stack_expertise
from ..agent.runtime import AgentOutputError
from ..llm.prompts import build_prompt
from ..models import DesignReviewDraft
from ..qa.confidence import DESIGN_RUBRIC, quote_verified, resolve_target, rubric_score
from ..qa.ledger import append_score
from ..qa.report import design_cases
from ..repo.analyzer import extract_target_sources
from ..settings import REQUIRE_FULL_ACCURACY, review_threshold
from ..workflow_support import MODEL_REPAIR_ATTEMPTS
from .greenfield import has_no_code

ROLE = "design_reviewer"
TASK = "\nTASK: validate recommended test design cases; follow the review output contract (scores, derivation, source_quote)."
_DIGIT = re.compile(r"\d")


def enabled() -> bool:
    return os.getenv("HARNESS_DESIGN_REVIEW", "1").strip().lower() not in {"0", "false", "off", "no"}


def min_confidence(state: dict | None = None) -> int:
    return review_threshold("design", state)


def gate_on(state: dict | None = None) -> bool:
    return bool(REQUIRE_FULL_ACCURACY) and not has_no_code(state or {})


def case_source(root, target: str, source_files: list[str]) -> str:
    """Complete source of one case's target; empty when the target does not resolve. A "module.function" target
    resolves through its symbol when no class or module of that name declares it (as in test review)."""
    if not target:
        return ""
    found = extract_target_sources(root, [target], source_files)
    resolved = resolve_target(target)
    if not found and resolved is not None and resolved.kind != "route":
        found = extract_target_sources(root, [resolved.symbol], source_files)
    return found


def score_case(review: dict, case: dict, source: str, threshold: int) -> dict:
    """The reviewed case with host-computed confidence (rubric points, caps, derivation and quote evidence)."""
    scores = {dimension: int(review["scores"][dimension]) for dimension in DESIGN_RUBRIC}
    quote_ok = quote_verified(str(review.get("source_quote") or ""), source)
    derivable = bool(str(review.get("derivation") or "").strip()) and quote_ok
    findings: list[dict] = []
    if not derivable:
        scores["expected_derivable"] = 0
        findings.append(
            {
                "rule": "expected_derivable_unproven",
                "cap": None,
                "reason": "expected_derivable needs a derivation and a source_quote that is verbatim in target_source.",
            }
        )
    if not source.strip():
        findings.append(
            {
                "rule": "target_unresolved",
                "cap": 50,
                "reason": f"Target `{case.get('target') or ''}` does not resolve to implementation source.",
            }
        )
    if _DIGIT.search(str(case.get("expected_result") or "")) and not quote_ok:
        findings.append(
            {
                "rule": "numeric_expected_unverified",
                "cap": 60,
                "reason": "The expected result is numeric but no verified source_quote supports it.",
            }
        )
    scored = rubric_score("design", scores, findings, review["verdict"])
    return {
        **scored,
        "case_id": str(review["case_id"]),
        "verdict": review["verdict"],
        "reason": review["reason"],
        "suggested_target": review.get("suggested_target"),
        "accepted": review["verdict"] == "approved" and scored["confidence"] >= threshold,
        # The fields the design artifact and older readers expect.
        "confidence": scored["confidence"],
        "derivations": [{"derivation": review.get("derivation") or ""}],
        "evidence": [{"quote": review.get("source_quote") or "", "verified": quote_ok}],
    }


def weak_cases(reviews, cases, state: dict | None = None) -> list[dict]:
    """Reviews below the gate or rejected, plus a stand-in for every case the reviewer did not answer."""
    gate = min_confidence(state)
    answered = {r["case_id"]: r for r in reviews}
    weak = [r for r in reviews if not r["accepted"] or r["confidence"] < gate]
    for case in cases:
        cid = str(case.get("id"))
        if cid not in answered:
            weak.append(
                {
                    "case_id": cid,
                    "confidence": 0,
                    "verdict": "unreviewed",
                    "reason": "The design reviewer did not return a usable review for this case.",
                    "caps": [],
                    "suggested_target": None,
                }
            )
    return weak


def repair_feedback(reviews) -> str:
    return (
        "Independent confidence review flagged these cases; fix, retarget or replace them using only source-proven behaviour: "
        + "; ".join(
            f"{r['case_id']} (confidence {r['confidence']}, {r['verdict']}): {r['reason']}"
            + "".join(f" Cap {c['rule']} {c['cap']}: {c['reason']}" for c in r.get("caps", []))
            + (f" Suggested target: {r['suggested_target']}." if r.get("suggested_target") else "")
            for r in reviews
        )
    )


def gate_message(weak: list[dict], total: int, state: dict | None = None) -> str:
    return (
        f"Confidence gate: test design {len(weak)} of {total} cases are below the {min_confidence(state)} "
        "confidence gate or were not reviewed; generation is blocked: "
        + "; ".join(
            f"{r['case_id']} confidence {r['confidence']} ({r['verdict']}): {r['reason']}" for r in weak
        )[:1800]
        + ". Revise the design for these cases. HARNESS_REQUIRE_FULL_ACCURACY=0 makes this gate advisory."
    )


def apply_design_reviews(design, state):
    """Put the latest review of each case on the case itself (confidence, reviewer_verdict, reviewer_reason)."""
    latest = {r["case_id"]: r for batch in state.get("design_reviews", {}).values() for r in batch}
    for case in design_cases(design):
        review = latest.get(str(case.get("id")))
        if review:
            case.update(
                confidence=review["confidence"],
                reviewer_verdict=review["verdict"],
                reviewer_reason=review["reason"],
            )
    if latest:
        design["design_reviews"] = sorted(latest.values(), key=lambda r: (-r["confidence"], r["case_id"]))


async def review_design_cases(host, state, cases, batch_index):
    """Score every case of one batch. Returns scored reviews; an unusable reviewer returns [] (the gate then blocks)."""
    analysis = state["artifacts"].get("repo_analysis", {})
    root = host.run_dir / "repo"
    source_files = analysis.get("source_files", [])
    threshold = min_confidence(state)
    targets = {str(c.get("target") or "") for c in cases}
    sources = {target: case_source(root, target, source_files) for target in targets}
    linked = {link["id"] for c in cases for link in c.get("traceability_links", [])}
    requirements = [
        r
        for r in state["artifacts"]
        .get("requirements", {})
        .get("requirement_model", {})
        .get("requirements", [])
        if r["id"] in linked
    ]
    sections = {
        "assignment": "Independently validate EACH recommended test design case against the actual source. Score four dimensions from the evidence (scores field): source_proven (0-30) the asserted behaviour is implemented by target_source; correct_target (0-25) the target is the method that implements it; expected_derivable (0-25) the expected result follows from the source, with a derivation and a source_quote copied verbatim from target_source; fixture_validity (0-20) the steps and data are valid for the real code. Verdict approved only for full marks; revise when a concrete fix exists (give suggested_target when the target is wrong); reject when the behaviour is not in the source. Return one review per supplied case ID. The host computes the final confidence from your scores and its caps; do not inflate scores.",
        "rubric": {"dimensions": DESIGN_RUBRIC, "verdict_caps": {"revise": 79, "reject": 0}},
        "output_contract": DesignReviewDraft.model_json_schema(),
        "json_rules": {"single_complete_object": True, "markdown_fences": False},
        "cases_under_review": cases,
        "approved_inputs": {"requirements": requirements},
        "target_source": {target: text for target, text in sources.items() if text},
        "revision_feedback": None,
    }
    persona = (
        stack_expertise(state.get("stack"), analysis.get("languages"), analysis.get("frameworks", ())) + TASK
    )
    repair = None
    reviews: list[dict] = []
    for attempt in range(1, MODEL_REPAIR_ATTEMPTS + 1):
        calls: list[dict] = []
        try:
            result = await host._call_model(
                stage="test_design",
                prompt_batch=f"review-{batch_index}",
                role=ROLE,
                skill_name="generic-test-generator",
                companions=(),
                persona=persona,
                prompt=build_prompt({**sections, "repair_feedback": repair}),
                state=state,
                persist=host.save,
                response_format=DesignReviewDraft,
            )
            calls.append(result)
            if result.get("status") != "completed":
                raise ValueError("Design reviewer did not produce a completed review.")
            reviews = DesignReviewDraft.model_validate(result["artifact"]).model_dump()["reviews"]
            ids = [r["case_id"] for r in reviews]
            if len(ids) != len(set(ids)) or set(ids) != {str(c["id"]) for c in cases}:
                raise ValueError("Review every supplied case ID exactly once, without inventing IDs.")
            break
        except (ValueError, AgentOutputError) as exc:
            repair = str(exc)[:600]
            host.audit(
                "design_reviewer_contract_repair",
                stage="test_design",
                batch=batch_index,
                attempt=attempt,
                error=repair,
            )
            if attempt == MODEL_REPAIR_ATTEMPTS:  # the gate decides what an unusable reviewer means
                host.audit("design_review_skipped", stage="test_design", batch=batch_index, error=repair)
                host.save()
                return []
    by_id = {str(c["id"]): c for c in cases}
    scored = []
    for review in reviews:
        case = by_id[review["case_id"]]
        item = score_case(review, case, sources.get(str(case.get("target") or ""), ""), threshold)
        append_score(
            host.run_dir,
            kind="design",
            subject_id=item["case_id"],
            control_ids=case.get("control_ids") or [],
            batch=batch_index,
            scored=item,
            threshold=threshold,
            calls=calls,
            gate="hard" if gate_on(state) else "advisory",
        )
        scored.append(item)
    scored.sort(key=lambda r: (-r["confidence"], r["case_id"]))
    state.setdefault("design_reviews", {})[str(batch_index)] = scored
    host.audit("design_reviewer_result", stage="test_design", batch=batch_index, reviews=scored)
    host.save()
    return scored

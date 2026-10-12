"""Independent Chief Principal Architect review of each recommended design case (advisory, bounded)."""

import os

from ..agent.personas import chief_architect_persona
from ..agent.runtime import AgentOutputError
from ..llm.prompts import build_prompt
from ..models import DesignReviewDraft
from ..qa.report import design_cases
from ..repo.analyzer import extract_target_sources
from ..repo.testability import testability_for_targets
from ..settings import review_threshold
from ..workflow_support import MODEL_REPAIR_ATTEMPTS


def enabled() -> bool:
    return os.getenv("HARNESS_DESIGN_REVIEW", "1").strip().lower() not in {"0", "false", "off", "no"}


def min_confidence(state: dict | None = None) -> int:
    return review_threshold("design", state)


def flagged(reviews, state: dict | None = None):
    gate = min_confidence(state)
    return [r for r in reviews if r["verdict"] == "reject" or r["confidence"] < gate]


def repair_feedback(reviews) -> str:
    return (
        "Independent architect review flagged these cases; fix, retarget or replace them using only source-proven behaviour: "
        + "; ".join(
            f"{r['case_id']} (confidence {r['confidence']}, {r['verdict']}): {r['reason']}"
            + (f" Suggested target: {r['suggested_target']}." if r.get("suggested_target") else "")
            for r in reviews
        )
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
    analysis = state["artifacts"].get("repo_analysis", {})
    root = host.run_dir / "repo"
    targets = [str(c.get("target") or "") for c in cases]
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
        "assignment": "Independently validate EACH recommended test design case against the actual source. Judge: is the behaviour source-proven, is the target the method that implements it, are fixtures realistic and testable for this stack, is the expected result derivable from source. Return one review per supplied case ID with verdict approved|revise|reject, confidence 0-100 (evidence-based judgment, not a probability) and a concrete reason; give suggested_target when the target is wrong. Use target_source as the authoritative implementation.",
        "output_contract": DesignReviewDraft.model_json_schema(),
        "json_rules": {"single_complete_object": True, "markdown_fences": False},
        "testability": testability_for_targets(
            analysis.get("testability"), targets, root, analysis.get("source_files", [])
        )
        or None,
        "cases_under_review": cases,
        "approved_inputs": {"requirements": requirements},
        "target_source": extract_target_sources(root, targets, analysis.get("source_files", [])),
        "revision_feedback": None,
    }
    persona = chief_architect_persona(
        state.get("stack"), analysis.get("languages"), analysis.get("frameworks", ())
    )
    repair = None
    for attempt in range(1, MODEL_REPAIR_ATTEMPTS + 1):
        try:
            result = await host._call_model(
                stage="test_design",
                prompt_batch=f"review-{batch_index}",
                role="design_reviewer",
                skill_name="generic-test-generator",
                companions=(),
                persona=persona
                + "\nTASK: validate recommended test design cases; follow the review output contract.",
                prompt=build_prompt({**sections, "repair_feedback": repair}),
                state=state,
                persist=host.save,
                response_format=DesignReviewDraft,
            )
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
            if attempt == MODEL_REPAIR_ATTEMPTS:  # advisory: never block design on an unusable reviewer
                host.audit("design_review_skipped", stage="test_design", batch=batch_index, error=repair)
                return []
    state.setdefault("design_reviews", {})[str(batch_index)] = reviews
    host.audit("design_reviewer_result", stage="test_design", batch=batch_index, reviews=reviews)
    host.save()
    return reviews

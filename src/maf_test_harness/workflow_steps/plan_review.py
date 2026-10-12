"""Confidence reviewer of test planning areas: rubric points from the reviewer, host caps, a hard gate at 95.

Each PLN area is checked for three things: its controls are covered by its evidence_files, its level fits the evidence,
and its scenarios can be derived from that evidence. The host checks the evidence files against the tracked repository
files and the controls against the requirement source files, applies the caps, and records every pass in the ledger.
An area's evidence file covers a control when it is one of the control requirement's source files or lies in the same
directory as one. Cross-cutting areas (security, performance, regression) may have no control: that is a note, not a
cap. Below the gate, the planning model is re-asked up to MODEL_REPAIR_ATTEMPTS times; a stage still below the gate
raises `Confidence gate: test planning ...` and test design never starts. A reviewer that cannot answer raises
ConfidenceReviewerUnavailable (see planning.py).
"""

from pathlib import PurePosixPath

from ..agent.personas import stack_expertise
from ..agent.runtime import AgentOutputError, GatewayUnavailableError
from ..llm.prompts import build_prompt
from ..models import PlanReviewDraft
from ..qa.confidence import PLAN_RUBRIC, reviewer_unavailable, rubric_score
from ..qa.ledger import append_score
from ..settings import full_accuracy, review_threshold
from ..workflow_support import MODEL_REPAIR_ATTEMPTS
from .greenfield import has_no_code

ROLE = "plan_reviewer"
TASK = "\nTASK: score each test plan area for control coverage, level fit and derivable scenarios; follow the review output contract."


def min_confidence(state: dict | None = None) -> int:
    return review_threshold("plan", state)


def gate_on(state: dict | None = None) -> bool:
    return full_accuracy(state) and not has_no_code(state or {})


def _requirement_files(state: dict) -> dict[str, set[str]]:
    requirements = (
        state["artifacts"].get("requirements", {}).get("requirement_model", {}).get("requirements", [])
    )
    return {str(r["id"]): set(r.get("source_files") or []) for r in requirements}


def repository_files(state: dict) -> set[str]:
    """Every tracked repository file (configs, specs, manifests and docs included), plus the analysed source and tests."""
    analysis = state["artifacts"].get("repo_analysis", {})
    return (
        set(analysis.get("tracked_files", []))
        | set(analysis.get("source_files", []))
        | set(analysis.get("test_files", []))
        | set(analysis.get("package_files", []))
    )


def _covers(requirement_files: set[str], evidence: set[str]) -> bool:
    """A requirement source file is among the evidence, or shares its directory with an evidence file."""
    directories = {str(PurePosixPath(path).parent) for path in evidence}
    return any(
        path in evidence or str(PurePosixPath(path).parent) in directories for path in requirement_files
    )


def score_area(
    review: dict, area: dict, controls: list[dict], req_files: dict, repo_files: set, threshold: int
) -> dict:
    """The reviewed area with host-computed confidence and its evidence-file checks."""
    scores = {dimension: int(review["scores"][dimension]) for dimension in PLAN_RUBRIC}
    evidence_files = list(area.get("evidence_files") or [])
    known = {path for path in evidence_files if path in repo_files}
    mine = [c for c in controls if c.get("plan_area_id") == area.get("area_id")]
    findings: list[dict] = []
    if not known:
        findings.append(
            {
                "rule": "evidence_missing",
                "cap": 40,
                "reason": "None of the area's evidence_files is a tracked file of the repository.",
            }
        )
    if not mine:
        # Cross-cutting areas (security, performance, regression) legitimately have no assigned control.
        findings.append(
            {
                "rule": "no_controls",
                "cap": None,
                "reason": "No approved requirement is assigned to this area (note: cross-cutting areas may have none).",
            }
        )
    elif not any(_covers(req_files.get(c["requirement_id"], set()), known) for c in mine):
        findings.append(
            {
                "rule": "controls_uncovered",
                "cap": 70,
                "reason": f"None of the source files of its {len(mine)} control requirement(s) is among its evidence_files "
                "or in the same directory as one.",
            }
        )
    scored = rubric_score("plan", scores, findings, review["verdict"])
    return {
        **scored,
        "area_id": str(review["area_id"]),
        "verdict": review["verdict"],
        "reason": review["reason"],
        "accepted": review["verdict"] == "approved" and scored["confidence"] >= threshold,
        "evidence": [{"quote": path, "exists": path in repo_files} for path in evidence_files],
        "derivations": [],
    }


def weak_areas(reviews, artifact: dict, state: dict | None = None) -> list[dict]:
    """Areas below the gate or rejected, plus a stand-in for every area the reviewer did not answer."""
    gate = min_confidence(state)
    answered = {r["area_id"] for r in reviews}
    weak = [r for r in reviews if not r["accepted"] or r["confidence"] < gate]
    for area in artifact.get("areas") or []:
        aid = str(area.get("area_id"))
        if aid not in answered:
            weak.append(
                {
                    "area_id": aid,
                    "confidence": 0,
                    "verdict": "unreviewed",
                    "reason": "The plan reviewer did not return a usable review for this area.",
                    "caps": [],
                }
            )
    return weak


def repair_feedback(weak: list[dict]) -> str:
    return (
        "Independent confidence review flagged these plan areas; re-plan them so every control is covered by the area's "
        "evidence_files (a file of the control's source or in its directory), the level matches the evidence and each "
        "scenario is derivable from it: "
        + "; ".join(
            f"{r['area_id']} (confidence {r['confidence']}, {r['verdict']}): {r['reason']}"
            + "".join(f" Cap {c['rule']} {c['cap']}: {c['reason']}" for c in r.get("caps", []))
            for r in weak
        )
    )


def gate_message(weak: list[dict], total: int, state: dict | None = None) -> str:
    return (
        f"Confidence gate: test planning {len(weak)} of {total} areas are below the {min_confidence(state)} "
        "confidence gate or were not reviewed; test design is blocked: "
        + "; ".join(
            f"{r['area_id']} confidence {r['confidence']} ({r['verdict']}): {r['reason']}" for r in weak
        )[:1800]
        + ". Options: (1) revise the plan with the feedback above; (2) edit the test planning reviewer prompt in Settings; "
        "(3) start the run with full accuracy off (require_full_accuracy false, or HARNESS_REQUIRE_FULL_ACCURACY=0)."
    )


async def review_plan_areas(host, state, artifact: dict, batch_index) -> list[dict]:
    """Score every area of one planning artifact. Raises ConfidenceReviewerUnavailable when the reviewer cannot answer."""
    analysis = state["artifacts"].get("repo_analysis", {})
    threshold = min_confidence(state)
    req_files = _requirement_files(state)
    repo_files = repository_files(state)
    areas = artifact.get("areas") or []
    controls = artifact.get("controls") or []
    sections = {
        "assignment": "Independently review EACH plan area. Score three dimensions from the evidence (scores field): controls_covered (0-40) how completely evidence_files cover the source files of the area's controls (a file of the control's source, or one in the same directory, counts); level_fit (0-30) whether the declared level matches the evidence; scenarios_derivable (0-30) whether every scenario can be derived from the cited files. An area with no control is allowed when it is cross-cutting (security, performance, regression); score its controls_covered from its evidence alone. Verdict approved only for full marks; revise when a concrete fix exists; reject when the area cannot be grounded in the repository. Return one review per supplied area_id. The host computes the final confidence from your scores and its caps; do not inflate scores.",
        "rubric": {"dimensions": PLAN_RUBRIC, "verdict_caps": {"revise": 79, "reject": 0}},
        "output_contract": PlanReviewDraft.model_json_schema(),
        "json_rules": {"single_complete_object": True, "markdown_fences": False},
        "plan_areas": [
            {
                k: area.get(k)
                for k in ("area_id", "name", "objective", "level", "risk", "evidence_files", "scenarios")
            }
            for area in areas
        ],
        "controls": [
            {
                "control_id": c["control_id"],
                "plan_area_id": c.get("plan_area_id"),
                "requirement_id": c["requirement_id"],
                "requirement_text": c.get("requirement_text"),
                "requirement_source_files": sorted(req_files.get(c["requirement_id"], set())),
            }
            for c in controls
        ],
        "repository_files": sorted(repo_files)[:400],
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
                stage="test_planning",
                prompt_batch=f"plan-review-{batch_index}",
                role=ROLE,
                skill_name="test-reviewer",
                companions=(),
                persona=persona,
                prompt=build_prompt({**sections, "repair_feedback": repair}),
                state=state,
                persist=host.save,
                response_format=PlanReviewDraft,
            )
            calls.append(result)
            if result.get("status") != "completed":
                raise ValueError("Plan reviewer did not produce a completed review.")
            reviews = PlanReviewDraft.model_validate(result["artifact"]).model_dump()["reviews"]
            ids = [r["area_id"] for r in reviews]
            if len(ids) != len(set(ids)) or set(ids) != {str(a.get("area_id")) for a in areas}:
                raise ValueError("Review every supplied area_id exactly once, without inventing IDs.")
            break
        except GatewayUnavailableError as exc:
            host.audit(
                "plan_reviewer_unavailable", stage="test_planning", batch=batch_index, error=str(exc)[:300]
            )
            host.save()
            raise reviewer_unavailable("test planning", f"gateway unreachable: {str(exc)[:400]}") from exc
        except (ValueError, AgentOutputError) as exc:
            repair = str(exc)[:600]
            host.audit(
                "plan_reviewer_contract_repair",
                stage="test_planning",
                batch=batch_index,
                attempt=attempt,
                error=repair,
            )
            if attempt == MODEL_REPAIR_ATTEMPTS:
                host.save()
                raise reviewer_unavailable(
                    "test planning",
                    f"returned no usable review after {MODEL_REPAIR_ATTEMPTS} attempts: {repair}",
                ) from exc
    by_id = {str(a.get("area_id")): a for a in areas}
    epoch = int(state.get("ledger_epoch") or 0)
    scored = []
    for review in reviews:
        area = by_id[review["area_id"]]
        item = score_area(review, area, controls, req_files, repo_files, threshold)
        append_score(
            host.run_dir,
            kind="plan",
            subject_id=item["area_id"],
            control_ids=[c["control_id"] for c in controls if c.get("plan_area_id") == item["area_id"]],
            batch=batch_index,
            scored=item,
            threshold=threshold,
            calls=calls,
            gate="hard" if gate_on(state) else "advisory",
            epoch=epoch,
        )
        scored.append(item)
    scored.sort(key=lambda r: (-r["confidence"], r["area_id"]))
    host.audit("plan_reviewer_result", stage="test_planning", batch=batch_index, reviews=scored)
    host.save()
    return scored

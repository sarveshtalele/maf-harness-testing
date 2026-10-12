"""Independent MAF review of generated cases against their actual source evidence."""

import re

from ..agent.personas import chief_architect_persona
from ..agent.runtime import AgentOutputError
from ..llm.prompts import generation_prompt
from ..models import TestReviewDraft
from ..qa.confidence import (
    RUBRIC,
    derivation_findings,
    resolve_target,
    review_accepted,
    score_review,
    verification_note,
)
from ..qa.derivation import deterministic_findings, mask_expected_literals
from ..repo.analyzer import build_repository_evidence, extract_target_sources
from ..settings import TEST_REVIEW_MIN_CONFIDENCE
from ..workflow_support import MODEL_REPAIR_ATTEMPTS


def _case_source(root, target: str, source_files: list[str], max_chars: int = 24_000) -> str:
    """The target's own source blocks. A "module.function" target resolves through its symbol when no class or module
    of that name declares it."""
    if not target:
        return ""
    found = extract_target_sources(root, [target], source_files, max_chars)
    resolved = resolve_target(target)
    if not found and resolved is not None and resolved.kind != "route":
        found = extract_target_sources(root, [resolved.symbol], source_files, max_chars)
    return found


def _confident_target(target: str) -> bool:
    resolved = resolve_target(target)
    return resolved is not None and resolved.kind != "route"


def _own_block_present(text: str, target: str) -> bool:
    """True when the target's own block (its symbol or owning class) survived the size budget."""
    resolved = resolve_target(target)
    if resolved is None or resolved.kind == "route":
        return True
    names = {resolved.symbol, resolved.owner, f"{resolved.owner}.{resolved.symbol}"} - {"."}
    names.discard("")
    return any(re.search(rf" :: {re.escape(name)} \(lines ", text) for name in names)


def _source_found(root, target: str, source_files: list[str]) -> bool:
    """Only a confident code symbol needs source (routes and free-text targets are not caps-checked against it)."""
    resolved = resolve_target(target)
    if resolved is None or resolved.kind == "route":
        return True
    return bool(_case_source(root, target, source_files))


async def review_generated_tests(host, state, candidate, cases, batch, partial_ok: bool = False):
    """Review each case; raise on any rejection unless partial_ok (last attempt), which returns every review."""
    requirements = state["artifacts"].get("requirements", {})
    linked = {link["id"] for case in cases for link in case.get("traceability_links", [])}
    selected = [
        r for r in requirements.get("requirement_model", {}).get("requirements", []) if r["id"] in linked
    ]
    paths = [p for r in selected for p in r.get("source_files", [])]
    targets = [str(case.get("target") or "") for case in cases]
    root = (host.run_dir / "repo").resolve()
    source_files = state["artifacts"].get("repo_analysis", {}).get("source_files", [])
    # Each case gets its own share of the budget, so one large class cannot push another case's target out of the prompt.
    budget = max(4_000, 24_000 // max(1, len(cases)))
    target_source = {
        str(case["id"]): _case_source(root, str(case.get("target") or ""), source_files, budget)
        for case in cases
    }
    source = build_repository_evidence(
        host.run_dir / "repo",
        max_files=max(1, len(set(paths))),
        max_total_chars=40000,
        preferred_paths=paths,
        target_symbols=targets,
    )
    # Blind derivation: each assertion's right-hand literal is masked as «E<n>» and the expected values are withheld.
    masked_files, slots = mask_expected_literals(candidate["files"])
    sections = {
        "assignment": "Independently review EACH generated test against actual repository source. Score every rubric dimension conservatively from evidence (scores field); full marks only when the behaviour is verifiable in target_source; name the lowest-scoring dimension in reason. Every assertion literal is masked as «E<n>» on purpose: for EACH slot of a case, derive the value the implementation produces for the fixture in that test, and return it in derivations (slot, value, steps, source_quote as verbatim target_source lines, fixture_quote as verbatim test lines that supply the input). Count, length, rounding and arithmetic must be computed from the code, not guessed. The host computes the final confidence from your scores, your derivations and deterministic caps, so do not inflate scores. Do not rewrite tests or trust author claims or explanatory comments. Flag nonexistent imports/APIs, wrong assumptions/assertions, invalid fixtures, ineffective mocks and missing invocation of claimed methods. Return one review per supplied case ID. Missing evidence must be insufficient_evidence, not supported. Scores are evidence-based judgments, not calibrated probabilities or runtime proof. Use target_source as the authoritative complete implementation of each targeted method. Do not return insufficient_evidence merely because repository_evidence is a truncated whole-file excerpt when target_source contains the complete method.",
        "strict_quality_rules": {
            "rubric_points": RUBRIC,
            "supported": "Source supports target, setup and assertion; execution is still required.",
            "needs_revision": "Concrete source contradiction or ineffective test.",
            "insufficient_evidence": "Needed behavior cannot be established from supplied source.",
        },
        # The design's expected_result is withheld: the reviewer must not anchor on an expectation it did not derive.
        "approved_inputs": {
            "approved_cases": [{k: v for k, v in c.items() if k != "expected_result"} for c in cases],
            "requirements": selected,
        },
        "generated_source": masked_files,
        "target_source": target_source,  # per case ID: the complete implementation of that case's target
        "repository_evidence": source,
        "citation_rule": "source_files must cite existing repository implementation files supporting your assessment. Do not cite newly generated tests; their code is supplied separately under generated_source.",
        "revision_feedback": state.get("feedback", {}).get(
            "test_generation"
        ),  # heal rounds: execution evidence
        "output_contract": TestReviewDraft.model_json_schema(),
        "json_rules": {"single_complete_object": True, "markdown_fences": False},
    }
    repair = None
    for attempt in range(1, MODEL_REPAIR_ATTEMPTS + 1):
        try:
            result = await host._call_model(
                stage="test_generation",
                prompt_batch=f"review-{batch}",
                role="test_reviewer",
                skill_name="test-reviewer",
                companions=(),
                persona=chief_architect_persona(
                    state.get("stack"),
                    state["artifacts"].get("repo_analysis", {}).get("languages"),
                    state["artifacts"].get("repo_analysis", {}).get("frameworks", ()),
                )
                + "\nTASK: review generated tests against source evidence using the review output contract (not a generation contract). Score the five rubric dimensions conservatively and explain uncertainties.",
                prompt=generation_prompt({**sections, "repair_feedback": repair}),
                state=state,
                persist=host.save,
                response_format=TestReviewDraft,
            )
            if result.get("status") != "completed":
                raise ValueError("Independent test reviewer did not produce a completed review.")
            reviews = TestReviewDraft.model_validate(result["artifact"]).model_dump()["reviews"]
            ids = [r["case_id"] for r in reviews]
            if len(ids) != len(set(ids)) or set(ids) != {str(c["id"]) for c in cases}:
                raise ValueError(
                    "Reviewer must assess every supplied case ID exactly once, without inventing IDs."
                )
            for review in reviews:
                for path in review["source_files"]:
                    target = root / path
                    if (
                        target.is_symlink()
                        or not target.resolve().is_relative_to(root)
                        or not target.is_file()
                    ):
                        raise ValueError(
                            f"Reviewer cited unavailable source: {path}. Cite existing implementation paths from repository_evidence, not generated test paths."
                        )
            break
        except (ValueError, AgentOutputError) as exc:
            repair = str(exc)
            host.audit(
                "test_reviewer_contract_repair",
                stage="test_generation",
                batch=batch,
                attempt=attempt,
                error=repair,
            )
            host.save()
            if attempt == MODEL_REPAIR_ATTEMPTS:
                raise
    by_case = {str(c["id"]): c for c in cases}
    scored = []
    for review in reviews:
        case = by_case[review["case_id"]]
        target = str(case.get("target") or "")
        files = [f["content"] for f in candidate["files"] if review["case_id"] in f["case_ids"]]
        case_source = target_source.get(review["case_id"], "")
        findings = [
            finding for text in files for finding in deterministic_findings(text, case_source, target)
        ]
        if (
            target
            and _case_source(root, target, source_files, 10**9)
            and not _own_block_present(case_source, target)
        ):
            findings.append(
                {
                    "rule": "target_source_dropped",
                    "cap": 50,
                    "reason": f"The target `{target}` is larger than this case's share of the review budget; "
                    "its own implementation was not shown to the reviewer.",
                }
            )
        findings += derivation_findings(
            slots=[s for s in slots if review["case_id"] in s["case_ids"]],
            derivations=review.get("derivations", []),
            target_source=case_source,
            test_text="\n".join(files),
            require_source=_confident_target(target),
        )
        scored.append(
            score_review(
                review,
                target=target,
                content="\n".join(files),
                source_found=_source_found(root, target, source_files),
                findings=findings,
            )
        )
    reviews = scored
    rejected = [r for r in reviews if not review_accepted(r, TEST_REVIEW_MIN_CONFIDENCE)]
    host.audit("test_reviewer_result", stage="test_generation", batch=batch, reviews=reviews)
    state.setdefault("test_reviews", []).append({"batch": batch, "reviews": reviews})
    host.save()
    if rejected and not partial_ok:
        raise ValueError(
            "Independent test review requires repair: "
            + "; ".join(
                f"{r['case_id']}: {verification_note(r, TEST_REVIEW_MIN_CONFIDENCE)}" for r in rejected
            )
        )
    return sorted(reviews, key=lambda r: (-r["confidence"], r["case_id"]))

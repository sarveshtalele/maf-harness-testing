"""Independent MAF review of generated cases against their actual source evidence."""

from ..agent.personas import chief_architect_persona
from ..llm.prompts import generation_prompt
from ..models import TestReviewDraft
from ..qa.confidence import RUBRIC, review_accepted, score_review, target_symbol, verification_note
from ..repo.analyzer import build_repository_evidence, extract_target_sources
from ..settings import TEST_REVIEW_MIN_CONFIDENCE
from ..workflow_support import MODEL_REPAIR_ATTEMPTS


def _source_found(root, target: str, source_files: list[str]) -> bool:
    """A "module.function" target resolves through its symbol when no class/module of that name declares it."""
    if not target.strip():
        return True
    return bool(extract_target_sources(root, [target], source_files)) or bool(
        extract_target_sources(root, [target_symbol(target)], source_files)
    )


async def review_generated_tests(host, state, candidate, cases, batch):
    requirements = state["artifacts"].get("requirements", {})
    linked = {link["id"] for case in cases for link in case.get("traceability_links", [])}
    selected = [
        r for r in requirements.get("requirement_model", {}).get("requirements", []) if r["id"] in linked
    ]
    paths = [p for r in selected for p in r.get("source_files", [])]
    targets = [str(case.get("target") or "") for case in cases]
    target_source = extract_target_sources(
        host.run_dir / "repo", targets, state["artifacts"].get("repo_analysis", {}).get("source_files", [])
    )
    source = build_repository_evidence(
        host.run_dir / "repo",
        max_files=max(1, len(set(paths))),
        max_total_chars=40000,
        preferred_paths=paths,
        target_symbols=targets,
    )
    sections = {
        "assignment": "Independently review EACH generated test against actual repository source. Score every rubric dimension conservatively from evidence (scores field); full marks only when the behaviour is verifiable in target_source; name the lowest-scoring dimension in reason. The host computes the final confidence from your scores and deterministic caps, so do not inflate scores. Do not rewrite tests or trust author claims or explanatory comments. Independently derive every expected literal from the actual fixture and implementation, including string lengths, counts, rounding and arithmetic; author calculations are unverified input. Flag nonexistent imports/APIs, wrong assumptions/assertions, invalid fixtures, ineffective mocks and missing invocation of claimed methods. Return one review per supplied case ID. Missing evidence must be insufficient_evidence, not supported. Scores are evidence-based judgments, not calibrated probabilities or runtime proof. Use target_source as the authoritative complete implementation of each targeted method. Do not return insufficient_evidence merely because repository_evidence is a truncated whole-file excerpt when target_source contains the complete method.",
        "strict_quality_rules": {
            "rubric_points": RUBRIC,
            "supported": "Source supports target, setup and assertion; execution is still required.",
            "needs_revision": "Concrete source contradiction or ineffective test.",
            "insufficient_evidence": "Needed behavior cannot be established from supplied source.",
        },
        "approved_inputs": {"approved_cases": cases, "requirements": selected},
        "generated_source": candidate["files"],
        "target_source": target_source,
        "repository_evidence": source,
        "citation_rule": "source_files must cite existing repository implementation files supporting your assessment. Do not cite newly generated tests; their code is supplied separately under generated_source.",
        "output_contract": TestReviewDraft.model_json_schema(),
        "json_rules": {"single_complete_object": True, "markdown_fences": False},
    }
    root = (host.run_dir / "repo").resolve()
    source_files = state["artifacts"].get("repo_analysis", {}).get("source_files", [])
    repair = None
    for attempt in range(1, MODEL_REPAIR_ATTEMPTS + 1):
        try:
            result = await host._call_model(
                stage="test_generation",
                prompt_batch=f"review-{batch}",
                role="test_reviewer",
                skill_name="generic-test-generator",
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
        except ValueError as exc:
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
        content = "\n".join(f["content"] for f in candidate["files"] if review["case_id"] in f["case_ids"])
        scored.append(
            score_review(
                review,
                target=target,
                content=content,
                source_found=_source_found(root, target, source_files),
            )
        )
    reviews = scored
    rejected = [r for r in reviews if not review_accepted(r, TEST_REVIEW_MIN_CONFIDENCE)]
    host.audit("test_reviewer_result", stage="test_generation", batch=batch, reviews=reviews)
    state.setdefault("test_reviews", []).append({"batch": batch, "reviews": reviews})
    host.save()
    if rejected:
        raise ValueError(
            "Independent test review requires repair: "
            + "; ".join(
                f"{r['case_id']}: {verification_note(r, TEST_REVIEW_MIN_CONFIDENCE)}" for r in rejected
            )
        )
    return sorted(reviews, key=lambda r: (-r["confidence"], r["case_id"]))

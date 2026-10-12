"""Independent MAF review of generated cases against their actual source evidence.

Blind derivation: the reviewer derives every masked assertion literal («E<n>») from the implementation. Format problems
(a missing or unverifiable derivation) are repaired inside the reviewer loop, DERIVATION_RETRIES times, before the host
caps the case. A verified derivation that disagrees with the asserted literal is re-asked once with the literal
revealed; only a confirmed disagreement caps the case (40). Reviewer mistakes are charged to the reviewer.
"""

from ..agent.personas import stack_expertise
from ..agent.runtime import AgentOutputError, GatewayUnavailableError
from ..llm.prompts import generation_prompt
from ..models import DerivationRecheckDraft, TestReviewDraft
from ..qa.confidence import (
    RUBRIC,
    ConfidenceReviewerUnavailable,
    confident_target,
    derivation_findings,
    derivation_problems,
    derivation_status,
    resolve_target,
    review_accepted,
    reviewer_unavailable,
    score_review,
    settle_deterministic,
    verification_note,
)
from ..qa.derivation import deterministic_findings, mask_expected_literals
from ..qa.ledger import append_score
from ..repo.analyzer import build_repository_evidence, extract_target_sources
from ..settings import full_accuracy, test_review_threshold
from ..workflow_support import MODEL_REPAIR_ATTEMPTS
from .greenfield import has_no_code

TARGET_BUDGET = 28_000  # the old 24k shared budget plus a modest margin; method bodies are always sent
DERIVATION_RETRIES = 2  # format repairs the reviewer gets before the host applies its derivation caps
REVIEW_ROLE = "test_reviewer"


def _case_source(
    root, target: str, source_files: list[str], max_chars: int = 24_000, hop: bool = True
) -> str:
    """The target's own source blocks. A "module.function" target resolves through its symbol when no class or module
    of that name declares it."""
    if not target:
        return ""
    found = extract_target_sources(root, [target], source_files, max_chars, hop)
    resolved = resolve_target(target)
    if not found and resolved is not None and resolved.kind != "route":
        found = extract_target_sources(root, [resolved.symbol], source_files, max_chars, hop)
    return found


def _exists(root, path: str) -> bool:
    """A reviewer-cited source file that is really present in the analysed repository (no symlink escape)."""
    target = root / path
    return not target.is_symlink() and target.resolve().is_relative_to(root) and target.is_file()


def _source_found(root, target: str, source_files: list[str]) -> bool:
    """Only a confident code symbol needs source (routes and free-text targets are not caps-checked against it)."""
    if not confident_target(target):
        return True
    return bool(_case_source(root, target, source_files))


def _review_sources(root, cases, source_files: list[str]) -> tuple[dict[str, str], set[str]]:
    """Source text per distinct target (each sent once), and the targets whose method body alone exceeds the budget.

    Every target's own method body is sent. Its helpers (one hop) are added while the budget allows, smallest first.
    """
    targets = list(dict.fromkeys(str(c.get("target") or "") for c in cases))
    bodies = {t: _case_source(root, t, source_files, 10**9, hop=False) for t in targets}
    fulls = {t: _case_source(root, t, source_files, 10**9) for t in targets}
    oversized = {t for t in targets if len(bodies[t]) > TARGET_BUDGET}
    texts = dict(bodies)
    used = sum(len(texts[t]) for t in targets)
    for target in sorted(targets, key=lambda t: len(fulls[t]) - len(bodies[t])):
        extra = len(fulls[target]) - len(bodies[target])
        if target in oversized or extra <= 0 or used + extra > TARGET_BUDGET:
            continue
        texts[target] = fulls[target]
        used += extra
    return texts, oversized


def _settle(host, validated: bool, role: str = REVIEW_ROLE) -> None:
    """Tell the runtime whether the reviewer's last answer was accepted (only accepted answers are cached)."""
    settle = getattr(host, "_settle_response_cache", None)
    if settle is not None:
        settle("test_generation", validated, role=role)


def _case_views(cid: str, case_target: dict[str, str], slots, candidate, masked_files, target_texts):
    """The slots, source texts and test texts one case is reviewed against."""
    target = case_target[cid]
    return {
        "slots": [s for s in slots if cid in s["case_ids"]],
        "source": target_texts.get(target, ""),
        "content": "\n".join(f["content"] for f in candidate["files"] if cid in f["case_ids"]),
        "masked": "\n".join(f["content"] for f in masked_files if cid in f["case_ids"]),
        "require_source": confident_target(target),
    }


def _format_problems(reviews, slots, case_target, target_texts, candidate, masked_files) -> list[str]:
    problems: list[str] = []
    for review in reviews:
        cid = review["case_id"]
        view = _case_views(cid, case_target, slots, candidate, masked_files, target_texts)
        for problem in derivation_problems(
            slots=view["slots"],
            derivations=review.get("derivations", []),
            target_source=view["source"],
            test_text=view["content"],
            masked_text=view["masked"],
            require_source=view["require_source"],
        ):
            problems.append(f"{cid}: {problem}")
    return problems


async def _recheck_mismatches(
    host, *, state, reviews, slots, case_target, target_texts, candidate, masked_files, persona, batch, calls
):
    """Re-ask once for verified derivations that disagree with the literal, with the literal revealed."""
    flagged: dict[str, str] = {}
    for review in reviews:
        cid = review["case_id"]
        view = _case_views(cid, case_target, slots, candidate, masked_files, target_texts)
        status = derivation_status(
            slots=view["slots"],
            derivations=review.get("derivations", []),
            target_source=view["source"],
            test_text=view["content"],
            masked_text=view["masked"],
            require_source=view["require_source"],
        )
        flagged.update(dict.fromkeys(status["mismatch"], cid))
    if not flagged:
        return reviews
    by_id = {s["slot"]: s for s in slots}
    previous = {
        d["slot"]: d
        for review in reviews
        if review["case_id"] in set(flagged.values())
        for d in review.get("derivations", [])
        if d["slot"] in flagged
    }
    affected = sorted(set(flagged.values()))
    prompt = generation_prompt(
        {
            "assignment": "Your derivation of these assertion literals disagrees with the literal the test asserts. The "
            "asserted literal is revealed below. Find the step where your derivation departs from the implementation. "
            "Return a corrected derivation for EACH listed slot (value, steps, verbatim source_quote from target_source, "
            "verbatim fixture_quote). If the implementation really produces your value, repeat it and say in steps why "
            "the assertion is wrong.",
            "slots_to_recheck": [
                {
                    "case_id": flagged[slot],
                    "slot": slot,
                    "asserted_literal": str(by_id[slot]["literal"]),
                    "previous_value": previous[slot]["value"],
                    "previous_steps": previous[slot]["steps"],
                }
                for slot in sorted(flagged)
            ],
            "generated_source": [f for f in masked_files if set(f["case_ids"]) & set(affected)],
            "target_source": {t: target_texts[t] for t in {case_target[c] for c in affected}},
            "case_target": {c: case_target[c] for c in affected},
            "output_contract": DerivationRecheckDraft.model_json_schema(),
            "json_rules": {"single_complete_object": True, "markdown_fences": False},
        }
    )
    try:
        result = await host._call_model(
            stage="test_generation",
            prompt_batch=f"recheck-{batch}",
            role=REVIEW_ROLE,
            skill_name="test-reviewer",
            companions=(),
            persona=persona,
            prompt=prompt,
            state=state,
            persist=host.save,
            response_format=DerivationRecheckDraft,
        )
        calls.append(result)
        if result.get("status") != "completed":
            raise ValueError("Derivation re-check did not complete.")
        answers = DerivationRecheckDraft.model_validate(result["artifact"]).model_dump()["derivations"]
    except GatewayUnavailableError as exc:
        raise reviewer_unavailable(
            "generated tests", f"re-check gateway unreachable: {str(exc)[:300]}"
        ) from exc
    except (ValueError, AgentOutputError) as exc:
        host.audit(
            "test_reviewer_recheck_skipped", stage="test_generation", batch=batch, error=str(exc)[:300]
        )
        host.save()
        _settle(host, False)
        return reviews  # the first derivation stands: a mismatch that was not re-confirmed still caps at 40
    _settle(host, True)
    answered = {a["slot"]: a for a in answers if a["slot"] in flagged}
    for review in reviews:
        review["derivations"] = [
            answered[d["slot"]] if d["slot"] in answered and flagged[d["slot"]] == review["case_id"] else d
            for d in review.get("derivations", [])
        ]
    host.audit(
        "test_reviewer_recheck",
        stage="test_generation",
        batch=batch,
        slots=sorted(flagged),
        answered=sorted(answered),
    )
    host.save()
    return reviews


def _reviewer_down(host, state: dict, batch, error: ConfidenceReviewerUnavailable) -> list:
    """An unavailable reviewer is not a low score. Gate on: the stage stops with the distinct error at once (author
    repairs are not spent). Gate off: advisory, the batch's cases stay unreviewed and the audit keeps the note."""
    if full_accuracy(state) and not has_no_code(state):
        host.audit("test_reviewer_unavailable", stage="test_generation", batch=batch, error=str(error)[:300])
        host.save()
        raise error
    host.audit(
        "test_reviewer_unavailable_advisory", stage="test_generation", batch=batch, error=str(error)[:300]
    )
    host.save()
    return []


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
    case_target = {str(case["id"]): str(case.get("target") or "") for case in cases}
    target_texts, oversized = _review_sources(root, cases, source_files)
    source = build_repository_evidence(
        host.run_dir / "repo",
        max_files=max(1, len(set(paths))),
        max_total_chars=40000,
        preferred_paths=paths,
        target_symbols=targets,
    )
    # Blind derivation: each assertion's right-hand literal is masked as «E<n>» and the expected values are withheld.
    masked_files, slots = mask_expected_literals(candidate["files"])
    # The reviewer's editable persona (PERSONAS["test_reviewer"]) is prepended by the model call; this is the detected stack.
    persona = (
        stack_expertise(
            state.get("stack"),
            state["artifacts"].get("repo_analysis", {}).get("languages"),
            state["artifacts"].get("repo_analysis", {}).get("frameworks", ()),
        )
        + "\nTASK: review generated tests against source evidence using the review output contract (not a generation contract). Score the five rubric dimensions conservatively and explain uncertainties."
    )
    sections = {
        "assignment": "Independently review EACH generated test against actual repository source. Score every rubric dimension conservatively from evidence (scores field); full marks only when the behaviour is verifiable in target_source; name the lowest-scoring dimension in reason. Every assertion literal is masked as «E<n>» on purpose: for EACH slot of a case, derive the value the implementation produces for the fixture in that test, and return it in derivations (slot, value, steps, source_quote as verbatim target_source lines, fixture_quote as verbatim test lines that supply the input). Count, length, rounding and arithmetic must be computed from the code, not guessed; a filter may drop items the fixture contains. The host computes the final confidence from your scores, your derivations and deterministic caps, so do not inflate scores. Do not rewrite tests or trust author claims or explanatory comments. Flag nonexistent imports/APIs, wrong assumptions/assertions, invalid fixtures, ineffective mocks and missing invocation of claimed methods. Return one review per supplied case ID. Missing evidence must be insufficient_evidence, not supported. Scores are evidence-based judgments, not calibrated probabilities or runtime proof. target_source maps each distinct target to the complete implementation of that target (use case_target to find a case's target) and is authoritative. Do not return insufficient_evidence merely because repository_evidence is a truncated whole-file excerpt when target_source contains the complete method.",
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
        "target_source": target_texts,
        "case_target": case_target,
        "repository_evidence": source,
        "citation_rule": "source_files must cite existing repository implementation files supporting your assessment. Do not cite newly generated tests; their code is supplied separately under generated_source.",
        "revision_feedback": state.get("feedback", {}).get(
            "test_generation"
        ),  # heal rounds: execution evidence
        "output_contract": TestReviewDraft.model_json_schema(),
        "json_rules": {"single_complete_object": True, "markdown_fences": False},
    }
    repair = None
    contract_attempts = 0
    format_retries = 0
    calls: list[dict] = []  # every reviewer call behind this batch's scores, for the confidence ledger
    while True:
        contract_attempts += 1
        try:
            result = await host._call_model(
                stage="test_generation",
                prompt_batch=f"review-{batch}",
                role=REVIEW_ROLE,
                skill_name="test-reviewer",
                companions=(),
                persona=persona,
                prompt=generation_prompt({**sections, "repair_feedback": repair}),
                state=state,
                persist=host.save,
                response_format=TestReviewDraft,
            )
            calls.append(result)
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
                    if not _exists(root, path):
                        raise ValueError(
                            f"Reviewer cited unavailable source: {path}. Cite existing implementation paths from repository_evidence, not generated test paths."
                        )
            problems = _format_problems(reviews, slots, case_target, target_texts, candidate, masked_files)
            if problems and format_retries < DERIVATION_RETRIES:
                format_retries += 1
                repair = (
                    "Derivation format: "
                    + "; ".join(problems)
                    + ". Return one derivation for every «E<n>» slot of each case, with quotes copied verbatim."
                )
                host.audit(
                    "test_reviewer_derivation_repair",
                    stage="test_generation",
                    batch=batch,
                    attempt=format_retries,
                    problems=problems[:20],
                )
                host.save()
                _settle(host, False)
                continue
            break
        except GatewayUnavailableError as exc:
            _settle(host, False)
            return _reviewer_down(
                host,
                state,
                batch,
                reviewer_unavailable("generated tests", f"gateway unreachable: {str(exc)[:400]}"),
            )
        except (ValueError, AgentOutputError) as exc:
            repair = str(exc)
            host.audit(
                "test_reviewer_contract_repair",
                stage="test_generation",
                batch=batch,
                attempt=contract_attempts,
                error=repair,
            )
            host.save()
            _settle(host, False)
            if contract_attempts == MODEL_REPAIR_ATTEMPTS:
                return _reviewer_down(
                    host,
                    state,
                    batch,
                    reviewer_unavailable(
                        "generated tests",
                        f"returned no usable review after {MODEL_REPAIR_ATTEMPTS} attempts: {repair}",
                    ),
                )
    _settle(host, True)
    try:
        reviews = await _recheck_mismatches(
            host,
            state=state,
            reviews=reviews,
            slots=slots,
            case_target=case_target,
            target_texts=target_texts,
            candidate=candidate,
            masked_files=masked_files,
            persona=persona,
            batch=batch,
            calls=calls,
        )
    except ConfidenceReviewerUnavailable as error:
        return _reviewer_down(host, state, batch, error)
    scored = []
    for review in reviews:
        cid = review["case_id"]
        target = case_target[cid]
        view = _case_views(cid, case_target, slots, candidate, masked_files, target_texts)
        derivations = review.get("derivations", [])
        findings = []
        for file in candidate["files"]:
            if cid in file["case_ids"]:
                findings += deterministic_findings(file["content"], view["source"], target)
        findings = settle_deterministic(
            findings,
            slots=view["slots"],
            derivations=derivations,
            target_source=view["source"],
            test_text=view["content"],
            masked_text=view["masked"],
            require_source=view["require_source"],
        )
        if target in oversized:
            findings.append(
                {
                    "rule": "target_source_oversized",
                    "cap": 70,
                    "reason": f"The target `{target}` alone exceeds the review budget. The reviewer saw its method body "
                    "only, without the helpers it calls.",
                }
            )
        findings += derivation_findings(
            slots=view["slots"],
            derivations=derivations,
            target_source=view["source"],
            test_text=view["content"],
            masked_text=view["masked"],
            require_source=view["require_source"],
        )
        scored.append(
            score_review(
                review,
                target=target,
                content=view["content"],
                source_found=_source_found(root, target, source_files),
                findings=findings,
            )
        )
    reviews = scored
    threshold = test_review_threshold(state)
    gate = "hard" if full_accuracy(state) and not has_no_code(state) else "advisory"
    case_controls = {str(c["id"]): c.get("control_ids") or [] for c in cases}
    for review in reviews:
        append_score(
            host.run_dir,
            kind="test",
            subject_id=review["case_id"],
            control_ids=case_controls.get(review["case_id"], []),
            batch=batch,
            scored={
                "scores": review["scores"],
                "points": review["model_score"],
                "caps": review["caps"],
                "notes": review["notes"],
                "confidence": review["confidence"],
                "verdict": review["verdict"],
                "reason": review["reason"],
                "accepted": review_accepted(review, threshold),
                "derivations": review.get("derivations", []),
                "evidence": [
                    {"quote": path, "exists": _exists(root, path)} for path in review["source_files"]
                ],
            },
            threshold=threshold,
            calls=calls,
            gate=gate,
            epoch=int(state.get("ledger_epoch") or 0),
        )
    rejected = [r for r in reviews if not review_accepted(r, test_review_threshold(state))]
    host.audit("test_reviewer_result", stage="test_generation", batch=batch, reviews=reviews)
    state.setdefault("test_reviews", []).append({"batch": batch, "reviews": reviews})
    host.save()
    if rejected and not partial_ok:
        raise ValueError(
            "Independent test review requires repair: "
            + "; ".join(
                f"{r['case_id']}: {verification_note(r, test_review_threshold(state))}" for r in rejected
            )
        )
    return sorted(reviews, key=lambda r: (-r["confidence"], r["case_id"]))

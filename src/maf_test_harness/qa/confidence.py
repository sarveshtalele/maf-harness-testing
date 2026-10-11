"""Host-computed confidence for reviewed generated cases.

The reviewer model scores five rubric dimensions. The host sums them, then applies deterministic caps from the
generated file and the target's source. A cap cannot be raised by the model. The checks are conservative regular
expressions across Python, JavaScript/TypeScript and Java; they are best-effort, not parsers.
"""

import re
from typing import Any

RUBRIC: dict[str, int] = {
    "source_and_api_match": 25,
    "assertion_checks_real_behavior": 30,
    "fixture_validity": 20,
    "isolation_and_determinism": 15,
    "traceability": 10,
}

_ASSERTION = re.compile(
    r"\bassert\b|\bassert[A-Z]\w*\s*\(|\bAssert\.|\b(?:and)?[Ee]xpect\s*\(|\.should\b|\bverify\s*\("
    r"|\.to(?:Equal|Be|Throw|Contain|Match)\w*\s*\(|pytest\.raises\("
)
_PY_PATCH = re.compile(r"""\bpatch(?:\.object)?\(\s*(?:[^,()]+,\s*)?["']([\w.]+)["']""")
_JS_SPY = re.compile(r"""\bjest\.spyOn\(\s*\w+\s*,\s*["'`](\w+)["'`]""")
_JS_MODULE_MOCK = re.compile(r"""\bjest\.(?:mock|doMock)\(\s*["'`]([^"'`]+)["'`]""")
_JAVA_MOCK = re.compile(r"\bmock\(\s*(\w+)\.class\s*\)|@Mock\s+(?:(?:private|final)\s+)*(\w+)\s+\w+")


def model_score(scores: dict[str, int]) -> int:
    return sum(int(scores[dimension]) for dimension in RUBRIC)


def target_symbol(target: str) -> str:
    """The symbol a case must exercise: "Class.method" -> "method", "Component" -> "Component"."""
    names = re.findall(r"[A-Za-z_]\w*", target or "")
    return names[-1] if names else ""


def _owners(target: str) -> set[str]:
    names = re.findall(r"[A-Za-z_]\w*", target or "")
    return set(names[:-1])


def mocks_target(target: str, content: str) -> bool:
    """True when the test replaces the unit under test itself (its symbol or owning module/class)."""
    symbol, owners = target_symbol(target), _owners(target)
    if not symbol:
        return False
    if any(match.group(1).split(".")[-1] == symbol for match in _PY_PATCH.finditer(content)):
        return True
    if any(match.group(1) == symbol for match in _JS_SPY.finditer(content)):
        return True
    lowered = {owner.lower() for owner in owners}
    for match in _JS_MODULE_MOCK.finditer(content):
        if re.split(r"[/\\]", match.group(1))[-1].split(".")[0].lower() in lowered:
            return True
    return any((match.group(1) or match.group(2)) in owners for match in _JAVA_MOCK.finditer(content))


def evidence_caps(*, target: str, content: str, verdict: str, source_found: bool) -> list[dict[str, Any]]:
    """Caps the host applies to one case. Target rules apply only when the case declares a target."""
    caps: list[dict[str, Any]] = []

    def add(rule: str, cap: int, reason: str) -> None:
        caps.append({"rule": rule, "cap": cap, "reason": reason})

    symbol = target_symbol(target)
    if symbol:
        if not source_found:
            add("target_source_missing", 50, f"No implementation source was found for target `{target}`.")
        if not re.search(rf"\b{re.escape(symbol)}\b", content):
            add("target_not_invoked", 40, f"Generated test never references the target symbol `{symbol}`.")
        if mocks_target(target, content):
            add("target_mocked", 40, f"Generated test replaces the unit under test `{symbol}` with a mock.")
    if not _ASSERTION.search(content):
        add("no_assertion", 30, "Generated test contains no assertion.")
    if verdict == "needs_revision":
        add("verdict_needs_revision", 49, "Reviewer verdict is needs_revision.")
    if verdict == "insufficient_evidence":
        add("verdict_insufficient_evidence", 59, "Reviewer verdict is insufficient_evidence.")
    return caps


def score_review(review: dict[str, Any], *, target: str, content: str, source_found: bool) -> dict[str, Any]:
    """The reviewed case with host-computed confidence; the reviewer's own confidence is kept as model_confidence."""
    scores = {dimension: int(review["scores"][dimension]) for dimension in RUBRIC}
    base = model_score(scores)
    caps = evidence_caps(target=target, content=content, verdict=review["verdict"], source_found=source_found)
    verdict = (
        "insufficient_evidence"
        if any(cap["rule"] == "target_source_missing" for cap in caps)
        else review["verdict"]
    )
    return {
        **review,
        "verdict": verdict,
        "model_confidence": review["confidence"],
        "model_score": base,
        "scores": scores,
        "caps": caps,
        "confidence": min([base, *(cap["cap"] for cap in caps)]),
    }


def review_accepted(review: dict[str, Any] | None, threshold: int) -> bool:
    return bool(review) and review["verdict"] == "supported" and review["confidence"] >= threshold


def weakest_dimensions(scores: dict[str, int], limit: int = 3) -> list[str]:
    """Dimensions below full marks, lowest share of their maximum first."""
    below = sorted(
        (scores[name] / maximum, name)
        for name, maximum in RUBRIC.items()
        if scores.get(name, maximum) < maximum
    )
    return [f"{name} {scores[name]}/{RUBRIC[name]}" for _, name in below[:limit]]


def verification_note(review: dict[str, Any], threshold: int) -> str:
    """One line for the repair feedback and the accuracy gate: confidence, weakest dimensions, caps, reviewer reason."""
    dimensions = weakest_dimensions(review["scores"]) if review.get("scores") else []
    caps = "; ".join(f"{cap['rule']} cap {cap['cap']}: {cap['reason']}" for cap in review.get("caps", []))
    return (
        f"confidence {review['confidence']} (threshold {threshold}, verdict {review['verdict']}); "
        f"weakest dimensions: {', '.join(dimensions) or 'none below full marks'}"
        + (f"; caps: {caps}" if caps else "")
        + f"; reviewer: {review['reason']}"
    )

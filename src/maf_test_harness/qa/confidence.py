"""Host-computed confidence for reviewed generated cases.

The reviewer model scores five rubric dimensions. The host sums them, then applies deterministic caps from the
generated file and the target's source. A cap cannot be raised by the model. The checks are conservative regular
expressions across Python, JavaScript/TypeScript and Java; they are best-effort, not parsers. A target that does not
name a confident code symbol (CLI commands, pages, flows, free text) gets no target caps at all.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUBRIC: dict[str, int] = {
    "source_and_api_match": 25,
    "assertion_checks_real_behavior": 30,
    "fixture_validity": 20,
    "isolation_and_determinism": 15,
    "traceability": 10,
}

_ASSERTION = re.compile(
    r"\bassert\b|\bassert[A-Z]\w*\s*\(|\bassert_\w+\s*\(|\bAssert\.|\b(?:and)?[Ee]xpect\s*\(|\.should\b|\bverify\s*\("
    r"|\.to(?:Equal|Be|Throw|Contain|Match)\w*\s*\(|pytest\.raises\(|\brequire\.\w+\(|\bt\.(?:Errorf|Fatalf|Fatal|Error)\("
    r"|@Test\([^)]*expected"
)
# Comments and string/template literals are blanked before code-level matching (newlines kept).
_MASK = re.compile(
    r"""(#[^\n]*|//[^\n]*|/\*.*?\*/|\"\"\".*?\"\"\"|'''.*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)""",
    re.S,
)
_PY_PATCH = re.compile(r"""\bpatch\(\s*(?:[^,()]+,\s*)?["']([\w.]+)["']""")
_PY_PATCH_OBJECT = re.compile(r"""\bpatch\.object\(\s*(\w+)\s*,\s*["'](\w+)["']""")
_JS_SPY = re.compile(r"""\bjest\.spyOn\(\s*\w+\s*,\s*["'`](\w+)["'`]""")
_JS_MODULE_MOCK = re.compile(r"""\bjest\.(?:mock|doMock)\(\s*["'`]([^"'`]+)["'`]""")
_JAVA_MOCK = re.compile(r"\bmock\(\s*(\w+)\.class\s*\)|@Mock\s+(?:(?:private|final)\s+)*(\w+)\s+\w+")

_ROUTE = re.compile(r"^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/\S*)")
_DESCRIPTORS = {
    "constructor",
    "property",
    "getter",
    "setter",
    "component",
    "hook",
    "page",
    "flow",
    "method",
    "function",
    "class",
    "endpoint",
    "route",
    "command",
    "screen",
}
_CONSTRUCTOR_NAMES = {"__init__", "constructor", "__construct"}
_PATH_TOKEN = re.compile(r"[/\\]|\.(?:ts|tsx|js|jsx|mjs|cjs|py|java|cs|go|rb|kt|rs)$", re.I)
_IDENT = re.compile(r"[A-Za-z_]\w*")
_CODE_NAME = re.compile(r"^(?:[A-Z][a-z0-9]+[A-Z]\w*|[a-z]+[A-Z]\w*|[A-Za-z]+_\w+)$")


@dataclass(frozen=True)
class CodeTarget:
    """A target that names one confident code symbol, or a route literal."""

    kind: str  # "symbol" | "constructor" | "route"
    symbol: str = ""
    owner: str = ""  # enclosing class (or module) name, when given
    module: str = ""  # dotted module path before the symbol, when given (Python)
    file_stem: str = ""  # source file stem, when the target names a file
    route: str = ""  # path literal; {param} and :param segments are wildcards


def resolve_target(target: str) -> CodeTarget | None:
    """Parse a case target. None means no confident symbol: target-based caps are skipped."""
    text = re.sub(r"\([^)]*\)", " ", target or "").strip()
    if not text:
        return None
    if route := _ROUTE.match(text):
        return CodeTarget("route", route=route.group(1))
    if text.startswith("CLI:") or re.search(r"(?<!\w)--\w", text):
        return None
    file_stem = ""
    words = []
    for word in text.split():
        if _PATH_TOKEN.search(word):
            file_stem = file_stem or Path(word.replace("\\", "/")).stem
        else:
            words.append(word)
    while words and words[-1].lower() in _DESCRIPTORS:
        descriptor = words.pop().lower()
        if descriptor == "constructor":
            owners = [w for w in words if _IDENT.fullmatch(w)]
            return (
                CodeTarget("constructor", symbol=owners[0], owner=owners[0], file_stem=file_stem)
                if owners
                else None
            )
    for word in words:
        if any(sep in word for sep in (".", "#", "::")):
            parts = _IDENT.findall(word.replace("::", ".").replace("#", "."))
            if len(parts) < 2:
                continue
            symbol, owner = parts[-1], parts[-2]
            module = ".".join(parts[:-1])
            if symbol in _CONSTRUCTOR_NAMES:
                return CodeTarget(
                    "constructor", symbol=owner, owner=owner, module=module, file_stem=file_stem
                )
            return CodeTarget("symbol", symbol=symbol, owner=owner, module=module, file_stem=file_stem)
    for word in words:
        if _CODE_NAME.fullmatch(word):
            return CodeTarget("symbol", symbol=word, file_stem=file_stem)
    return None


def target_symbol(target: str) -> str:
    """The confident symbol of a target, or "" when it names none (see resolve_target)."""
    resolved = resolve_target(target)
    return resolved.symbol if resolved and resolved.kind != "route" else ""


def mask_code(content: str) -> str:
    """Comments and string/template contents blanked (newlines kept), so only code can match."""
    return _MASK.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), content)


_LINE_PREFIX = re.compile(r"^\d+: ", re.M)


def _normal(text: str) -> str:
    """Whitespace-collapsed text with target line-number prefixes removed, for verbatim quote checks."""
    return " ".join(_LINE_PREFIX.sub("", text or "").split())


def _normal_value(value: str) -> str:
    return (value or "").strip().strip("'\"").strip()


def _quoted(quote: str, normal_text: str) -> bool:
    """A non-empty quote that is a whitespace-normalised substring of the (normalised) text."""
    return bool(_normal(quote)) and _normal(quote) in normal_text


def derivation_findings(
    *,
    slots: list[dict[str, Any]],
    derivations: list[dict[str, Any]],
    target_source: str,
    test_text: str,
    require_source: bool = True,
) -> list[dict[str, Any]]:
    """Caps for the blind derivations of one case (slots from qa/derivation.mask_expected_literals).

    Every slot must be answered (60), each quote must be verbatim in the target source or the test (50), and each value
    must equal the real literal (40, verdict needs_revision). A case with no slots has nothing to derive. A case without
    a confident code target has no implementation to quote, so only its fixture quotes are checked (require_source=False).
    """
    if not slots:
        return []
    answers = {d["slot"]: d for d in derivations}
    missing = [s["slot"] for s in slots if s["slot"] not in answers]
    findings: list[dict[str, Any]] = []
    if missing:
        findings.append(
            {
                "rule": "derivation_missing",
                "cap": 60,
                "reason": f"The reviewer did not derive {', '.join(missing)}; every assertion literal needs a derivation.",
            }
        )
    source, fixture = _normal(target_source), _normal(test_text)
    unverifiable = [
        slot
        for slot in slots
        if slot["slot"] in answers
        and not (
            (not require_source or _quoted(answers[slot["slot"]]["source_quote"], source))
            and _quoted(answers[slot["slot"]]["fixture_quote"], fixture)
        )
    ]
    if unverifiable:
        findings.append(
            {
                "rule": "derivation_unverifiable",
                "cap": 50,
                "reason": "Quoted source or fixture lines are not verbatim in the target source or the test: "
                + ", ".join(slot["slot"] for slot in unverifiable)
                + ".",
            }
        )
    mismatched = [
        slot
        for slot in slots
        if slot["slot"] in answers
        and _normal_value(answers[slot["slot"]]["value"]) != _normal_value(slot["value"])
    ]
    if mismatched:
        findings.append(
            {
                "rule": "blind_derivation_mismatch",
                "cap": 40,
                "reason": "The reviewer's derived value differs from the literal the test asserts: "
                + "; ".join(
                    f"{slot['slot']} derived {answers[slot['slot']]['value']!r}, asserted {slot['value']!r}"
                    for slot in mismatched
                )
                + ".",
            }
        )
    return findings


def model_score(scores: dict[str, int]) -> int:
    return sum(int(scores[dimension]) for dimension in RUBRIC)


def _route_pattern(route: str) -> re.Pattern[str]:
    parts = re.split(r"\{[^}]*\}|:\w+", route)
    params = re.findall(r"\{[^}]*\}|:\w+", route)
    pattern = ""
    for index, part in enumerate(parts):
        pattern += re.escape(part)
        if index < len(params):
            pattern += r"[^/\s'\"`?#]+"
    return re.compile(pattern)


def _names(resolved: CodeTarget) -> set[str]:
    return {name for name in (resolved.module, resolved.owner) if name}


def _modules_match(module: str, resolved: CodeTarget) -> bool:
    candidates = _names(resolved)
    if not module:
        return not candidates
    if not candidates:
        return True
    return any(module == c or module.endswith("." + c) or c.endswith("." + module) for c in candidates)


def mocks_target(target: str | CodeTarget, content: str) -> bool:
    """True when the test replaces the unit under test itself: its symbol, through its own module or class."""
    resolved = target if isinstance(target, CodeTarget) else resolve_target(target)
    if resolved is None or resolved.kind == "route":
        return False
    symbol = resolved.symbol
    for match in _PY_PATCH.finditer(content):
        module, _, attr = match.group(1).rpartition(".")
        if attr == symbol and _modules_match(module, resolved):
            return True
    for match in _PY_PATCH_OBJECT.finditer(content):
        if match.group(2) == symbol and match.group(1) in _names(resolved):
            return True
    if any(match.group(1) == symbol for match in _JS_SPY.finditer(content)):
        return True
    stems = (
        {resolved.file_stem.lower()}
        if resolved.file_stem
        else {resolved.owner.lower()}
        if resolved.owner
        else set()
    )
    for match in _JS_MODULE_MOCK.finditer(content):
        if Path(match.group(1).replace("\\", "/")).name.split(".")[0].lower() in stems - {""}:
            return True
    owners = {resolved.owner} if resolved.owner else set()
    return any((match.group(1) or match.group(2)) in owners for match in _JAVA_MOCK.finditer(content))


def evidence_caps(*, target: str, content: str, verdict: str, source_found: bool) -> list[dict[str, Any]]:
    """Caps the host applies to one case. Target rules apply only when the target names a confident symbol or route."""
    caps: list[dict[str, Any]] = []

    def add(rule: str, cap: int, reason: str) -> None:
        caps.append({"rule": rule, "cap": cap, "reason": reason})

    resolved = resolve_target(target)
    code = mask_code(content)
    if resolved is not None:
        if resolved.kind == "route":
            invoked = _route_pattern(resolved.route).search(content) is not None
            subject = f"route `{resolved.route}`"
        else:
            name = resolved.symbol
            invoked = re.search(rf"\b{re.escape(name)}\b", code) is not None
            subject = f"symbol `{name}`"
            if not source_found:
                add("target_source_missing", 50, f"No implementation source was found for target `{target}`.")
            if mocks_target(resolved, content):
                add(
                    "target_mocked", 40, f"Generated test replaces the unit under test {subject} with a mock."
                )
        if not invoked:
            add("target_not_invoked", 40, f"Generated test never exercises the target {subject}.")
    if not _ASSERTION.search(code):
        add("no_assertion", 30, "Generated test contains no assertion.")
    if verdict == "needs_revision":
        add("verdict_needs_revision", 49, "Reviewer verdict is needs_revision.")
    if verdict == "insufficient_evidence":
        add("verdict_insufficient_evidence", 59, "Reviewer verdict is insufficient_evidence.")
    return caps


def score_review(
    review: dict[str, Any],
    *,
    target: str,
    content: str,
    source_found: bool,
    findings: list[dict[str, Any]] | tuple[()] = (),
) -> dict[str, Any]:
    """The reviewed case with host-computed confidence; the reviewer's own confidence is kept as model_confidence.

    `findings` are the deterministic derivation checks (qa/derivation.py): a finding with a numeric cap joins the caps,
    a finding with cap None is a note (derivation_required) that caps nothing.
    """
    scores = {dimension: int(review["scores"][dimension]) for dimension in RUBRIC}
    base = model_score(scores)
    caps = evidence_caps(target=target, content=content, verdict=review["verdict"], source_found=source_found)
    caps += [dict(f) for f in findings if f.get("cap") is not None]
    notes = [dict(f) for f in findings if f.get("cap") is None]
    rules = {cap["rule"] for cap in caps}
    if "target_source_missing" in rules:
        verdict = "insufficient_evidence"
    elif rules & {"expectation_contradicts_source", "blind_derivation_mismatch"}:
        verdict = "needs_revision"
    else:
        verdict = review["verdict"]
    return {
        **review,
        "verdict": verdict,
        "model_confidence": review["confidence"],
        "model_score": base,
        "scores": scores,
        "caps": caps,
        "notes": notes,
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

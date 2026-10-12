"""Host-computed confidence for reviewed generated cases.

The reviewer model scores five rubric dimensions. The host sums them, then applies deterministic caps from the
generated file and the target's source. A cap cannot be raised by the model. The checks are conservative regular
expressions across Python, JavaScript/TypeScript and Java; they are best-effort, not parsers. A target that does not
name a confident code symbol (CLI commands, pages, flows, free text) gets no target caps at all.
"""

import ast
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

# Rubrics per review kind (points sum to 100). The test rubric is RUBRIC above. A reviewer scores its dimensions; the
# host sums them, then applies host caps (rules below) and the verdict cap. A reviewer's own confidence is never used.
PLAN_RUBRIC: dict[str, int] = {"controls_covered": 40, "level_fit": 30, "scenarios_derivable": 30}
DESIGN_RUBRIC: dict[str, int] = {
    "source_proven": 30,
    "correct_target": 25,
    "expected_derivable": 25,
    "fixture_validity": 20,
}
RUBRICS: dict[str, dict[str, int]] = {"plan": PLAN_RUBRIC, "design": DESIGN_RUBRIC, "test": RUBRIC}
VERDICT_CAPS: dict[str, int] = {"revise": 79, "reject": 0}


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


def confident_target(target: str) -> bool:
    """True when the target names one confident code symbol (test review and design review cap only these)."""
    resolved = resolve_target(target)
    return resolved is not None and resolved.kind != "route"


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


_QUOTE_PREFIX = re.compile(r"(?:^|(?<=\s))\d+: ", re.M)
MIN_QUOTE_CHARS = 12  # non-space characters a verified quote needs, unless it is an exact full source line


def _normal(text: str) -> str:
    """Whitespace-collapsed text with line-number prefixes removed wherever they appear (quotes may span lines)."""
    return " ".join(_QUOTE_PREFIX.sub(" ", text or "").split())


def _quoted(quote: str, *texts: str) -> bool:
    """A verified quote: a whitespace-normalised substring of one of the texts with at least MIN_QUOTE_CHARS
    non-space characters, or exactly one full line of a text (so a trivial token such as "a", "0" or "if" never verifies)."""
    normal = _normal(quote)
    if not normal:
        return False
    substring_ok = len(re.sub(r"\s", "", normal)) >= MIN_QUOTE_CHARS
    for text in texts:
        if substring_ok and normal in _normal(text):
            return True
        if any(_normal(line) == normal for line in (text or "").splitlines()):
            return True
    return False


def literal_matches(value: str, literal: Any) -> bool:
    """Whether a reviewer's value text denotes the asserted literal. Numbers compare by value; strings are unescaped."""
    text = (value or "").strip()
    try:
        parsed: Any = ast.literal_eval(text)
    except Exception:  # not a Python literal: compare the text with surrounding quotes removed
        parsed = text.strip("'\"")
    if isinstance(literal, bool) or literal is None:
        return str(parsed) == str(literal)
    if isinstance(literal, int | float):
        if isinstance(parsed, bool) or not isinstance(parsed, int | float):
            return False
        return float(parsed) == float(literal)
    return isinstance(parsed, str) and parsed == literal


def derivation_status(
    *,
    slots: list[dict[str, Any]],
    derivations: list[dict[str, Any]],
    target_source: str,
    test_text: str,
    masked_text: str = "",
    require_source: bool = True,
) -> dict[str, list[str]]:
    """Each slot of one case sorted into missing, unverifiable (quotes not verbatim), mismatch (verified quotes, wrong
    value) or verified. A quote may come from the target source, and the fixture quote from the masked or unmasked test."""
    answers = {d["slot"]: d for d in derivations}
    status: dict[str, list[str]] = {"missing": [], "unverifiable": [], "mismatch": [], "verified": []}
    for slot in slots:
        answer = answers.get(slot["slot"])
        if answer is None:
            status["missing"].append(slot["slot"])
            continue
        quotes_ok = (not require_source or _quoted(answer["source_quote"], target_source)) and _quoted(
            answer["fixture_quote"], test_text, masked_text
        )
        if not quotes_ok:
            status["unverifiable"].append(slot["slot"])
        elif not literal_matches(answer["value"], slot["literal"]):
            status["mismatch"].append(slot["slot"])
        else:
            status["verified"].append(slot["slot"])
    return status


def derivation_problems(**kwargs: Any) -> list[str]:
    """Format problems the reviewer can fix by answering again: missing or unverifiable derivations."""
    status = derivation_status(**kwargs)
    problems = []
    if status["missing"]:
        problems.append(f"no derivation for {', '.join(status['missing'])}")
    if status["unverifiable"]:
        problems.append(
            f"quotes are not verbatim in target_source or the test for {', '.join(status['unverifiable'])}"
        )
    return problems


def derivation_findings(
    *,
    slots: list[dict[str, Any]],
    derivations: list[dict[str, Any]],
    target_source: str,
    test_text: str,
    masked_text: str = "",
    require_source: bool = True,
) -> list[dict[str, Any]]:
    """Caps for the final blind derivations of one case, after the reviewer's repair chances are spent.

    Missing derivations cap at 60, unverifiable quotes at 50, and a verified derivation whose value differs from the
    asserted literal at 40 with verdict needs_revision (callers re-ask once before a mismatch is final).
    """
    if not slots:
        return []
    status = derivation_status(
        slots=slots,
        derivations=derivations,
        target_source=target_source,
        test_text=test_text,
        masked_text=masked_text,
        require_source=require_source,
    )
    findings: list[dict[str, Any]] = []
    if status["missing"]:
        findings.append(
            {
                "rule": "derivation_missing",
                "cap": 60,
                "reason": f"The reviewer did not derive {', '.join(status['missing'])}; every assertion literal needs a derivation.",
            }
        )
    if status["unverifiable"]:
        findings.append(
            {
                "rule": "derivation_unverifiable",
                "cap": 50,
                "reason": "Quoted source or fixture lines are not verbatim in the target source or the test: "
                + ", ".join(status["unverifiable"])
                + ".",
            }
        )
    if status["mismatch"]:
        by_slot = {s["slot"]: s for s in slots}
        answers = {d["slot"]: d for d in derivations}
        findings.append(
            {
                "rule": "blind_derivation_mismatch",
                "cap": 40,
                "reason": "The reviewer's derived value differs from the literal the test asserts, and the re-check "
                "confirmed it: "
                + "; ".join(
                    f"{slot} derived {answers[slot]['value']!r}, asserted {by_slot[slot]['value']!r}"
                    for slot in status["mismatch"]
                )
                + ".",
            }
        )
    return findings


def _lifts_filter(
    finding: dict[str, Any], slots: list[dict[str, Any]], derivations: list[dict[str, Any]], status
) -> bool:
    """D1 is lifted when a verified derivation of the asserted count cites the filter: a filter token in its steps or
    source quote, or the filter line quoted verbatim."""
    answers = {d["slot"]: d for d in derivations}
    tokens = set(finding.get("tokens", []))
    for slot in slots:
        if slot["slot"] not in status["verified"] or slot["literal"] != finding.get("expected"):
            continue
        answer = answers[slot["slot"]]
        words = set(re.findall(r"[A-Za-z_]\w*", f"{answer['steps']} {answer['source_quote']}"))
        if tokens & words:
            return True
        if any(
            _normal(line) and _normal(line) in _normal(answer["source_quote"])
            for line in finding.get("filter_lines", [])
        ):
            return True
    return False


def settle_deterministic(
    findings: list[dict[str, Any]],
    *,
    slots: list[dict[str, Any]],
    derivations: list[dict[str, Any]],
    target_source: str,
    test_text: str,
    masked_text: str = "",
    require_source: bool = True,
) -> list[dict[str, Any]]:
    """Apply the evidence rules to the deterministic findings of one case.

    D1 (cap 60) is lifted to a note when the reviewer's verified derivation of that count cites the filter. D2 (cap 0)
    becomes a conflict cap of 60 when the verified blind derivation agrees with the literal (a human should look), and
    stays 0 when the blind derivation disagrees or is missing. Notes (cap None) pass through unchanged.
    """
    status = derivation_status(
        slots=slots,
        derivations=derivations,
        target_source=target_source,
        test_text=test_text,
        masked_text=masked_text,
        require_source=require_source,
    )
    settled: list[dict[str, Any]] = []
    for finding in findings:
        rule = finding.get("rule")
        if rule == "count_assumes_no_filter" and finding.get("cap") == 60:
            if _lifts_filter(finding, slots, derivations, status):
                settled.append(
                    {
                        "rule": rule,
                        "cap": None,
                        "reason": "Note: the reviewer's verified derivation cites the filter, so the count is not capped.",
                    }
                )
                continue
        elif (
            rule == "expectation_contradicts_source"
            and finding.get("cap") == 0
            and any(
                slot["slot"] in status["verified"] and slot["literal"] == finding.get("expected")
                for slot in slots
            )
        ):
            settled.append(
                {
                    "rule": "expectation_conflict",
                    "cap": 60,
                    "reason": f"The source computes `{finding.get('key')}` = {finding.get('value')}, the test expects "
                    f"{finding.get('expected')}, and the blind derivation agrees with the test. A human should check.",
                }
            )
            continue
        settled.append(finding)
    return settled


class ConfidenceReviewerUnavailable(RuntimeError):
    """A confidence reviewer returned no usable review (gateway unreachable, or its own contract repair exhausted).
    This is an outage, not a low score: the stage stops at once and the author's repair attempts are not spent."""


def reviewer_unavailable(stage: str, detail: str) -> ConfidenceReviewerUnavailable:
    return ConfidenceReviewerUnavailable(f"Confidence reviewer unavailable: {stage} {detail}".strip())


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


def quote_verified(quote: str, text: str) -> bool:
    """A non-empty quote that is a whitespace-normalised substring of ``text`` (the same rule the test review uses)."""
    return _quoted(quote, text)


def rubric_score(
    kind: str, scores: dict[str, Any], caps: list[dict[str, Any]], verdict: str
) -> dict[str, Any]:
    """Host confidence of one reviewed plan, design or test artifact.

    ``scores`` are the reviewer's dimension points (each clamped to its maximum). ``caps`` are the host's findings: a
    finding with a numeric cap joins the caps, a finding with cap None is a note. The verdict adds its own cap
    (revise 79, reject 0). Confidence is the points sum capped by the lowest cap.
    """
    rubric = RUBRICS[kind]
    clean = {dimension: min(int(scores[dimension]), maximum) for dimension, maximum in rubric.items()}
    points = sum(clean.values())
    applied = [dict(c) for c in caps if c.get("cap") is not None]
    notes = [dict(c) for c in caps if c.get("cap") is None]
    if verdict in VERDICT_CAPS:
        applied.append(
            {
                "rule": f"verdict_{verdict}",
                "cap": VERDICT_CAPS[verdict],
                "reason": f"Reviewer verdict is {verdict}.",
            }
        )
    return {
        "scores": clean,
        "points": points,
        "caps": applied,
        "notes": notes,
        "confidence": min([points, *(c["cap"] for c in applied)]),
    }

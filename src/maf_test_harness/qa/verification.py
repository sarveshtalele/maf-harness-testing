"""Verification triage (accuracy 7.4): sort each failing executed case by evidence, never by a model call.

Policy: a failure is TEST_DEFECT (and so regenerated) only with positive evidence. When unsure, it is UNRESOLVED.

Classes:
- ``product_defect``: the oracle is ``specified`` or ``golden``, the failure is an assertion pair, and the documented
  quote states one of its two values (either side, normalised and boundary-matched). The test is kept.
- ``test_defect``: (a) the generated test cannot collect or compile because it imports a module the repository does not
  contain (not declared, not stdlib), or uses a keyword error during collection or compilation; or (b) the oracle is
  ``derived`` (no documented value exists) and the quote states neither value of an assertion that parses.
- ``environment``: a missing declared third-party package, or the existing error taxonomy names an environment cause.
- ``unresolved``: everything else, including multi-line diffs, operators other than ``==``, unparsed values, empty
  messages, and metamorphic, property, characterization or missing oracles.

``verified`` is separate: review accepted AND execution passed, and never characterization (see ``counts_as_verified``).
"""

from __future__ import annotations

import ast
import re
import sys
from typing import Any

from .. import errors
from . import triage as triage_rules

PRODUCT_DEFECT = "product_defect"
TEST_DEFECT = "test_defect"
ENVIRONMENT = "environment"
UNRESOLVED = "unresolved"
CLASSES = (PRODUCT_DEFECT, TEST_DEFECT, ENVIRONMENT, UNRESOLVED)

DOCUMENTED_ORACLES = frozenset({"specified", "golden"})
# Only a derived expectation has positive provenance for being a generator guess; the others stay unresolved.
GUESSABLE_ORACLES = frozenset({"derived"})
# Where the documented quote may sit on a design case; the first non-empty value wins.
QUOTE_KEYS = ("oracle_quote", "expectation_quote", "documented_quote")
_ENVIRONMENT_CATEGORIES = frozenset({"environment", "infra"})

_DECIMAL = re.compile(r"Decimal\(\s*(['\"])(.*?)\1\s*\)")
_JS_LITERALS = {"true": "True", "false": "False", "null": "None", "undefined": "None"}
_PYTEST_E_ASSERT = re.compile(r"^E\s+assert\s+(?P<left>.+?)\s+==\s+(?P<right>.+?)\s*$", re.M)
_PYTEST_ASSERT = re.compile(r"\bassert\s+(?P<left>.+?)\s+==\s+(?P<right>.+?)\s*$", re.M)
_JUNIT = re.compile(r"expected:<(?P<expected>.*)>\s+but was:<(?P<actual>.*)>")
_JEST_EXPECTED = re.compile(r"^\s*Expected:\s*(?P<value>.+?)\s*$", re.M)
_JEST_RECEIVED = re.compile(r"^\s*Received:\s*(?P<value>.+?)\s*$", re.M)
_UNPARSED_OPERATOR = re.compile(r"!=|\bis not\b|\bis\b|\bin\b|<|>|approx", re.I)
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w]|\.\d)")
_MODULE_MISSING = re.compile(r"No module named ['\"]?(?P<module>[\w.]+)")
_NAME_IMPORT = re.compile(r"cannot import name ['\"]?\w+['\"]? from ['\"]?(?P<module>[\w.]+)")
_COLLECTION_PHASE = re.compile(
    r"while importing|error collecting|collection failure|ERROR collecting|errors? during collection", re.I
)


# ---------------------------------------------------------------- oracle fields (read defensively)


def oracle_kind(case: dict[str, Any]) -> str:
    """The design case's oracle kind; empty when the case predates oracles.

    ``oracle`` is either the kind string or a dict ``{kind, source, relation, verified}``."""
    raw = case.get("oracle")
    if isinstance(raw, dict):
        raw = raw.get("kind")
    return str(raw or "").strip().lower()


def oracle_verified(case: dict[str, Any]) -> bool:
    raw = case.get("oracle")
    return isinstance(raw, dict) and bool(raw.get("verified"))


def is_characterization(case: dict[str, Any]) -> bool:
    return oracle_kind(case) == "characterization"


def counts_as_verified(case: dict[str, Any]) -> bool:
    """Verified oracle, never characterization (current behaviour is not verified)."""
    return oracle_verified(case) and not is_characterization(case)


def documented_quote(case: dict[str, Any]) -> str:
    raw = case.get("oracle")
    if isinstance(raw, dict) and raw.get("quote"):
        return " ".join(str(raw["quote"]).split())
    for key in QUOTE_KEYS:
        value = " ".join(str(case.get(key) or "").split())
        if value:
            return value
    return ""


def is_verified(source_review: str, execution_status: str) -> bool:
    """Verified means both: the source review accepted the case AND it passed execution."""
    return source_review == "accepted" and execution_status == "passed"


# ---------------------------------------------------------------- value parsing and comparison


class Unparsed(ValueError):
    """A reported value that is not a Python or JS literal: the case is UNRESOLVED, never guessed."""


def parse_value(text: str) -> Any:
    """A value from a reported repr. ``Decimal('x')`` becomes ``x``; JS literals map to Python ones."""
    source = _DECIMAL.sub(lambda m: m.group(2), str(text).strip())
    source = re.sub(r"\b(true|false|null|undefined)\b", lambda m: _JS_LITERALS[m.group(1)], source)
    try:
        return ast.literal_eval(source)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError) as exc:
        raise Unparsed(f"not a literal: {text[:80]}") from exc


def _junit_value(raw: str) -> Any:
    """JUnit prints values unquoted and marks the differing part with one [ ] pair (``[a]bc``). A literal is taken as
    written (lists stay lists); otherwise the text is a string with the single diff marker removed."""
    try:
        return parse_value(raw)
    except Unparsed:
        if raw.count("[") == 1 and raw.count("]") == 1:
            return re.sub(r"\[([^\[\]]*)\]", r"\1", raw)
        return raw


def same(a: Any, b: Any) -> bool:
    """Structural equality. Booleans only equal booleans (True is not 1); numbers compare by value."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple, dict)) or isinstance(b, (list, tuple, dict)):
        return False
    if isinstance(a, str) != isinstance(b, str):
        return False
    return a == b


def _boundary(token: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(token)}(?!\w)", re.IGNORECASE)


def _short(value: Any) -> bool:
    return value is None or isinstance(value, bool) or value == "" or len(str(value)) <= 2


def quote_states(quote: str, value: Any) -> bool:
    """Whether the documented quote states ``value``: token or number boundaries, never a bare substring.

    Short values (2 characters or fewer), None, booleans and empty strings need an exact token match."""
    if not quote:
        return False
    text = " ".join(str(quote).split())
    if value is None:
        return bool(re.search(r"(?<!\w)(None|null|undefined)(?!\w)", text))
    if isinstance(value, bool):
        word = "True" if value else "False"
        return bool(_boundary(word).search(text) or _boundary(word.lower()).search(text))
    if value == "":
        return False
    if isinstance(value, (int, float)):
        if _short(value):
            return bool(_boundary(str(value)).search(text))
        return any(float(m.group(0)) == float(value) for m in _NUMBER.finditer(text))
    if isinstance(value, str):
        return bool(_boundary(value).search(text))
    compact = re.sub(r"\s+", "", repr(value))
    return bool(compact and compact in re.sub(r"\s+", "", text))


def documented_matches(quote: str, *values: Any) -> list[str]:
    """The reprs of the given values that the quote states (empty when none is stated)."""
    return [repr(value) for value in values if quote_states(quote, value)]


# ---------------------------------------------------------------- assertion extraction


def _diff_block(text: str) -> bool:
    # pytest's "+  where x = f()" explanations are not diffs; real diff lines are "E   - x" or "E   + y".
    return bool(re.search(r"^E\s+[-+](?!\s*where\b)\s", text, re.M)) or "Full diff" in text


def _unparsed_operator(assert_line: str) -> bool:
    return bool(_UNPARSED_OPERATOR.search(assert_line))


def assertion_pair(message: str) -> tuple[str, str, str] | None:
    """(actual_text, expected_text, form) for one assertion, or None when the message has none.

    pytest: the ``E   assert X == Y`` line is preferred over the source header. JUnit: ``expected:<..> but was:<..>``.
    Jest: the ``Expected:`` and ``Received:`` lines. Operand order is not assumed: the caller checks both sides.
    Raises Unparsed for an assertion we cannot read (other operators, diff blocks)."""
    text = message or ""
    junit = _JUNIT.search(text)
    if junit:
        return junit.group("actual"), junit.group("expected"), "junit"
    expected_line = _JEST_EXPECTED.search(text)
    received_line = _JEST_RECEIVED.search(text)
    if expected_line and received_line:
        return received_line.group("value"), expected_line.group("value"), "jest"
    found = _PYTEST_E_ASSERT.search(text) or _PYTEST_ASSERT.search(text)
    if not found:
        return None
    if _diff_block(text):
        raise Unparsed("multi-line diff")
    if _unparsed_operator(found.group(0)):
        raise Unparsed("assertion operator other than ==")
    return found.group("left"), found.group("right"), "pytest"


# ---------------------------------------------------------------- import and collection evidence


def _top(module: str) -> str:
    return module.split(".", 1)[0]


def repo_module_names(source_files: list[str]) -> set[str]:
    """Dotted module names the repository contains (each package and file, and its top-level name)."""
    names: set[str] = set()
    for path in source_files:
        if not path.endswith(".py"):
            continue
        parts = path[:-3].replace("\\", "/").split("/")
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        for index in range(1, len(parts) + 1):
            names.add(".".join(parts[:index]).lower())
    return names


def _import_failure(text: str, context: dict[str, Any]) -> dict[str, Any] | None:
    """Module-level evidence for an import error, or None when the message is not an import error."""
    missing = _MODULE_MISSING.search(text)
    name_error = _NAME_IMPORT.search(text)
    if not missing and not name_error:
        return None
    module = (missing or name_error).group("module").lower()
    top = _top(module)
    declared = {str(d).lower().replace("-", "_") for d in context.get("declared") or []}
    repo = context.get("modules") or set()
    if top in {m.lower().replace("-", "_") for m in declared} or top.replace("_", "-") in declared:
        return {"classification": ENVIRONMENT, "reason": f"declared package {top} is not installed"}
    if top in sys.stdlib_module_names:
        return {"classification": UNRESOLVED, "reason": f"standard-library module {top} failed to import"}
    if name_error:
        if module in repo or top in repo:
            return {
                "classification": UNRESOLVED,
                "reason": f"{module} exists in the repository; the missing name was not checked against it",
            }
        return {"classification": UNRESOLVED, "reason": f"module {module} could not be resolved"}
    if module in repo or top in repo:
        return {
            "classification": UNRESOLVED,
            "reason": f"{module} exists in the repository but failed to import",
        }
    if not repo:
        return {
            "classification": UNRESOLVED,
            "reason": "no repository module list to prove the module is missing",
        }
    return {
        "classification": TEST_DEFECT,
        "reason": f"the generated test imports {module}, which the repository does not contain",
    }


# ---------------------------------------------------------------- classification


def classify_failure(
    message: str,
    *,
    oracle: str,
    quote: str,
    ecosystem: str | None = None,
    status: str = "failed",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic triage of one failure. Returns the class, the reason and the evidence used."""
    context = context or {}
    text = message or ""
    base: dict[str, Any] = {
        "oracle": oracle or None,
        "expected": None,
        "actual": None,
        "documented_quote": quote or None,
        "documented_matches": [],
        "diagnoses": [],
    }
    if not text.strip():
        return {**base, "classification": UNRESOLVED, "reason": "the failure has no message to triage"}
    diagnoses = errors.classify(text, ecosystem or None)
    codes = [d.code for d in diagnoses]
    base["diagnoses"] = codes[:3]

    import_evidence = _import_failure(text, context)
    if import_evidence is not None:
        return {**base, **import_evidence}
    if any(d.category in _ENVIRONMENT_CATEGORIES for d in diagnoses):
        return {**base, "classification": ENVIRONMENT, "reason": f"environment or setup failure ({codes[0]})"}

    try:
        pair = assertion_pair(text)
    except Unparsed as exc:
        return {**base, "classification": UNRESOLVED, "reason": f"assertion not parsed: {exc}"}
    if pair is None:
        # Keyword rules apply only to collection, compile or import failures, never to a message with an assertion.
        if (status == "error" or _COLLECTION_PHASE.search(text)) and triage_rules.classify(
            text
        ) == "test_defect":
            return {
                **base,
                "classification": TEST_DEFECT,
                "reason": "the generated test cannot collect or compile (import, name, syntax or compile error)",
            }
        return {
            **base,
            "classification": UNRESOLVED,
            "reason": "the failure is not an assertion with two values",
        }

    actual_text, expected_text, form = pair
    try:
        if form == "junit":
            actual, expected = _junit_value(actual_text), _junit_value(expected_text)
        else:
            actual = parse_value(actual_text)
            expected = parse_value(expected_text)
    except Unparsed as exc:
        return {**base, "classification": UNRESOLVED, "reason": f"values not parsed: {exc}"}
    base.update(actual=actual_text.strip(), expected=expected_text.strip())
    if same(actual, expected):
        return {**base, "classification": UNRESOLVED, "reason": "both values are equal after normalisation"}

    matches = documented_matches(quote, actual, expected)
    base["documented_matches"] = matches
    kind = oracle_kind({"oracle": oracle})
    if kind in DOCUMENTED_ORACLES:
        if matches:
            return {
                **base,
                "classification": PRODUCT_DEFECT,
                "reason": f"the documented quote states {', '.join(matches)}; the test asserts a different value ({kind})",
            }
        return {
            **base,
            "classification": UNRESOLVED,
            "reason": f"the documented quote states neither {expected_text.strip()} nor {actual_text.strip()}",
        }
    if kind in GUESSABLE_ORACLES:
        if matches:
            return {
                **base,
                "classification": UNRESOLVED,
                "reason": f"a derived expectation conflicts with the documented quote ({', '.join(matches)})",
            }
        return {
            **base,
            "classification": TEST_DEFECT,
            "reason": f"oracle derived: no document states {expected_text.strip()} or {actual_text.strip()}",
        }
    label = kind or "missing"
    return {
        **base,
        "classification": UNRESOLVED,
        "reason": f"oracle {label} is not decided by the deterministic rules",
    }


def triage_cases(
    cases: list[dict[str, Any]], ecosystem: str | None = None, context: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """One record per executed failure (failed, error) and per not-executed case with a message.

    ``cases`` are the report rows, each with ``id``, ``status``, ``message``, ``oracle`` and ``oracle_quote``."""
    records = []
    for case in cases:
        status = case.get("status")
        if status in {"failed", "error"} or (status == "not_executed" and case.get("message")):
            result = classify_failure(
                str(case.get("message") or ""),
                oracle=oracle_kind(case),
                quote=str(case.get("oracle_quote") or ""),
                ecosystem=ecosystem,
                # A not-executed case was quarantined or never collected: a collection-phase failure.
                status="error" if status == "not_executed" else str(status),
                context=context,
            )
            records.append({"case_id": case["id"], "status": status, **result})
        elif status in {"failed", "error"}:
            records.append(
                {
                    "case_id": case["id"],
                    "status": status,
                    **classify_failure("", oracle="", quote="", status=str(status)),
                }
            )
    return records


def summarize_triage(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(CLASSES, 0)
    for record in records:
        counts[record["classification"]] = counts.get(record["classification"], 0) + 1
    return counts


def heal_targets(records: list[dict[str, Any]]) -> set[str]:
    """Case IDs the repair loop may regenerate: test defects only."""
    return {r["case_id"] for r in records if r["classification"] == TEST_DEFECT}


# ---------------------------------------------------------------- heal guard


def _assertion_lines(text: str) -> str:
    return "\n".join(
        line
        for line in text.splitlines()
        if re.search(r"\bassert|expect\(|==|toBe|toEqual|assertEqual", line)
    )


def heal_guard_violations(
    previous: dict[str, str], new: dict[str, str], guards: dict[str, dict[str, Any]]
) -> list[str]:
    """Documented literals that an assertion in the previous file stated and the healed file no longer states.

    ``guards`` maps a case ID to ``{"files": [...], "documented": [...]}``. A violation rejects the healed file."""
    violations = []
    for case_id, guard in guards.items():
        documented = [str(v) for v in guard.get("documented") or [] if str(v)]
        if not documented:
            continue
        old_text = _assertion_lines("\n".join(previous.get(path, "") for path in guard.get("files") or []))
        new_text = _assertion_lines("\n".join(new.get(path, "") for path in guard.get("files") or []))
        for literal in documented:
            if _boundary(literal).search(old_text) and not _boundary(literal).search(new_text):
                violations.append(f"{case_id}: documented value {literal} was changed")
    return violations

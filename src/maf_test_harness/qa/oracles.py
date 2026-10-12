"""Pure helpers for requirement tiers, verbatim evidence quotes and test oracles (no model calls).

Tiers: ``product`` requirements are what the repository promises its users (README, SKILL.md, docs, references, CLI
help); ``component`` requirements are callable-level declarations. Runs written before tiers existed read as component.

Oracles: every design case states where its expected value comes from.

- ``specified``: a verbatim quote from a repository file or a user-supplied greenfield document (never model text).
- ``derived``: a verbatim quote from the target's implementation in the repository (unavailable in greenfield runs).
- ``golden``: a verified rule quote (repository or supplied document) and a scenario fixture description.
- ``metamorphic`` / ``property``: a stated relation or property that names the target. Stated, not verified.
- ``characterization``: current behaviour only. Never verified.

``counts_as_verified`` is the single definition used by the design reviewer and the report.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

ORACLE_KINDS = ("specified", "derived", "metamorphic", "golden", "property", "characterization")
TIERS = ("product", "component")
QUOTE_KINDS = ("specified", "derived", "golden")  # verified by a quote check
RELATION_KINDS = ("metamorphic", "golden", "property")  # need a relation of their own
STATED_KINDS = ("metamorphic", "property")  # counted through their stated relation and a test
MIN_QUOTE_CHARS = 8  # non-space characters a verified quote needs
PREFIX_CHARS = 20  # a quote ending in "..." matches its first PREFIX_CHARS characters or more
MIN_RELATION_CHARS = 20
MAX_READ_BYTES = 5_000_000
MAX_REPORTED = 40
_LINE_NUMBER = re.compile(r"(?m)^\s*\d+[:|]\s?")
_LOCATOR = re.compile(r"^\s*(?:[\w./\\-]+:)?\d+:\s*")  # "path:line:" or "line:" at the start of a quote
_CURLY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "′": "'", "″": '"'})
_MARKUP = re.compile(r"`+|\*\*|__")
_REPO_PREFIX = re.compile(r"^(?:\./|repo/)+")


def tier_of(requirement: dict | None) -> str:
    """``product`` or ``component``; a requirement without a (valid) tier is component (old runs)."""
    tier = str((requirement or {}).get("tier") or "component")
    return tier if tier in TIERS else "component"


def is_gap(requirement: dict | None) -> bool:
    """A requirement the host demoted (unverified evidence). Gap requirements do not require cases."""
    return (requirement or {}).get("status") == "gap"


def normalize(text: Any) -> str:
    """NFKC, straight quotes, markdown emphasis and code markers removed, whitespace collapsed (same on both sides)."""
    folded = unicodedata.normalize("NFKC", str(text or "")).translate(_CURLY)
    return " ".join(_MARKUP.sub("", folded).split())


def normalize_path(rel: Any) -> str:
    """Repository-relative path as the model writes it: no leading ``./`` or ``repo/``, forward slashes."""
    return _REPO_PREFIX.sub("", str(rel or "").strip().replace("\\", "/"))


def _needles(quote: Any) -> set[str]:
    raw = str(quote or "")
    forms = {
        raw,
        _LINE_NUMBER.sub("", raw),
        _LOCATOR.sub("", raw),
        _LOCATOR.sub("", _LINE_NUMBER.sub("", raw)),
    }
    needles = set()
    for form in forms:
        needle = normalize(form)
        if needle.endswith("..."):  # a prefix quote: the first PREFIX_CHARS characters must match
            needle = needle[:-3].rstrip()
            if len(needle.replace(" ", "")) >= PREFIX_CHARS:
                needles.add(needle)
            continue
        if len(needle.replace(" ", "")) >= MIN_QUOTE_CHARS:
            needles.add(needle)
    return needles


def quote_in_text(quote: Any, text: str | None) -> bool:
    """True when the quote appears verbatim in text after normalisation (see ``normalize``).

    Accepted forms: the quote itself, the quote without ``path:line:``/``line:`` locators, without line-number prefixes
    (evidence listings), and a trailing ``...`` quote that matches a prefix of at least PREFIX_CHARS characters.
    """
    if not text:
        return False
    needles = _needles(quote)
    if not needles:
        return False
    haystacks = {normalize(text), normalize(_LINE_NUMBER.sub("", text))}
    return any(needle in hay for needle in needles for hay in haystacks)


def file_reader(root: Path | None) -> Callable[[str], str | None]:
    """Reads a repository file under ``root`` (text, BOM and CRLF tolerated, up to MAX_READ_BYTES).

    None for missing, escaping (``..``, absolute, symlink out of the tree), oversized, binary (NUL byte) or unreadable
    paths. Accepts ``./`` and ``repo/`` prefixes.
    """
    base = root.resolve() if root is not None else None

    def read(rel: str) -> str | None:
        clean = normalize_path(rel)
        if base is None or not clean or Path(clean).is_absolute() or ".." in Path(clean).parts:
            return None
        try:
            target = (base / clean).resolve()
            if not target.is_relative_to(base) or not target.is_file():
                return None
            with target.open("rb") as handle:
                data = handle.read(MAX_READ_BYTES + 1)
        except OSError:
            return None
        if len(data) > MAX_READ_BYTES or b"\x00" in data[:8192]:
            return None
        return data.decode("utf-8-sig", errors="replace")

    return read


def oracle_shape_problems(case: dict, derived_allowed: bool = True) -> list[str]:
    """Structural rules (no file reads). Each message names the case and says what to change."""
    title = str(case.get("title") or case.get("id") or "case")
    oracle = case.get("oracle")
    if not isinstance(oracle, dict):
        return [
            f"{title}: no oracle. Add an oracle with a kind; use characterization if no source supports the value."
        ]
    kind = oracle.get("kind")
    if kind not in ORACLE_KINDS:
        return [f"{title}: oracle kind {kind!r} is not one of {', '.join(ORACLE_KINDS)}."]
    source = oracle.get("source")
    problems = []
    if kind == "derived" and not derived_allowed:
        problems.append(
            f"{title}: derived is unavailable in greenfield runs (there is no repository source). Use specified with "
            "a verbatim quote from the supplied documents, or characterization if no document supports the value."
        )
    elif kind in QUOTE_KINDS and not (
        isinstance(source, dict)
        and str(source.get("file") or "").strip()
        and str(source.get("quote") or "").strip()
    ):
        problems.append(
            f"{title}: {kind} needs source {{file, line, quote}} with a verbatim quote. Copy the exact line from the "
            "file, or choose another kind, or use characterization."
        )
    if kind in RELATION_KINDS and len(normalize(oracle.get("relation"))) < MIN_RELATION_CHARS:
        problems.append(
            f"{title}: {kind} needs a stated relation of at least {MIN_RELATION_CHARS} characters that names the "
            "target and a concrete input transformation or property."
        )
    return problems


def check_oracles(
    cases: Iterable[dict],
    read_file: Callable[[str], str | None],
    corpus: str = "",
    derived_allowed: bool = True,
) -> list[str]:
    """Check every case's oracle and set ``oracle["verified"]`` / ``oracle["stated"]``. Returns ALL problems.

    ``corpus`` is the user-supplied greenfield document text (empty in repository runs); it is the only non-repository
    text a quote may come from, and only for specified and golden rules. Model-written requirement text or evidence is
    never a quote source. A case whose oracle fails is not verified.
    """
    problems: list[str] = []
    for case in cases:
        title = str(case.get("title") or case.get("id") or "case")
        oracle = case.get("oracle") if isinstance(case.get("oracle"), dict) else {}
        oracle["verified"] = False
        oracle["stated"] = False
        shape = oracle_shape_problems(case, derived_allowed)
        if shape:
            problems += shape
            continue
        kind = oracle["kind"]
        if kind == "characterization":
            continue
        source = oracle.get("source")
        problems_here: list[str] = []
        quote_ok = True
        if isinstance(source, dict) and str(source.get("quote") or "").strip():
            file = normalize_path(source.get("file"))
            text = read_file(file)
            found = quote_in_text(source["quote"], text)
            if not found and kind in ("specified", "golden"):
                found = quote_in_text(source["quote"], corpus)
            if not found:
                quote_ok = False
                if text is None:
                    where = (
                        "the repository or the supplied documents" if kind != "derived" else "the repository"
                    )
                    problems_here.append(
                        f"{title}: {kind} source file {file} does not exist in {where}. Use an existing file, "
                        "choose another kind, or use characterization."
                    )
                else:
                    problems_here.append(
                        f"{title}: the {kind} quote is not verbatim in {file}. Copy the exact source line without "
                        "line numbers from that file, choose another kind, or use characterization if no source "
                        "supports the value."
                    )
        if kind in STATED_KINDS:
            target = str(case.get("target") or "")
            name = re.split(r"[.:#/]", target)[-1].strip() if target else ""
            if name and name.lower() not in normalize(oracle.get("relation")).lower():
                problems_here.append(
                    f"{title}: the {kind} relation must name the target {target} and a concrete input "
                    "transformation or property."
                )
            oracle["stated"] = quote_ok and not problems_here
        if kind in QUOTE_KINDS:
            oracle["verified"] = quote_ok and not problems_here
        problems += problems_here
    return problems


def oracle_repair_message(problems: list[str]) -> str:
    """The repair text for the model: every problem (up to MAX_REPORTED), one line each, with the action to take."""
    shown = problems[:MAX_REPORTED]
    lines = [f"Test oracle check failed for {len(problems)} problem(s). Fix every case below:"]
    lines += [f"- {p[:400]}" for p in shown]
    if len(problems) > len(shown):
        lines.append(f"- and {len(problems) - len(shown)} more; fix them the same way.")
    return "\n".join(lines)


def counts_as_verified(case: dict | None) -> bool:
    """The single definition. specified, derived and golden: the host quote check passed. metamorphic and property:
    the relation is stated and the case has a test (``case["test_exists"]``, set by the report). characterization never.
    """
    oracle = (case or {}).get("oracle")
    if not isinstance(oracle, dict):
        return False
    kind = oracle.get("kind")
    if kind in QUOTE_KINDS:
        return oracle.get("verified") is True
    if kind in STATED_KINDS:
        return oracle.get("stated") is True and (case or {}).get("test_exists") is True
    return False


def oracle_label(case: dict | None) -> str:
    """What the report shows for a case: its kind when verified or stated, otherwise ``characterization`` or
    ``unverified`` (also used for a missing or invalid oracle)."""
    oracle = (case or {}).get("oracle") if isinstance((case or {}).get("oracle"), dict) else {}
    kind = oracle.get("kind")
    if kind == "characterization":
        return "characterization"
    if counts_as_verified(case):
        return "verified" if kind in QUOTE_KINDS else kind
    if kind in STATED_KINDS and oracle.get("stated") is True:
        return f"{kind} (stated, no test)"
    return "unverified"


def _cases(design: dict | None) -> list[dict]:
    return [
        case
        for key, value in (design or {}).items()
        if key.endswith("_tests") and isinstance(value, list)
        for case in value
        if isinstance(case, dict)
    ]


def oracle_summary(design: dict, requirements: dict) -> dict:
    """Counts for the report: oracle kinds, labels, tiers, characterization cases, and product requirements with or
    without cases.

    ``requirements`` is the requirements artifact (``requirement_model`` inside). A case's tier is ``product`` when it
    links a product requirement directly or through an acceptance criterion, otherwise ``component``. Gap requirements
    are listed under ``product_requirements.gap`` and are not expected to have cases.
    """
    model = (requirements or {}).get("requirement_model", {})
    tiers = {str(r.get("id")): tier_of(r) for r in model.get("requirements", [])}
    parent = {str(c.get("id")): str(c.get("requirement_id")) for c in model.get("acceptance_criteria", [])}
    gaps = sorted(str(r.get("id")) for r in model.get("requirements", []) if is_gap(r))
    by_kind: dict[str, int] = dict.fromkeys((*ORACLE_KINDS, "missing"), 0)
    by_label: dict[str, int] = {}
    cases_by_tier = dict.fromkeys(TIERS, 0)
    covered: set[str] = set()
    characterization: list[str] = []
    verified = 0
    cases = _cases(design)
    for case in cases:
        oracle = case.get("oracle") if isinstance(case.get("oracle"), dict) else {}
        kind = oracle.get("kind") if oracle.get("kind") in ORACLE_KINDS else "missing"
        by_kind[kind] += 1
        label = oracle_label(case)
        by_label[label] = by_label.get(label, 0) + 1
        if kind == "characterization":
            characterization.append(str(case.get("id")))
        verified += counts_as_verified(case)
        linked = set()
        for link in case.get("traceability_links") or []:
            if link.get("type") == "requirement":
                linked.add(str(link.get("id")))
            elif link.get("type") == "acceptance_criterion":
                linked.add(parent.get(str(link.get("id")), ""))
        covered |= linked
        product = any(tiers.get(rid) == "product" for rid in linked)
        cases_by_tier["product" if product else "component"] += 1
    requirements_by_tier = dict.fromkeys(TIERS, 0)
    for tier in tiers.values():
        requirements_by_tier[tier] += 1
    product_ids = sorted(rid for rid, tier in tiers.items() if tier == "product")
    return {
        "total_cases": len(cases),
        "by_kind": by_kind,
        "by_label": by_label,
        "verified_cases": verified,
        "characterization_cases": sorted(characterization),
        "requirements_by_tier": requirements_by_tier,
        "cases_by_tier": cases_by_tier,
        "product_requirements": {
            "total": len(product_ids),
            "with_cases": [rid for rid in product_ids if rid in covered],
            "without_cases": [rid for rid in product_ids if rid not in covered and rid not in gaps],
            "gap": [rid for rid in gaps if tiers.get(rid) == "product"],
        },
    }

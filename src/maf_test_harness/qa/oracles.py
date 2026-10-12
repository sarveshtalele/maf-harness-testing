"""Pure helpers for requirement tiers, verbatim evidence quotes and test oracles (no model calls).

Tiers: ``product`` requirements are what the repository promises its users (README, SKILL.md, docs, references, CLI
help); ``component`` requirements are callable-level declarations. Runs written before tiers existed read as component.

Oracles: every design case states where its expected value comes from. ``specified`` and ``derived`` need a verbatim
quote (documentation or implementation), ``golden`` needs a verified documented rule plus a scenario description,
``metamorphic`` and ``property`` need a stated relation or property, and ``characterization`` records current behaviour
only. A characterization case is never verified; ``counts_as_verified`` is the single definition.
"""

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

ORACLE_KINDS = ("specified", "derived", "metamorphic", "golden", "property", "characterization")
TIERS = ("product", "component")
QUOTE_SOURCE_KINDS = ("specified", "derived", "golden")  # a verified quote is required
RELATION_KINDS = ("metamorphic", "golden", "property")  # a stated relation or property is required
MIN_QUOTE_CHARS = 8  # non-space characters a verified quote needs
MIN_RELATION_CHARS = 20
MAX_READ_BYTES = 512_000
_LINE_NUMBER = re.compile(r"(?m)^\s*\d+[:|]\s?")


def tier_of(requirement: dict | None) -> str:
    """``product`` or ``component``; a requirement without a (valid) tier is component (old runs)."""
    tier = str((requirement or {}).get("tier") or "component")
    return tier if tier in TIERS else "component"


def _collapse(text: Any) -> str:
    return " ".join(str(text or "").split())


def quote_in_text(quote: Any, text: str | None) -> bool:
    """True when the quote is a whitespace-normalised substring of text (line-number prefixes ignored on either side)."""
    if not text:
        return False
    needles = {_collapse(quote), _collapse(_LINE_NUMBER.sub("", str(quote or "")))}
    haystacks = {_collapse(text), _collapse(_LINE_NUMBER.sub("", text))}
    return any(
        len(needle.replace(" ", "")) >= MIN_QUOTE_CHARS and any(needle in hay for hay in haystacks)
        for needle in needles
    )


def file_reader(root: Path | None) -> Callable[[str], str | None]:
    """Reads a repository-relative file under ``root``; None for missing, escaping, oversized or unreadable paths."""
    base = root.resolve() if root is not None else None

    def read(rel: str) -> str | None:
        if base is None or not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            return None
        target = (base / rel).resolve()
        try:
            if (
                not target.is_relative_to(base)
                or not target.is_file()
                or target.stat().st_size > MAX_READ_BYTES
            ):
                return None
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    return read


def oracle_shape_problems(case: dict) -> list[str]:
    """Structural rules (no file reads): kind present, source where required, relation where required."""
    title = str(case.get("title") or case.get("id") or "case")
    oracle = case.get("oracle")
    if not isinstance(oracle, dict):
        return [f"{title}: no oracle; every case states an oracle kind"]
    kind = oracle.get("kind")
    if kind not in ORACLE_KINDS:
        return [f"{title}: oracle kind {kind!r} is not one of {', '.join(ORACLE_KINDS)}"]
    source = oracle.get("source")
    problems = []
    if kind in QUOTE_SOURCE_KINDS and not (
        isinstance(source, dict) and _collapse(source.get("file")) and _collapse(source.get("quote"))
    ):
        problems.append(f"{title}: {kind} oracle needs a source file and a verbatim quote")
    if kind in RELATION_KINDS and len(_collapse(oracle.get("relation"))) < MIN_RELATION_CHARS:
        problems.append(
            f"{title}: {kind} oracle needs a stated relation or property (at least {MIN_RELATION_CHARS} characters)"
        )
    return problems


def check_oracles(
    cases: Iterable[dict], read_file: Callable[[str], str | None], corpus: str = ""
) -> list[str]:
    """Verify every case's oracle: shape, then the quote in its source file (or the approved requirement corpus, where
    a documented rule may be quoted). Sets ``oracle["verified"]`` on each case; returns the problems found.

    ``derived`` quotes must come from the repository file. A quote the host cannot find demotes the case: it is not
    verified, so it never counts as verified.
    """
    problems: list[str] = []
    for case in cases:
        shape = oracle_shape_problems(case)
        oracle = case.get("oracle") if isinstance(case.get("oracle"), dict) else {}
        if shape:
            oracle["verified"] = False
            problems += shape
            continue
        kind = oracle["kind"]
        source = oracle.get("source")
        ok = True
        if isinstance(source, dict) and _collapse(source.get("quote")):
            text = read_file(str(source.get("file") or "")) or ""
            found = quote_in_text(source["quote"], text) or (
                kind != "derived" and quote_in_text(source["quote"], corpus)
            )
            if not found:
                ok = False
                problems.append(
                    f"{case.get('title') or case.get('id')}: {kind} quote is not verbatim in {source.get('file')}"
                )
        oracle["verified"] = bool(ok and kind != "characterization")
    return problems


def counts_as_verified(case: dict | None) -> bool:
    """A case counts as verified only with a valid, host-verified, non-characterization oracle."""
    oracle = (case or {}).get("oracle")
    return (
        isinstance(oracle, dict)
        and oracle.get("kind") != "characterization"
        and oracle.get("verified") is True
    )


def _cases(design: dict | None) -> list[dict]:
    return [
        case
        for key, value in (design or {}).items()
        if key.endswith("_tests") and isinstance(value, list)
        for case in value
        if isinstance(case, dict)
    ]


def oracle_summary(design: dict, requirements: dict) -> dict:
    """Counts for the report: oracle kinds, tiers, characterization cases, and product requirements with or without cases.

    ``requirements`` is the requirements artifact (``requirement_model`` inside). A case's tier is ``product`` when it
    links a product requirement directly or through an acceptance criterion, otherwise ``component``.
    """
    model = (requirements or {}).get("requirement_model", {})
    tiers = {str(r.get("id")): tier_of(r) for r in model.get("requirements", [])}
    parent = {str(c.get("id")): str(c.get("requirement_id")) for c in model.get("acceptance_criteria", [])}
    gaps = sorted(str(r.get("id")) for r in model.get("requirements", []) if r.get("status") == "gap")
    by_kind: dict[str, int] = dict.fromkeys((*ORACLE_KINDS, "missing"), 0)
    cases_by_tier = dict.fromkeys(TIERS, 0)
    covered: set[str] = set()
    characterization: list[str] = []
    verified = 0
    cases = _cases(design)
    for case in cases:
        oracle = case.get("oracle") if isinstance(case.get("oracle"), dict) else {}
        kind = oracle.get("kind") if oracle.get("kind") in ORACLE_KINDS else "missing"
        by_kind[kind] += 1
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
        "verified_cases": verified,
        "characterization_cases": sorted(characterization),
        "requirements_by_tier": requirements_by_tier,
        "cases_by_tier": cases_by_tier,
        "product_requirements": {
            "total": len(product_ids),
            "with_cases": [rid for rid in product_ids if rid in covered],
            "without_cases": [rid for rid in product_ids if rid not in covered],
            "gap": [rid for rid in gaps if tiers.get(rid) == "product"],
        },
    }

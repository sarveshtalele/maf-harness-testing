"""Turn raw tool output into diagnoses: which known error it is, whose fault, and what may be done about it."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import classifiers
from .taxonomy import APP_DEFECT, CODES, ENVIRONMENT, INFRA, TEST_DEFECT, UNKNOWN

_RANK = {ENVIRONMENT: 0, INFRA: 1, TEST_DEFECT: 2, APP_DEFECT: 3, UNKNOWN: 4}


@dataclass
class Context:
    """Facts that decide an ambiguous error: an import error is an environment problem exactly when the package is
    a declared dependency that failed to install."""

    missing_packages: list[str] = field(default_factory=list)


@dataclass
class Diagnosis:
    code: str
    category: str
    autonomy: str
    title: str
    hint: str
    recipes: tuple[str, ...]
    max_attempts: int
    evidence: str = ""
    detail: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "recipes": list(self.recipes)}


def _diagnosis(code: str, evidence: str, detail: dict[str, str] | None = None) -> Diagnosis:
    known = CODES[code]
    return Diagnosis(
        known.code,
        known.category,
        known.autonomy,
        known.title,
        known.hint,
        known.recipes,
        known.max_attempts,
        evidence[:240].strip(),
        detail or {},
    )


def _tables(ecosystem: str | None) -> list[list[classifiers.Rule]]:
    languages = classifiers.BY_ECOSYSTEM.get(ecosystem or "", [] if ecosystem else classifiers.ALL_LANGUAGES)
    return [*languages, classifiers.COMMON, *classifiers.EXECUTION]


def classify(text: str, ecosystem: str | None = None, context: Context | None = None) -> list[Diagnosis]:
    """Every known error found in ``text``, most actionable first (environment, infra, test, application)."""
    text = text or ""
    found: dict[str, Diagnosis] = {}
    for table in _tables(ecosystem):
        for code, pattern in table:
            match = pattern.search(text)
            if not match or code in found:
                continue
            line = text[text.rfind("\n", 0, match.start()) + 1 : text.find("\n", match.end())]
            found[code] = _diagnosis(
                code, line or match.group(0), {k: v for k, v in match.groupdict().items() if v}
            )
    diagnoses = [_resolve(d, context) for d in found.values()]
    unique = {d.code: d for d in diagnoses}
    return sorted(unique.values(), key=lambda d: _RANK[d.category])  # stable: table order breaks ties


def _resolve(diagnosis: Diagnosis, context: Context | None) -> Diagnosis:
    """An import error of a declared-but-uninstalled package is the environment's fault, not the test's."""
    module = diagnosis.detail.get("module")
    if diagnosis.code == "EXEC-101" and module and context and context.missing_packages:
        from .. import env_check  # imported here: env_check -> runners, and runners use this package

        dist = env_check.explained_by_missing(module.split(".")[0], context.missing_packages)
        if dist:
            return _diagnosis("ENV-PY-007", diagnosis.evidence, {"module": module, "package": dist})
    return diagnosis


def classify_result(
    result: dict[str, Any], text: str = "", ecosystem: str | None = None, context: Context | None = None
) -> list[Diagnosis]:
    """Like :func:`classify`, but also understands the runner's own statuses (``timeout``, ``blocked``)."""
    extra = ""
    if result.get("status") == "timeout":
        extra = "Command timed out after the step's time limit"
    elif result.get("status") == "blocked":
        extra = str(result.get("error") or "")
    return classify("\n".join(part for part in (extra, text) if part), ecosystem, context)


def primary(diagnoses: list[Diagnosis]) -> Diagnosis | None:
    return diagnoses[0] if diagnoses else None


def is_environmental(diagnoses: list[Diagnosis]) -> bool:
    """True when the most actionable diagnosis is an environment or infrastructure problem (never a test's fault)."""
    first = primary(diagnoses)
    return bool(first and first.category in {ENVIRONMENT, INFRA})

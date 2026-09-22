"""Pattern tables per ecosystem. A rule is ``(error code, regular expression)``; order is priority (first match wins
inside a table, and every table contributes at most one diagnosis per code)."""

from __future__ import annotations

from . import common, dotnet, java, python
from ._compile import Rule

# Language-specific tables first (they know the toolchain's own wording), then shared ones, then execution errors.
BY_ECOSYSTEM: dict[str, list[list[Rule]]] = {
    "python": [python.RULES],
    "java": [java.RULES],
    "maven": [java.RULES],
    "gradle": [java.RULES],
    "dotnet": [dotnet.RULES],
}
ALL_LANGUAGES: list[list[Rule]] = [python.RULES, java.RULES, dotnet.RULES]
COMMON: list[Rule] = common.RULES
EXECUTION: list[list[Rule]] = [python.EXEC_RULES, java.EXEC_RULES, dotnet.EXEC_RULES, common.EXEC_RULES]

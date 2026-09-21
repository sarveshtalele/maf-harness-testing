"""Shared helper to compile a pattern table."""

from __future__ import annotations

import re

Rule = tuple[str, "re.Pattern[str]"]


def compile_rules(rules: list[tuple[str, str]]) -> list[Rule]:
    return [(code, re.compile(pattern, re.I | re.M)) for code, pattern in rules]

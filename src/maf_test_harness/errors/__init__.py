"""Error taxonomy, classifiers and recovery playbook (see docs/ERROR_PLAYBOOK.md)."""

from __future__ import annotations

from .engine import Context, Diagnosis, classify, classify_result, is_environmental, primary
from .playbook import RECIPES, Recipe
from .taxonomy import (
    APP_DEFECT,
    CODES,
    ENVIRONMENT,
    INFRA,
    TEST_DEFECT,
    UNKNOWN,
    ErrorCode,
)

__all__ = [
    "APP_DEFECT",
    "CODES",
    "ENVIRONMENT",
    "INFRA",
    "RECIPES",
    "TEST_DEFECT",
    "UNKNOWN",
    "Context",
    "Diagnosis",
    "ErrorCode",
    "Recipe",
    "classify",
    "classify_result",
    "is_environmental",
    "primary",
]

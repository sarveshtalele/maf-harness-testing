"""Single-source runtime settings for the MAF test harness.

Credentials are never stored in source or the UI. Supply them through the launching
environment (see ``.env.example``). Every tunable has a documented default.
"""
from __future__ import annotations

import os

API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()
MODEL = os.getenv("OPENAI_MODEL", "deepseek-v4-flash-global").strip()

# "progressive": real MAF SkillsProvider (advertise -> load_skill -> read_skill_resource); the host
#                verifies the assigned skill was actually loaded through the MAF tool before accepting output.
# "preloaded":   host injects SKILL.md into agent instructions (no MAF skill tools). Fallback for models
#                that cannot call tools; recorded in provenance as NOT using the MAF Agent Skills provider.
SKILL_MODE = os.getenv("HARNESS_SKILL_MODE", "progressive").strip().lower()

RUN_TOKEN_LIMIT = int(os.getenv("HARNESS_RUN_TOKEN_LIMIT", "1000000"))
INPUT_TOKEN_LIMIT = int(os.getenv("HARNESS_INPUT_TOKEN_LIMIT", "200000"))
CONTEXT_WINDOW_TOKENS = int(os.getenv("HARNESS_CONTEXT_WINDOW_TOKENS", "200000"))
PROVIDER_CALL_LIMIT = int(os.getenv("HARNESS_PROVIDER_CALL_LIMIT", "300"))

# Provider response cap, not the total workflow budget.
MAX_OUTPUT_TOKENS = int(os.getenv("HARNESS_MAX_OUTPUT_TOKENS", "16384"))

# Approved design cases implemented per provider call during test generation.
GENERATION_BATCH_SIZE = max(1, int(os.getenv("HARNESS_GENERATION_BATCH_SIZE", "3")))
GENERATION_MAX_ATTEMPTS = max(1, int(os.getenv("HARNESS_GENERATION_MAX_ATTEMPTS", "3")))

# Wall-clock limit for each host-run setup/test command.
EXECUTION_TIMEOUT_SECONDS = float(os.getenv("HARNESS_EXECUTION_TIMEOUT", "900"))


def validate_gateway_settings() -> None:
    if not API_KEY:
        raise ValueError("Set OPENAI_API_KEY before starting the workflow.")
    if not MODEL:
        raise ValueError("Set OPENAI_MODEL before starting the workflow.")
    if SKILL_MODE not in {"progressive", "preloaded"}:
        raise ValueError("HARNESS_SKILL_MODE must be 'progressive' or 'preloaded'.")

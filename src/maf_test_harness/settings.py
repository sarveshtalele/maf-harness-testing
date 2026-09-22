"""Single-source runtime settings for the MAF test harness.

Credentials are never stored in source or the UI. Supply them through the launching
environment, or through a git-ignored ``.env`` file at the project root (see ``.env.example``);
a variable that is already set in the environment always wins. Every tunable has a documented default.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_DOTENV_KEY = re.compile(r"(?:OPENAI_[A-Z_]+|LLM_MODEL|HARNESS_[A-Z_]+)")


def load_dotenv(path: Path) -> None:
    """Read ``KEY=VALUE`` lines from ``path`` into the environment without overriding anything already set.

    Only the harness's own variables are read (OPENAI_*, LLM_MODEL, HARNESS_*), so an unrelated ``.env`` cannot inject
    other settings. Values may be quoted; an unquoted value ends at `` #`` (inline comment).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if not _DOTENV_KEY.fullmatch(key):
            continue
        if value[:1] in {'"', "'"}:
            end = value.find(value[0], 1)
            value = value[1:end] if end > 0 else value[1:]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if value:
            os.environ.setdefault(key, value)


for _candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
    load_dotenv(_candidate)

API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()
MODEL = (os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "deepseek-v4-flash-global").strip()

# "progressive": real MAF SkillsProvider (advertise -> load_skill -> read_skill_resource); the host
#                verifies the assigned skill was actually loaded through the MAF tool before accepting output.
# "preloaded":   host injects SKILL.md into agent instructions (no MAF skill tools). Fallback for models
#                that cannot call tools; recorded in provenance as NOT using the MAF Agent Skills provider.
SKILL_MODE = os.getenv("HARNESS_SKILL_MODE", "progressive").strip().lower()

# The ONLY spending limit: tokens (input + output) across ALL runs on this machine per local calendar day.
# There are deliberately no per-run, per-call, or call-count limits. See usage.py.
# The owner of the machine may raise it for a day with HARNESS_DAILY_TOKEN_LIMIT; the default is 50,000,000.
DAILY_TOKEN_LIMIT = int(os.getenv("HARNESS_DAILY_TOKEN_LIMIT", "50000000"))

# Central deterministic tools (code_intel/autofix/build_gate). Each can be switched off for A/B comparison.
AGENT_TOOLS = os.getenv("HARNESS_AGENT_TOOLS", "on").strip().lower() != "off"  # tools agents may call
AUTOFIX = (
    os.getenv("HARNESS_AUTOFIX", "on").strip().lower() != "off"
)  # mechanical repairs before any LLM retry
BUILD_GATE = (
    os.getenv("HARNESS_BUILD_GATE", "on").strip().lower() != "off"
)  # compile/collect after env install

# STRICT GUARDRAIL (default on): environment_setup must install every declared requirement, consistently, before any
# later stage starts. "off" restores the old behaviour (warn, then let the reviewer proceed). See docs/ENVIRONMENTS.md.
STRICT_ENV = os.getenv("HARNESS_STRICT_ENV", "on").strip().lower() != "off"

# Hooks (see docs/HOOKS.md): "enforce" (default), "evaluate_only" (record what would be blocked, never block) or "off".
HOOKS = os.getenv("HARNESS_HOOKS", "enforce").strip().lower()
# Who enforces hooks inside the agent loop: "inhouse" (default, no extra package) or "sdk" (MAF AGENT-HOOKS-0.1 middleware,
# needs `pip install maf-test-harness[hooks]`).
HOOKS_BACKEND = os.getenv("HARNESS_HOOKS_BACKEND", "inhouse").strip().lower()

# Agent Skill validators as an in-loop tool for the design stage (off: each tool schema is resent with every call).
SKILL_EVALUATORS = os.getenv("HARNESS_SKILL_EVALUATORS", "off").strip().lower() == "on"

# An LLM diagnosis agent (typed tools, hooks, todo list) for setup failures the deterministic recovery cannot fix.
DIAGNOSIS_AGENT = os.getenv("HARNESS_DIAGNOSIS_AGENT", "off").strip().lower() == "on"

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

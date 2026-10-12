"""Single-source runtime settings for the MAF test harness.

Credentials are never stored in source or the UI. Supply them through the launching
environment, or through a git-ignored ``.env`` file at the project root (see ``.env.example``);
a variable that is already set in the environment always wins. Every tunable has a documented default.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from . import paths

_DOTENV_KEY = re.compile(r"(?:OPENAI_[A-Z_]+|LLM_MODEL|HARNESS_[A-Z_]+)")
_from_dotenv: dict[str, str] = {}  # variables load_dotenv put into the environment (to report their source)


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
        if value and key not in os.environ:
            os.environ[key] = _from_dotenv[key] = value


# Only the install root or user-data .env is read: a checkout the user runs the harness from may be untrusted and
# must not be able to set HARNESS_* switches or redirect the API base URL.
load_dotenv(paths.env_file())

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
GENERATION_BATCH_SIZE = min(8, max(1, int(os.getenv("HARNESS_GENERATION_BATCH_SIZE", "3"))))
# Design cases kept per requirement (host-enforced after design); a run may override it ({"case_budget": ...}).
CASES_PER_REQUIREMENT = min(10, max(1, int(os.getenv("HARNESS_CASES_PER_REQUIREMENT", "2"))))
GENERATION_MAX_ATTEMPTS = max(1, int(os.getenv("HARNESS_GENERATION_MAX_ATTEMPTS", "3")))

# Test generation: fail-soft batches. If failed_batches / total > this ratio, or zero files generated, raise.
# Default 0.5 allows up to half the batches to fail; clamp to [0, 1].
MAX_FAILED_BATCH_RATIO = max(0.0, min(1.0, float(os.getenv("HARNESS_MAX_FAILED_BATCH_RATIO", "0.5"))))

# Review confidence gates (plan, design, test) are HARD gates: the resolved value is always within 95-100, so neither a
# run override nor an env value can weaken them. Resolution: run override > env > REVIEW_DEFAULT_MIN_CONFIDENCE.
REVIEW_KINDS = ("plan", "design", "test")
REVIEW_GATE_FLOOR = 95
REVIEW_GATE_CEILING = 100
REVIEW_DEFAULT_MIN_CONFIDENCE = REVIEW_GATE_FLOOR


def _threshold(raw) -> int | None:
    """An integer confidence clamped into the hard gate range; anything that is not an integer yields None."""
    if isinstance(raw, bool):
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return max(REVIEW_GATE_FLOOR, min(REVIEW_GATE_CEILING, value))


def review_threshold(kind: str, state: dict | None = None) -> int:
    """Minimum confidence (95-100) a reviewed ``kind`` artifact needs. Reads, in order: the run's override in
    ``state["confidence_thresholds"][kind]``, the env ``HARNESS_{KIND}_REVIEW_MIN_CONFIDENCE``, then the default 95.
    Values below 95 are raised to 95 and values above 100 are lowered to 100; malformed values fall back to the next
    source, so a bad value can never silently remove the gate."""
    if kind not in REVIEW_KINDS:
        raise ValueError(f"Unknown review kind {kind!r}; expected one of {', '.join(REVIEW_KINDS)}.")
    overrides = (state or {}).get("confidence_thresholds") or {}
    value = _threshold(overrides.get(kind)) if isinstance(overrides, dict) else None
    if value is None:
        value = _threshold(os.getenv(f"HARNESS_{kind.upper()}_REVIEW_MIN_CONFIDENCE", ""))
    return REVIEW_DEFAULT_MIN_CONFIDENCE if value is None else value


def test_review_threshold(state: dict | None = None) -> int:
    """The test-review gate of a run. Generation and test review call this at run time, never a constant."""
    return review_threshold("test", state)


# Import-time alias kept for modules that still read the test gate as a constant (new code uses the resolver).
TEST_REVIEW_MIN_CONFIDENCE = review_threshold("test")


def full_accuracy(state: dict | None = None) -> bool:
    """Full-accuracy gate (default on): every designed case must be generated and accepted before execution can start.
    Off restores the fail-soft behaviour (failed or unverified cases run and report generation_failed; reviewer gates
    turn advisory). A run's own ``require_full_accuracy`` wins over ``HARNESS_REQUIRE_FULL_ACCURACY``, read at call time."""
    value = (state or {}).get("require_full_accuracy")
    if isinstance(value, bool):
        return value
    return os.getenv("HARNESS_REQUIRE_FULL_ACCURACY", "1").strip().lower() not in {"0", "false", "off", "no"}


def design_review_enabled() -> bool:
    """HARNESS_DESIGN_REVIEW=0 turns the design confidence reviewer, and its gate, off."""
    return os.getenv("HARNESS_DESIGN_REVIEW", "1").strip().lower() not in {"0", "false", "off", "no"}


# Targeted regeneration rounds for cases still unverified before the gate blocks (clamped to 0-5).
ACCURACY_ROUNDS = max(0, min(5, int(os.getenv("HARNESS_ACCURACY_ROUNDS", "2"))))


# Per-stage model overrides: JSON object {stage_name: model_id}. Unset or invalid JSON defaults to MODEL.
# Example: {"test_generation": "claude-opus-4", "test_design": "claude-sonnet-4"}
def parse_stage_models(text: str) -> dict[str, str]:
    """Parse a HARNESS_STAGE_MODELS JSON object into {stage: model}; raises ValueError when malformed."""
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"HARNESS_STAGE_MODELS must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("HARNESS_STAGE_MODELS must be a JSON object {stage: model}.")
    return {str(k): str(v).strip() for k, v in parsed.items() if str(v).strip()}


def _stage_models_or_default(text: str) -> dict[str, str]:
    """Invalid JSON falls back to the default model for every stage (it must not stop the import)."""
    try:
        return parse_stage_models(text or "{}")
    except ValueError:
        logging.getLogger(__name__).warning(
            "HARNESS_STAGE_MODELS is not a JSON object; using %s for all stages", MODEL
        )
        return {}


STAGE_MODELS: dict[str, str] = _stage_models_or_default(os.getenv("HARNESS_STAGE_MODELS", "").strip())


# ---- runtime resolver ---------------------------------------------------------------------------------------------
# Precedence per field: in-process runtime override (set via the API/desktop) > environment > .env file > default.
# The module constants above (API_KEY, BASE_URL, MODEL, STAGE_MODELS) are import-time snapshots kept only for
# backward-compatible reads; build clients and hosts from current().
_runtime: dict = {}
_runtime_lock = threading.Lock()
_ENV_NAMES = {
    "api_key": ("OPENAI_API_KEY",),
    "base_url": ("OPENAI_BASE_URL",),
    "model": ("OPENAI_MODEL", "LLM_MODEL"),
    "stage_models": ("HARNESS_STAGE_MODELS",),
}


def _env_source(field: str) -> tuple[str, str]:
    for name in _ENV_NAMES[field]:
        value = os.environ.get(name, "").strip()
        if value:
            return value, (".env" if _from_dotenv.get(name) == os.environ.get(name) else "environment")
    return "", "default"


def current() -> dict:
    """Resolve {api_key, base_url, model, stage_models, source} now. ``source`` is where the API key comes from."""
    with _runtime_lock:
        rt = dict(_runtime)
    out: dict = {}
    sources: dict[str, str] = {}
    for field in ("api_key", "base_url", "model", "stage_models"):
        if field in rt:
            out[field], sources[field] = rt[field], "runtime"
        else:
            out[field], sources[field] = _env_source(field)
    out["stage_models"] = (
        _stage_models_or_default(out["stage_models"])
        if isinstance(out["stage_models"], str)
        else (out["stage_models"] or {})
    )
    out["base_url"] = validate_base_url(out["base_url"]) if out["base_url"] else ""
    out["model"] = out["model"] or "deepseek-v4-flash-global"
    out["source"] = sources["api_key"] if out["api_key"] else "none"
    out["sources"] = sources
    return out


_LOCAL_APP_URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/\S*)?")


def is_local_app_url(value: str) -> bool:
    """True for an http(s) URL on localhost or 127.0.0.1: the application under test runs on this host."""
    return _LOCAL_APP_URL.fullmatch(value.strip()) is not None


def validate_base_url(url: str) -> str:
    """https only; plain http solely for localhost / 127.0.0.1. Empty means the provider default."""
    url = url.strip()
    if not url:
        return ""
    parts = urlsplit(url)
    local = parts.hostname in {"localhost", "127.0.0.1"}
    if parts.scheme not in {"https", "http"} or not parts.hostname or (parts.scheme == "http" and not local):
        raise ValueError("Base URL must use https (http is allowed only for localhost).")
    if parts.username or parts.password:
        raise ValueError("Base URL must not contain credentials.")
    return url


def set_runtime(
    *, api_key=None, base_url=None, model=None, stage_models=None, save_to_env: bool = False
) -> None:
    """Validate and apply in-process overrides (None leaves a field alone). ``save_to_env`` also writes the .env file."""
    update: dict = {}
    if api_key is not None:
        if (
            not isinstance(api_key, str)
            or not api_key.strip()
            or len(api_key) > 500
            or re.search(r"\s", api_key.strip())
        ):
            raise ValueError("API key must be a single non-empty string.")
        update["api_key"] = api_key.strip()
    if base_url is not None:
        if not isinstance(base_url, str):
            raise ValueError("Base URL must be a string.")
        update["base_url"] = validate_base_url(base_url)
        # The saved key is sent to the base URL's host. Moving it to another host needs an explicit new key.
        if (
            _host_of(update["base_url"]) != _host_of(current()["base_url"])
            and api_key is None
            and current()["api_key"]
        ):
            raise ValueError(
                "Changing the API host requires a new API key in the same request; the saved key is not sent to a new host."
            )
    if model is not None:
        if not isinstance(model, str) or not model.strip() or len(model.strip()) > 100:
            raise ValueError("Model must be 1-100 characters.")
        update["model"] = model.strip()
    if stage_models is not None:
        if isinstance(stage_models, str):
            stage_models = parse_stage_models(stage_models.strip() or "{}")
        if not isinstance(stage_models, dict) or len(stage_models) > 20:
            raise ValueError("stage_models must be an object {stage: model}.")
        clean = {str(k): str(v).strip() for k, v in stage_models.items() if str(v).strip()}
        if any(len(v) > 100 for v in clean.values()):
            raise ValueError("Stage model names are limited to 100 characters.")
        update["stage_models"] = clean
    with _runtime_lock:
        _runtime.update(update)
    if save_to_env:
        _write_env(update)


def _host_of(url: str) -> str:
    """Host that receives the key: the provider default when the base URL is empty."""
    return (urlsplit(url).hostname or "") if url else "api.openai.com"


def clear_runtime() -> None:
    with _runtime_lock:
        _runtime.clear()


def _write_env(update: dict) -> None:
    names = {"api_key": "OPENAI_API_KEY", "base_url": "OPENAI_BASE_URL", "model": "OPENAI_MODEL"}
    values = {names[k]: v for k, v in update.items() if k in names}
    if "stage_models" in update:
        values["HARNESS_STAGE_MODELS"] = json.dumps(update["stage_models"]) if update["stage_models"] else ""
    path = paths.env_file()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    seen: set[str] = set()
    out = []
    for line in lines:
        key = line.partition("=")[0].strip().removeprefix("export ").strip()
        if key in values:
            if key not in seen and values[key]:
                out.append(f"{key}={_quote(values[key])}")
            seen.add(key)
        else:
            out.append(line)
    out += [f"{k}={_quote(v)}" for k, v in values.items() if k not in seen and v]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write("\n".join(out) + "\n")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:  # Windows: ACLs, not modes
        pass


def _quote(value: str) -> str:
    if "'" in value or "\n" in value or "\r" in value:
        raise ValueError("Values saved to .env cannot contain quotes or newlines.")
    return f"'{value}'" if re.search(r"""[\s#"]""", value) else value


# Wall-clock limit for each host-run setup/test command.
EXECUTION_TIMEOUT_SECONDS = float(os.getenv("HARNESS_EXECUTION_TIMEOUT", "900"))
MODEL_TIMEOUT_SECONDS = max(1, float(os.getenv("HARNESS_MODEL_TIMEOUT_SECONDS", "300")))


def validate_gateway_settings() -> None:
    cur = current()
    if not cur["api_key"]:
        raise ValueError(
            "Set an API key (Configuration > Model & API key, or OPENAI_API_KEY) before starting the workflow."
        )
    if not cur["model"]:
        raise ValueError("Set OPENAI_MODEL before starting the workflow.")
    if SKILL_MODE not in {"progressive", "preloaded"}:
        raise ValueError("HARNESS_SKILL_MODE must be 'progressive' or 'preloaded'.")

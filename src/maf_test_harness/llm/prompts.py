"""Prompt assembly for prefix caching.

Providers (DeepSeek, OpenAI and compatible gateways) cache a request's *prefix*: the longest run of leading tokens
identical to an earlier request is billed at a fraction of the price. A stage prompt is a JSON object, so key order decides
how much of it repeats between two calls. Content that is the same for every call of a stage (the assignment, the output
schema, the rules, approved artifacts) goes first, content that changes per call (this batch's cases and source, repair
feedback) goes last. Nothing about the meaning of the prompt changes; only the order of its keys.

Two calls of the same stage then share everything up to the first per-call key, and a repair retry of the same call shares
everything except the repair note.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

# Keys, most stable first. A key not listed keeps its insertion order after the listed ones.
STAGE_ORDER = (
    "assignment",
    "output_contract",
    "json_rules",
    "repository",
    "stack_profile",
    "live_app",
    "approved_inputs",
    "repository_evidence",
    "evidence_file_paths",
    "revision_feedback",
    "repair_feedback",
)
GENERATION_ORDER = (
    "assignment",
    "output_contract",
    "json_rules",
    "approved_inputs",  # its own keys are ordered stable-first too (see GENERATION_INPUT_ORDER)
    "repository",
    "target_source",
    "collaborator_signatures",
    "repository_evidence",
    "revision_feedback",
    "repair_feedback",
)
GENERATION_INPUT_ORDER = ("test_plan", "generation_policy", "requirements", "batch", "approved_cases")


def ordered(sections: Mapping[str, Any], order: Sequence[str]) -> dict[str, Any]:
    """``sections`` with the keys of ``order`` first (in that order), then the rest as inserted."""
    first = {key: sections[key] for key in order if key in sections}
    return {**first, **{key: value for key, value in sections.items() if key not in first}}


def build_prompt(sections: Mapping[str, Any], order: Sequence[str] = STAGE_ORDER) -> str:
    """The compact JSON prompt with stable content first."""
    return json.dumps(ordered(sections, order), separators=(",", ":"))


def generation_prompt(sections: Mapping[str, Any]) -> str:
    sections = dict(sections)
    if isinstance(sections.get("approved_inputs"), Mapping):
        sections["approved_inputs"] = ordered(sections["approved_inputs"], GENERATION_INPUT_ORDER)
    return build_prompt(sections, GENERATION_ORDER)


def shared_prefix_chars(first: str, second: str) -> int:
    """How many leading characters two prompts have in common (the most a prefix cache can serve)."""
    return len(os.path.commonprefix([first, second]))


def prefix_ratio(first: str, second: str) -> float:
    longest = max(len(first), len(second))
    return round(100 * shared_prefix_chars(first, second) / longest, 1) if longest else 0.0


__all__ = [
    "GENERATION_INPUT_ORDER",
    "GENERATION_ORDER",
    "STAGE_ORDER",
    "build_prompt",
    "generation_prompt",
    "ordered",
    "prefix_ratio",
    "shared_prefix_chars",
]

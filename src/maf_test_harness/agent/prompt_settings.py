"""User-owned prompt templates; snapshots never change historical model requests."""

from __future__ import annotations

import json
from pathlib import Path

from .. import paths
from ..storage import atomic_json
from .personas import PERSONAS

REVIEWER_KEYS = tuple(key for key in PERSONAS if key.endswith("_reviewer"))


def _builtin_personas() -> dict[str, str]:
    return {stage: persona.instructions() for stage, persona in PERSONAS.items()}


def defaults() -> dict:
    example = paths.prompts_dir() / "repository-review.md"
    persona_file = paths.prompts_dir() / "personas.json"
    personas = _builtin_personas()
    if persona_file.is_file():
        # A user file from before a stage existed (for example the reviewer personas) keeps its own texts and gets the
        # built-in text for every missing stage; an unknown stage or an empty text is still refused.
        stored = json.loads(persona_file.read_text(encoding="utf-8"))
        if not isinstance(stored, dict) or set(stored) - set(PERSONAS):
            raise ValueError("Persona file may contain only configured stages.")
        personas.update(stored)
    if not all(isinstance(value, str) and value.strip() for value in personas.values()):
        raise ValueError(
            "Default persona file must contain every configured stage with nonempty instructions."
        )
    return {
        "repository_prompt": example.read_text(encoding="utf-8") if example.is_file() else "",
        "personas": personas,
        "overrides": {},
    }


def _targets_reviewer(key: str) -> bool:
    """True for an override that would replace a reviewer call: a reviewer stage, or a reviewer batch (review-, plan-review-,
    recheck-) of any stage. Reviewer answers are judged against the host's own prompt, so they cannot be overridden."""
    stage, _, batch = str(key).partition(":")
    return stage in REVIEWER_KEYS or batch.startswith(("review-", "plan-review-", "recheck-"))


def validate(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - {"repository_prompt", "personas", "overrides"}:
        raise ValueError("Prompt settings must contain repository_prompt, personas and overrides only.")
    result = {**defaults(), **value}
    if not isinstance(result["repository_prompt"], str) or len(result["repository_prompt"]) > 12000:
        raise ValueError("Repository prompt must be text up to 12,000 characters.")
    for field in ("personas", "overrides"):
        values = result[field]
        if not isinstance(values, dict):
            raise ValueError(f"{field} must be a prompt mapping.")
        for key, text in values.items():
            if field == "overrides" and _targets_reviewer(key):
                raise ValueError("Whole-prompt overrides cannot target a confidence reviewer.")
            if (
                (key not in PERSONAS if field == "personas" else key.split(":")[0] not in PERSONAS)
                or not isinstance(text, str)
                or not text.strip()
                or len(text) > 524288
            ):
                raise ValueError("Unknown stage or invalid prompt text (maximum 524,288 characters).")
    result["personas"] = {**defaults()["personas"], **result["personas"]}
    return result


def load(path: Path) -> dict:
    """Stored settings. An override that targets a reviewer (written before the restriction) is dropped, not fatal."""
    if not path.is_file():
        return defaults()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("overrides"), dict):
        raw = {**raw, "overrides": {k: v for k, v in raw["overrides"].items() if not _targets_reviewer(k)}}
    return validate(raw)


def save(path: Path, value: dict) -> dict:
    clean = validate(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, clean)  # unique staging name, Windows-lock retry
    return clean

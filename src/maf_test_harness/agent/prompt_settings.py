"""User-owned prompt templates; snapshots never change historical model requests."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .. import paths
from .personas import PERSONAS


def defaults() -> dict:
    example = paths.prompts_dir() / "repository-review.md"
    persona_file = paths.prompts_dir() / "personas.json"
    personas = (
        json.loads(persona_file.read_text())
        if persona_file.is_file()
        else {stage: persona.instructions() for stage, persona in PERSONAS.items()}
    )
    if set(personas) != set(PERSONAS) or not all(
        isinstance(value, str) and value.strip() for value in personas.values()
    ):
        raise ValueError(
            "Default persona file must contain every configured stage with nonempty instructions."
        )
    return {
        "repository_prompt": example.read_text() if example.is_file() else "",
        "personas": personas,
        "overrides": {},
    }


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
    return validate(json.loads(path.read_text())) if path.is_file() else defaults()


def save(path: Path, value: dict) -> dict:
    clean = validate(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    return clean

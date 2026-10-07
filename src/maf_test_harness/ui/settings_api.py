"""Focused settings api operations."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..agent import prompt_settings
from ..agent.personas import PERSONAS
from ..llm.usage import DailyUsageLedger
from ..settings import API_KEY, BASE_URL, CASES_PER_REQUIREMENT, MODEL
from ..workflow import STAGES
from .presentation import public

ROOT = Path(__file__).resolve().parents[3]


class SettingsAPI:
    def prompts(self, key: str | None = None, record_id: str | None = None) -> dict:
        with self.lock:
            folder = self.run_path(key) if key else self.runs.parent
            if record_id:
                if not re.fullmatch(r"[a-f0-9]{32}", record_id):
                    raise ValueError("Invalid prompt record ID")
                path = folder / "prompts" / f"{record_id}.json"
                if not path.is_file():
                    raise FileNotFoundError("Prompt record unavailable")
                return public(json.loads(path.read_text(encoding="utf-8")))
            settings = prompt_settings.load(folder / "prompts.json")
            records = self.read(key)["state"].get("prompt_records", []) if key else []
            return public(
                {
                    "settings": settings,
                    "defaults": prompt_settings.defaults(),
                    "records": records,
                    "host_stages": ["repo_analysis", "environment_setup", "test_execution"],
                }
            )

    def save_prompts(self, body: dict, key: str | None = None) -> dict:
        with self.lock:
            if key and self.busy():
                raise RuntimeError(
                    "Wait for the current operation to finish before changing this run's prompts."
                )
            folder = self.run_path(key) if key else self.runs.parent
            current = prompt_settings.load(folder / "prompts.json")
            if set(body) == {"override_key", "override_prompt"}:
                key_name, text = body["override_key"], body["override_prompt"]
                if not isinstance(key_name, str) or key_name.split(":")[0] not in PERSONAS:
                    raise ValueError("Unknown prompt override stage")
                overrides = dict(current["overrides"])
                if text is None:
                    overrides.pop(key_name, None)
                else:
                    overrides[key_name] = text
                body = {"overrides": overrides}
            settings = prompt_settings.save(folder / "prompts.json", {**current, **body})
            if key:
                event = {
                    "event": "prompts_saved",
                    "at": __import__("datetime")
                    .datetime.now(__import__("datetime").timezone.utc)
                    .isoformat(),
                    "stage": "prompt_settings",
                    "sha256": __import__("hashlib")
                    .sha256(json.dumps(settings, sort_keys=True).encode())
                    .hexdigest(),
                }
                with (folder / "audit.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event) + "\n")
            return public({"settings": settings})

    def config(self):
        return public(
            {
                "model": MODEL,
                "case_budget_default": CASES_PER_REQUIREMENT,
                "build_id": os.getenv("HARNESS_BUILD_ID", ""),
                "base_url": BASE_URL,
                "key_configured": bool(API_KEY),
                "run_directory": str(self.runs),
                "service_log_directory": os.getenv("HARNESS_LOG_DIR") or str(self.runs.parent / "logs"),
                "daily": DailyUsageLedger().snapshot(),
                "stages": STAGES,
                "personas": {
                    stage: {
                        "name": PERSONAS[stage].name,
                        "role": PERSONAS[stage].role,
                        "goal": PERSONAS[stage].goal,
                    }
                    for stage in STAGES
                },
            }
        )

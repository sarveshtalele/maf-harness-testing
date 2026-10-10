"""Focused settings api operations."""

from __future__ import annotations

import json
import os
import re

from .. import settings
from ..agent import prompt_settings
from ..agent.personas import PERSONAS
from ..llm.usage import DailyUsageLedger
from ..settings import CASES_PER_REQUIREMENT
from ..workflow import STAGES
from .presentation import public


def _probe(cur: dict) -> None:
    """Tiny connectivity check: list models, else a 1-token completion (20 s cap, no retries)."""
    from openai import OpenAI

    client = OpenAI(api_key=cur["api_key"], base_url=cur["base_url"] or None, timeout=20, max_retries=0)
    try:
        next(iter(client.models.list()), None)
    except Exception:  # some gateways do not expose /models
        client.chat.completions.create(
            model=cur["model"], messages=[{"role": "user", "content": "ping"}], max_tokens=1
        )


class SettingsAPI:
    model_probe = None  # tests inject a fake

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

    @staticmethod
    def _model_view() -> dict:
        cur = settings.current()
        key = cur["api_key"]
        return {
            "base_url": cur["base_url"],
            "model": cur["model"],
            "stage_models": cur["stage_models"],
            "key_configured": bool(key),
            "key_hint": key[-4:] if len(key) >= 8 else ("" if not key else "****"),
            "source": cur["source"],
        }

    def model_settings(self) -> dict:
        return self._model_view()

    def save_model_settings(self, body: dict) -> dict:
        with self.lock:
            if self.busy():
                raise RuntimeError("Wait for the current operation to finish before changing model settings.")
            unknown = set(body) - {"base_url", "model", "stage_models", "api_key", "save_to_env"}
            if unknown:
                raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
            settings.set_runtime(
                api_key=body.get("api_key") or None,  # empty string keeps the current key
                base_url=body.get("base_url"),
                model=body.get("model"),
                stage_models=body.get("stage_models"),
                save_to_env=body.get("save_to_env") is True,
            )
            return self._model_view()

    def test_model_settings(self) -> dict:
        cur = settings.current()
        if not cur["api_key"]:
            return {"ok": False, "message": "No API key is configured."}
        try:
            (self.model_probe or _probe)(cur)
        except Exception as exc:  # message is redacted: never echo the key
            return {"ok": False, "message": public(f"{type(exc).__name__}: {str(exc)[:300]}")}
        return {"ok": True, "message": "Connection works."}

    def config(self):
        cur = settings.current()
        return public(
            {
                "model": cur["model"],
                "case_budget_default": CASES_PER_REQUIREMENT,
                "build_id": os.getenv("HARNESS_BUILD_ID", ""),
                "base_url": cur["base_url"],
                "key_configured": bool(cur["api_key"]),
                "key_source": cur["source"],
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

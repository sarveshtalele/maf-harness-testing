"""Focused settings api operations."""

from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit

from .. import settings
from ..agent import prompt_settings
from ..agent.personas import PERSONAS
from ..llm.usage import DailyUsageLedger
from ..settings import CASES_PER_REQUIREMENT
from ..storage import prompt_log_records
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


def _status_code(exc: Exception) -> int | None:
    """HTTP status of a provider error (SDK attribute, its response, or a code quoted in the text)."""
    code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(code, int):
        return code
    match = re.search(r"(?:code|status)[: ]+(401|403|404|429)\b", str(exc)[:300], re.IGNORECASE)
    return int(match.group(1)) if match else None


def _friendly_error(exc: Exception, host: str) -> str:
    """Plain sentence for the common failure modes; the raw text goes in the details field instead."""
    name = type(exc).__name__
    code = _status_code(exc)
    if code in {401, 403}:
        return "The API key was rejected by the gateway."
    if code == 404:
        return "The model or endpoint was not found — check the model name and base URL."
    if code == 429:
        return "The gateway's quota or rate limit is exhausted."
    if name in {"APIConnectionError", "APITimeoutError", "ConnectError", "TimeoutException"} or isinstance(
        exc, (ConnectionError, TimeoutError, OSError)
    ):
        return f"Could not reach the gateway at {host} — check the URL, network or VPN."
    return "The gateway returned an unexpected error. Check the model, base URL and key, then test again."


class SettingsAPI:
    model_probe = None  # tests inject a fake

    def _prompt_records(self, key: str) -> list:
        """The run's prompt log, de-duplicated by id; legacy records kept in run.json's state are included."""
        return prompt_log_records(self.run_path(key))

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
            records = self._prompt_records(key) if key else []
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

    def test_model_settings(self, body: dict | None = None) -> dict:
        """Probe the typed (not saved) base URL, model and key; nothing is saved or logged. Empty key = saved key."""
        body = body or {}
        unknown = set(body) - {"base_url", "model", "api_key"}
        if unknown:
            raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
        for field in ("base_url", "model", "api_key"):
            if body.get(field) is not None and not isinstance(body[field], str):
                raise ValueError(f"{field} must be text.")
        typed_key = (body.get("api_key") or "").strip() or None
        if typed_key is not None and (len(typed_key) > 500 or re.search(r"\s", typed_key)):
            raise ValueError("API key must be a single non-empty string.")
        base_url = settings.validate_base_url(body["base_url"]) if body.get("base_url") is not None else None
        model = body.get("model")
        if model is not None and (not model.strip() or len(model.strip()) > 100):
            raise ValueError("Model must be 1-100 characters.")
        cur = settings.current()
        if not typed_key and "base_url" in body and (base_url or "") != (cur["base_url"] or ""):
            # The saved key is never sent to a host the user did not save it for (an empty URL = provider default host).
            return {"ok": False, "message": "Enter the API key to test a different base URL."}
        cur = {
            **cur,
            "api_key": typed_key or cur["api_key"],
            "base_url": base_url if base_url is not None else cur["base_url"],
            "model": model.strip() if model else cur["model"],
        }
        if not cur["api_key"]:
            return {"ok": False, "message": "No API key is configured."}
        host = urlsplit(cur["base_url"]).hostname or "api.openai.com"
        try:
            (self.model_probe or _probe)(cur)
        except Exception as exc:  # the detail is redacted: never echo the key
            detail = str(exc)
            if typed_key:  # a typed key is not in the saved settings: redact it before truncating
                detail = detail.replace(typed_key, "[REDACTED]")
            return {
                "ok": False,
                "message": _friendly_error(exc, host),
                "detail": public(f"{type(exc).__name__}: {detail[:300]}"),
            }
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

"""Optional OpenTelemetry export for the MAF spans the framework already emits.

Agent Framework instruments agents, chat clients, tools and workflows by default (GenAI semantic conventions) and leaves the
providers and exporters to the application: ``configure_otel_providers()`` once at start-up, or the standard ``OTEL_*``
environment variables. The harness's own record of a run is ``trace.jsonl`` (see ``trace.py``) and needs none of this; this
module only lets an operator also send the framework's spans to a console or a collector.

``HARNESS_OTEL``: ``off`` (default) | ``console`` | ``otlp`` (endpoint from ``OTEL_EXPORTER_OTLP_ENDPOINT``).
Message content (prompts, responses, tool arguments) is never captured: sensitive-data capture stays off.
"""

from __future__ import annotations

import os
from typing import Any

_configured: dict[str, Any] | None = None


def configure_telemetry(mode: str | None = None) -> dict[str, Any]:
    """Configure MAF OpenTelemetry once per process and report what happened (never raises)."""
    global _configured
    if _configured is not None:
        return _configured
    mode = (mode or os.getenv("HARNESS_OTEL", "off")).strip().lower()
    status: dict[str, Any] = {"mode": mode, "enabled": False, "sensitive_data": False, "reason": ""}
    if mode == "off":
        status["reason"] = "HARNESS_OTEL=off: MAF instrumentation is on, but no exporter is configured"
    elif mode not in {"console", "otlp"}:
        status["reason"] = f"unknown HARNESS_OTEL value '{mode}' (use off, console or otlp)"
    else:
        try:
            from agent_framework.observability import configure_otel_providers

            configure_otel_providers(enable_console_exporters=mode == "console", enable_sensitive_data=False)
            status["enabled"] = True
        except ImportError as exc:
            status["reason"] = f"{exc}. Install the SDK: pip install opentelemetry-sdk" + (
                " opentelemetry-exporter-otlp" if mode == "otlp" else ""
            )
        except Exception as exc:  # exporter/endpoint misconfiguration must not stop a run
            status["reason"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    _configured = status
    return status

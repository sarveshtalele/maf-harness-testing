"""Shared secret redaction at the UI presentation boundary."""

import re

from ..hooks.builtin import redact


def public(value):
    """Redact secrets on the presentation boundary without changing checkpoint hashes."""
    if isinstance(value, str):
        return redact(value)[0]
    if isinstance(value, list):
        return [public(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.search(r"(?i)^(api_key|password|secret|access_token)$", key)
            else public(item)
            for key, item in value.items()
        }
    return value

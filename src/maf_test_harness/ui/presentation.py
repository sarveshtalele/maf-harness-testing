"""Shared secret redaction at the UI presentation boundary."""

import re

from ..hooks.builtin import redact

# Scalars only: token counters (input_tokens, tokens) and status maps (secrets) are not credentials.
_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|password|secret|token|authorization|bearer)")


def public(value):
    """Redact secrets on the presentation boundary without changing checkpoint hashes."""
    if isinstance(value, str):
        return redact(value)[0]
    if isinstance(value, list):
        return [public(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if isinstance(key, str)
            and _SECRET_KEY.search(key)
            and not isinstance(item, dict | list | bool | int | float)
            else public(item)
            for key, item in value.items()
        }
    return value

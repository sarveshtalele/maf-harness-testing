"""Shared secret redaction at the UI presentation boundary."""

import re

from ..hooks.builtin import redact

# Scalars only: token counters (input_tokens, tokens) and status maps (secrets) are not credentials.
_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|password|secret|token|authorization|bearer)")


def _secret_value(item) -> bool:
    """A value under a credential-like key is hidden unless it is empty, structural, numeric or a count string."""
    if item is None or isinstance(item, dict | list | bool | int | float):
        return False
    return not (isinstance(item, str) and item.isdigit())  # "12" is a limit or count, not a credential


def public(value):
    """Redact secrets on the presentation boundary without changing checkpoint hashes."""
    if isinstance(value, str):
        return redact(value)[0]
    if isinstance(value, list):
        return [public(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if isinstance(key, str) and _SECRET_KEY.search(key) and _secret_value(item)
            else public(item)
            for key, item in value.items()
        }
    return value

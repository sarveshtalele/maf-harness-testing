"""UTF-8 state storage resilient to short Windows sharing/antivirus locks."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4


def _retry(operation):
    for attempt in range(40):
        try:
            return operation()
        except PermissionError as exc:
            transient = getattr(exc, "winerror", None) in {5, 32, 33} or (os.name == "nt" and exc.errno == 13)
            if not transient or attempt == 39:
                raise
            time.sleep(0.05)
    # Unreachable: the last attempt returns or raises. Explicit so control flow never falls through.
    raise RuntimeError("Retry budget exhausted without a result.")


retry = _retry  # public name for modules outside storage; _retry stays for existing callers


def read_json(path: Path):
    return json.loads(_retry(lambda: path.read_text(encoding="utf-8")))


def atomic_json(path: Path, data: dict):
    # Unique staging files prevent a second writer from overwriting the first writer's staging data.
    temp = path.with_name(f"{path.stem}-{uuid4().hex}.tmp")
    _retry(lambda: temp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8"))
    if os.name != "nt":
        os.chmod(temp, 0o600)
    _retry(lambda: temp.replace(path))

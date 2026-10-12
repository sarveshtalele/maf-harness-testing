"""UTF-8 state storage resilient to short Windows sharing/antivirus locks."""

import json
import os
import stat
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


def read_regular_file(path: Path) -> bytes:
    """Bytes of a regular file, never following a symlink at the final component.

    POSIX: open with O_NOFOLLOW, then check that the opened descriptor is the object the path names now (same device
    and inode), so a file swapped for a link between the check and the read is refused. Windows has no O_NOFOLLOW:
    a symlink check on the path is the best available fallback there.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"{path.name} is not a regular file")
        return path.read_bytes()
    fd = os.open(path, os.O_RDONLY | nofollow)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        named = os.lstat(path)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise FileNotFoundError(f"{path.name} changed while it was being read")
        return stream.read()


def read_json(path: Path):
    return json.loads(_retry(lambda: path.read_text(encoding="utf-8")))


def atomic_json(path: Path, data: dict):
    # Unique staging files prevent a second writer from overwriting the first writer's staging data.
    temp = path.with_name(f"{path.stem}-{uuid4().hex}.tmp")
    _retry(lambda: temp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8"))
    if os.name != "nt":
        os.chmod(temp, 0o600)
    _retry(lambda: temp.replace(path))

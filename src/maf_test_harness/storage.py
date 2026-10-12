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


def append_jsonl(path: Path, record: dict) -> None:
    """Append one record as a JSON line. Logs grow per model call, so they live beside the run, not in its state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, default=str) + "\n")


def read_jsonl(path: Path) -> list:
    """Every record in a JSON-lines log; a torn last line from a crash is skipped."""
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


PROMPT_LOG = "prompt_records.jsonl"


def _legacy_prompt_records(run_dir: Path) -> list[dict]:
    """Records that older runs kept inside run.json's checkpointed state (before the JSONL log existed)."""
    path = run_dir / "run.json"
    if not path.is_file():
        return []
    try:
        records = (json.loads(path.read_text(encoding="utf-8")).get("state") or {}).get("prompt_records")
    except (OSError, ValueError, AttributeError):
        return []
    return [r for r in records if isinstance(r, dict)] if isinstance(records, list) else []


def append_prompt_record(run_dir: Path, record: dict) -> None:
    """Append one prompt record. The first write to a log seeds it from a legacy run.json list, so resumed legacy runs
    keep their history in the log; readers de-duplicate by id (see ``prompt_log_records``)."""
    log = run_dir / PROMPT_LOG
    if not log.exists():
        for legacy in _legacy_prompt_records(run_dir):
            append_jsonl(log, legacy)
    append_jsonl(log, record)


def prompt_log_records(run_dir: Path) -> list[dict]:
    """Every prompt record of a run, oldest first, each id once (legacy run.json records first, then the log)."""
    seen: set[str] = set()
    records: list[dict] = []
    for record in [*_legacy_prompt_records(run_dir), *read_jsonl(run_dir / PROMPT_LOG)]:
        if isinstance(record, dict) and record.get("id") is not None:
            if record["id"] in seen:
                continue
            seen.add(record["id"])
        records.append(record)
    return records


def read_json(path: Path):
    return json.loads(_retry(lambda: path.read_text(encoding="utf-8")))


def atomic_json(path: Path, data: dict):
    # Unique staging files prevent a second writer from overwriting the first writer's staging data.
    temp = path.with_name(f"{path.stem}-{uuid4().hex}.tmp")
    _retry(lambda: temp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8"))
    if os.name != "nt":
        os.chmod(temp, 0o600)
    _retry(lambda: temp.replace(path))

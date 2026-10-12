"""Fail when a credential-shaped string is in the repository tree: provider keys (sk-, gh*_), AWS access key ids,
Slack tokens and PEM private-key headers. Stdlib only, so the ci secrets-scan job runs it without installing anything.

    python3 scripts/secrets_scan.py [ROOT]

Test fixtures must split such strings in source (for example "AKIA" + "...") so the scan does not flag them.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN ((RSA|EC|DSA|OPENSSH|PGP) )?PRIVATE KEY-----"
)
SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", ".next", "__pycache__"}
SKIP_FILES = {"uv.lock"}  # hashes, not credentials


def _candidate_files(root: Path) -> list[Path]:
    """Tracked files when root is a git work tree (ignored caches such as .pytest_cache are not part of the
    repository); otherwise every file outside the skipped folders."""
    try:
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        listed = None
    if listed is not None:
        return [root / name for name in listed.decode("utf-8", "replace").split("\0") if name]
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        found.extend(Path(dirpath) / name for name in filenames)
    return found


def find(root: Path) -> list[str]:
    """`path:line` for every text line that matches PATTERN."""
    hits: list[str] = []
    for path in _candidate_files(root):
        if path.name in SKIP_FILES:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8000]:  # binary files are skipped, as grep -I does
            continue
        for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if PATTERN.search(line):
                hits.append(f"{path.relative_to(root).as_posix()}:{number}")
    return hits


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]).resolve() if args else ROOT
    hits = find(root)
    for hit in hits:
        print(f"credential-shaped string: {hit}")
    if hits:
        print("credential-shaped string found")
        return 1
    print("secrets scan: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())

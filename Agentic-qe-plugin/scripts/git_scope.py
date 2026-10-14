"""Read-only Git change scope with immutable revisions and path hashes."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> bytes:
    allowed = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"}
    }
    allowed["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, env=allowed, timeout=30, check=False
    )
    if proc.returncode:
        raise ValueError(
            f"Git scope command failed: {' '.join(args[:2])}: {proc.stderr.decode(errors='replace')[:300]}"
        )
    return proc.stdout


def _path(repo: Path, relative: str) -> Path:
    if not relative or "\x00" in relative or Path(relative).is_absolute():
        raise ValueError("Git returned an invalid path")
    path = repo / relative
    if not path.resolve().is_relative_to(repo.resolve()) or path.is_symlink():
        raise ValueError(f"Git path leaves repository or is a symlink: {relative}")
    return path


def _entries(repo: Path, data: bytes, group: str, revision: str | None = None) -> list[dict]:
    tokens = [part.decode("utf-8", errors="surrogateescape") for part in data.split(b"\x00") if part]
    entries = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if not status or status[0] not in "ACDMRTUXB":
            raise ValueError("Git returned an unsupported change status")
        needed = 2 if status[0] in {"R", "C"} else 1
        if index + needed > len(tokens):
            raise ValueError("Git returned an incomplete change record")
        paths = tokens[index : index + needed]
        index += needed
        for relative in paths:
            _path(repo, relative)
        current = paths[-1]
        file = _path(repo, current)
        if status[0] == "D":
            content_hash = None
        elif revision:
            spec = f":{current}" if revision == ":" else f"{revision}:{current}"
            content_hash = hashlib.sha256(_git(repo, "show", spec)).hexdigest()
        else:
            content_hash = hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None
        entries.append(
            {
                "path": current,
                "previous_path": paths[0] if needed == 2 else None,
                "status": status,
                "group": group,
                "sha256": content_hash,
            }
        )
    return entries


def capture(
    repo: Path, mode: str, base: str | None = None, target: str = "HEAD", include_untracked: bool = False
) -> dict:
    repo = Path(repo).resolve()
    if not repo.is_dir() or _git(repo, "rev-parse", "--show-toplevel").decode().strip() != str(repo):
        raise ValueError("Select the root of a local Git repository")
    head = _git(repo, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if mode == "committed":
        if not base:
            raise ValueError("A base ref is required for committed diff scope")
        base_sha = _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").decode().strip()
        target_sha = _git(repo, "rev-parse", "--verify", f"{target}^{{commit}}").decode().strip()
        merge_base = _git(repo, "merge-base", base_sha, target_sha).decode().strip()
        entries = _entries(
            repo,
            _git(repo, "diff", "--name-status", "-z", "-M", merge_base, target_sha, "--"),
            "committed",
            target_sha,
        )
        revision = {"base": base_sha, "merge_base": merge_base, "target": target_sha}
    elif mode == "working-tree":
        entries = _entries(
            repo, _git(repo, "diff", "--cached", "--name-status", "-z", "-M", "--"), "staged", ":"
        )
        entries += _entries(repo, _git(repo, "diff", "--name-status", "-z", "-M", "--"), "unstaged")
        if include_untracked:
            for raw in _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\x00"):
                if raw:
                    relative = raw.decode("utf-8", errors="surrogateescape")
                    file = _path(repo, relative)
                    if file.is_file():
                        entries.append(
                            {
                                "path": relative,
                                "previous_path": None,
                                "status": "?",
                                "group": "untracked",
                                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                            }
                        )
        revision = {
            "base": head,
            "target": head,
            "index_sha256": hashlib.sha256(_git(repo, "ls-files", "--stage", "-z")).hexdigest(),
        }
    else:
        raise ValueError("Scope mode must be committed or working-tree")
    if not entries:
        raise ValueError("Selected Git scope contains no changed files")
    return {
        "mode": mode,
        "repo": str(repo),
        "revision": revision,
        "include_untracked": include_untracked,
        "changed_files": entries,
        "related_files": [],
    }

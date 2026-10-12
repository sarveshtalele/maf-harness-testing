"""Fail when the release version disagrees across the six version files, or a git tag does not match it.

Checked fields: pyproject.toml; uv.lock (the editable maf-test-harness entry); frontend/package.json and
desktop/package.json; and the root and packages[""] versions of frontend/package-lock.json and
desktop/package-lock.json. A tag must be exactly vX.Y.Z or vX.Y.Z-rcN.
"""

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path

TAG_RE = re.compile(r"v(\d+\.\d+\.\d+)(-rc\d+)?")


def _json(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def versions(root: Path) -> dict[str, str]:
    """Every version field a release touches, keyed by where it lives."""
    found = {
        "pyproject.toml": tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    }
    lock = tomllib.loads((root / "uv.lock").read_text("utf-8"))
    ours = [
        p
        for p in lock.get("package", [])
        if p.get("name") == "maf-test-harness" and p.get("source") == {"editable": "."}
    ]
    if len(ours) != 1:
        raise ValueError("uv.lock must have exactly one editable maf-test-harness entry")
    found["uv.lock"] = ours[0]["version"]
    for folder in ("frontend", "desktop"):
        found[f"{folder}/package.json"] = _json(root / folder / "package.json")["version"]
        lock_json = _json(root / folder / "package-lock.json")
        found[f"{folder}/package-lock.json"] = lock_json["version"]
        found[f'{folder}/package-lock.json packages[""]'] = lock_json["packages"][""]["version"]
    return found


def check(root: Path) -> str | None:
    try:
        found = versions(root)
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        return f"cannot read the version files: {exc}"
    expected = found["pyproject.toml"]
    stale = {name: value for name, value in found.items() if value != expected}
    if stale:
        listed = " ".join(f"{name}={value}" for name, value in found.items())
        return f"version mismatch (pyproject.toml={expected}): {listed}"
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag = os.environ.get("GITHUB_REF_NAME", "")
        match = TAG_RE.fullmatch(tag)
        if not match:
            return f"tag {tag!r} must look like v{expected} or v{expected}-rcN"
        if match.group(1) != expected:
            return f"tag {tag} does not match version v{expected} (v{expected}-rcN also accepted)"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    err = check(ap.parse_args().root)
    if err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

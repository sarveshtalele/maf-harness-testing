"""Fail if pyproject and frontend versions differ, or a release tag mismatches."""

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path


def check(root: Path) -> str | None:
    py = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    js = json.loads((root / "frontend" / "package.json").read_text("utf-8"))["version"]
    desk = json.loads((root / "desktop" / "package.json").read_text("utf-8"))["version"]
    if py != js or py != desk:
        return f"version mismatch: pyproject.toml={py} frontend/package.json={js} desktop/package.json={desk}"
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag = os.environ.get("GITHUB_REF_NAME", "")
        if tag != f"v{py}" and tag.split("-rc")[0] != f"v{py}":
            return f"tag {tag} does not match version v{py} (v{py}-rcN also accepted)"
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

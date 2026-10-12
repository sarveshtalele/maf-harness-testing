"""Run the merge gate (CLAUDE.md, "Gate before merge") in order and print one summary.

    python scripts/check_all.py [--skip-ui] [--skip-desktop]

Run it with the project interpreter: `uv run python scripts/check_all.py`, or .venv/bin/python on macOS and Linux and
.venv\\Scripts\\python.exe on Windows. Install first: `uv sync --locked --extra dev`, then `npm ci --include=dev` in
frontend/ (Node 24) and `npm ci` in desktop/. Every step runs even after an earlier failure, so the summary lists them
all. The exit code is 0 only when every step that ran passed.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
NPM = "npm.cmd" if os.name == "nt" else "npm"  # same resolution as scripts/build_desktop.py


@dataclass
class Step:
    name: str
    argv: list[str]
    group: str = "core"  # core | ui | desktop: --skip-ui and --skip-desktop drop a group
    cwd: Path = ROOT
    env: dict[str, str] = field(default_factory=dict)


def _pythonpath() -> str:
    """The repository's src first, then any PYTHONPATH already set; empty entries are dropped."""
    parts = [str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]
    return os.pathsep.join(part for part in parts if part)


def _free_port() -> str:
    """A free local port, so parallel checkouts running Playwright never collide."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return str(probe.getsockname()[1])


def steps() -> list[Step]:
    py_env = {"PYTHONPATH": _pythonpath()}
    ui_env = {
        "PW_API_PORT": os.environ.get("PW_API_PORT") or _free_port(),
        "PW_UI_PORT": os.environ.get("PW_UI_PORT") or _free_port(),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "dummy-key-for-ui-tests"),
        "HARNESS_TEST_PYTHON": PY,
        "PYTHONPATH": _pythonpath(),
    }
    return [
        Step("ruff check", [PY, "-m", "ruff", "check", "src", "tests", "scripts"], env=py_env),
        Step(
            "ruff format --check",
            [PY, "-m", "ruff", "format", "--check", "src", "tests", "scripts"],
            env=py_env,
        ),
        Step("architecture map current", [PY, "scripts/gen_architecture.py", "--check"], env=py_env),
        Step("version files agree", [PY, "scripts/check_version.py"], env=py_env),
        Step(
            "skills lint (strict)", [PY, "-m", "maf_test_harness.cli", "lint-skills", "--strict"], env=py_env
        ),
        Step("pytest", [PY, "-m", "pytest", "-q", "--ignore=tests/targets"], env=py_env),
        Step("frontend typecheck", [NPM, "run", "typecheck"], "ui", ROOT / "frontend"),
        Step("frontend build", [NPM, "run", "build"], "ui", ROOT / "frontend"),
        Step("frontend playwright", [NPM, "test"], "ui", ROOT / "frontend", ui_env),
        Step("desktop shell tests", [NPM, "test"], "desktop", ROOT / "desktop"),
    ]


def run_step(step: Step) -> tuple[str, float, str]:
    """Returns (PASS|FAIL, seconds, detail). Output streams straight to the terminal."""
    print(f"\n=== {step.name}: {' '.join(step.argv)} (in {step.cwd.relative_to(ROOT) or '.'})", flush=True)
    started = time.monotonic()
    try:
        done = subprocess.run(step.argv, cwd=step.cwd, env={**os.environ, **step.env}, check=False)
    except OSError as exc:  # e.g. npm is not installed
        return "FAIL", time.monotonic() - started, f"could not start: {exc}"
    elapsed = time.monotonic() - started
    return ("PASS", elapsed, "") if done.returncode == 0 else ("FAIL", elapsed, f"exit {done.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--skip-ui", action="store_true", help="skip the frontend typecheck, build and Playwright steps"
    )
    parser.add_argument("--skip-desktop", action="store_true", help="skip the desktop shell tests")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    skipped = {group for group, flag in (("ui", args.skip_ui), ("desktop", args.skip_desktop)) if flag}
    results: list[tuple[str, str, float, str]] = []
    for step in steps():
        if step.group in skipped:
            results.append((step.name, "SKIP", 0.0, f"--skip-{step.group}"))
            continue
        status, seconds, detail = run_step(step)
        results.append((step.name, status, seconds, detail))

    width = max(len(name) for name, *_ in results)
    print("\nMerge gate summary", flush=True)
    for name, status, seconds, detail in results:
        timing = f"{seconds:7.1f} s" if status != "SKIP" else "         "
        print(f"  {status:<4}  {name:<{width}}  {timing}  {detail}".rstrip(), flush=True)
    failed = [name for name, status, *_ in results if status == "FAIL"]
    ran = [r for r in results if r[1] != "SKIP"]
    if failed:
        print(f"\nResult: FAILED ({len(failed)} of {len(ran)} steps): {', '.join(failed)}", flush=True)
        return 1
    print(f"\nResult: PASSED ({len(ran)} steps)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

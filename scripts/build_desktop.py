"""Build the desktop installer: PyInstaller backend, Next.js standalone frontend, electron-builder.

uv run python scripts/build_desktop.py [--arch x64|arm64] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NPM = "npm.cmd" if os.name == "nt" else "npm"


def plan(arch: str | None = None) -> list[dict]:
    """Ordered steps: argv, cwd, extra env, and an optional python callable name run after the command."""
    dist = ["run", "dist"] + (["--", f"--{arch}"] if arch else [])
    return [
        {
            "argv": [
                "uv",
                "run",
                "pyinstaller",
                "packaging/backend.spec",
                "--noconfirm",
                "--distpath",
                "build",
                # The default workpath (build/<spec name>) equals the dist folder build/backend: keep them apart.
                "--workpath",
                "build/pyinstaller-work",
            ],
            "cwd": ROOT,
            "env": {},
        },
        {"argv": [NPM, "ci"], "cwd": ROOT / "frontend", "env": {}},
        {
            "argv": [NPM, "run", "build"],
            "cwd": ROOT / "frontend",
            "env": {"DESKTOP_BUILD": "1"},
            "after": "copy_static",
        },
        {"argv": [NPM, "ci"], "cwd": ROOT / "desktop", "env": {}},
        {"argv": [NPM, *dist], "cwd": ROOT / "desktop", "env": {}},
    ]


def copy_static() -> None:
    """Standalone output omits .next/static and public; the Electron resources mapping copies them from here."""
    fe = ROOT / "frontend"
    standalone = fe / ".next" / "standalone"
    if not (standalone / "server.js").is_file():
        raise SystemExit(f"{standalone / 'server.js'} missing; check outputFileTracingRoot in next.config.ts")
    for src, dst in (
        (fe / ".next" / "static", standalone / ".next" / "static"),
        (fe / "public", standalone / "public"),
    ):
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
    stage_frontend(standalone)


STAGED_FRONTEND = ROOT / "build" / "frontend"


def stage_frontend(standalone: Path) -> Path:
    """Copy the standalone server to build/frontend with node_modules renamed to deps.

    electron-builder always drops `node_modules` from extraResources, so the traced dependencies ship as `deps/` and
    the desktop shell points NODE_PATH at it (desktop/src/main.ts).
    """
    if STAGED_FRONTEND.exists():
        shutil.rmtree(STAGED_FRONTEND)
    shutil.copytree(standalone, STAGED_FRONTEND, symlinks=True)
    modules = STAGED_FRONTEND / "node_modules"
    if modules.is_dir():
        modules.rename(STAGED_FRONTEND / "deps")
    return STAGED_FRONTEND


MACHINE_ARCH = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}


def arch_mismatch(arch: str, machine: str) -> str | None:
    """Error text when --arch names a CPU other than this machine's (the bundled Python backend is native code)."""
    actual = MACHINE_ARCH.get(machine.lower())
    if actual == arch:
        return None
    return (
        f"--arch {arch} does not match this machine ({machine}, {actual or 'unknown arch'}): the PyInstaller backend "
        "is native code, so build each architecture on its own runner"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arch", choices=("x64", "arm64"), help="electron-builder arch (default: all configured)"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    args = parser.parse_args(argv)
    if args.arch and (problem := arch_mismatch(args.arch, platform.machine())):
        parser.error(problem)
    for step in plan(args.arch):
        env = " ".join(f"{k}={v}" for k, v in step["env"].items())
        cwd = step["cwd"].relative_to(ROOT).as_posix() or "."
        print(f"[{cwd}] {env + ' ' if env else ''}{shlex.join(step['argv'])}", flush=True)
        if args.dry_run:
            continue
        subprocess.run(step["argv"], cwd=step["cwd"], env={**os.environ, **step["env"]}, check=True)
        if step.get("after") == "copy_static":
            copy_static()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Launch the packaged desktop installer and check that the app really starts.

uv run python scripts/desktop_smoke.py [--installer PATH] [--timeout SECONDS] [--output PATH]

Windows: silent NSIS install into a temp dir (or the per-user folder if the installer ignores /D), then run the app.
macOS: mount the DMG read-only, copy "Agentic QE.app" to a temp dir, clear quarantine, run its binary.
Linux: run the AppImage with --appimage-extract-and-run (CI wraps this in xvfb-run -a).

The app runs with AGENTIC_QE_SMOKE=1 in a fresh profile and prints
"AGENTIC_QE_SMOKE OK backend=<port> ui=<port>" before quitting with exit code 0. The check passes only when that line
is present and the exit code is 0. The captured output is printed either way.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = ROOT / "desktop" / "release"
APP_NAME = "Agentic QE"
DEFAULT_TIMEOUT = 180
INSTALL_TIMEOUT = 300
UNINSTALL_WAIT = 60
OK_RE = re.compile(r"^AGENTIC_QE_SMOKE OK backend=(\d+) ui=(\d+)\r?$", re.MULTILINE)
MACHINE_TO_ARCH = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64", "AMD64": "x64"}


class SmokeError(RuntimeError):
    """Setup failed before the app could report; ``output`` holds any installer output worth printing."""

    def __init__(self, reason: str, output: str = "") -> None:
        super().__init__(reason)
        self.output = output


@dataclass
class Run:
    returncode: int | None
    output: str
    timed_out: bool = False


# ---- installer discovery -------------------------------------------------------------------------------------------


def installer_pattern(system: str) -> str:
    """Glob for the electron-builder artifact (productName "Agentic QE", artifactName in electron-builder.yml)."""
    patterns = {
        "Windows": "Agentic-QE-*-win-x64.exe",
        "Darwin": "Agentic-QE-*-mac-*.dmg",
        "Linux": "Agentic-QE-*-linux-*.AppImage",  # electron-builder writes x86_64 or x64 here
    }
    if system not in patterns:
        raise ValueError(f"no desktop installer for {system}")
    return patterns[system]


def find_installer(system: str, machine: str, release_dir: Path | None = None) -> Path:
    """The single installer for this OS (and, on macOS, this CPU architecture) in desktop/release."""
    release_dir = release_dir or RELEASE_DIR
    pattern = installer_pattern(system)
    found = sorted(release_dir.glob(pattern))
    if system == "Darwin":
        arch = MACHINE_TO_ARCH.get(machine, machine)
        found = [p for p in found if f"-mac-{arch}." in p.name] or found
    if len(found) != 1:
        names = ", ".join(p.name for p in found) or "none"
        raise FileNotFoundError(f"expected one {pattern} in {release_dir} (found: {names}); pass --installer")
    return found[0]


# ---- command construction (pure) ----------------------------------------------------------------------------------


def smoke_env(base: dict[str, str] | None = None) -> dict[str, str]:
    return {**(dict(os.environ) if base is None else base), "AGENTIC_QE_SMOKE": "1"}


def windows_install_argv(installer: Path, target: Path) -> list[str]:
    # NSIS wants /D last, unquoted, and the path must not contain spaces (see temp_root).
    return [str(installer), "/S", f"/D={target}"]


def windows_app_candidates(target: Path, local_appdata: Path) -> list[Path]:
    """Where the installed exe may be: the requested dir, or the per-user default if /D was ignored."""
    return [target / f"{APP_NAME}.exe", local_appdata / "Programs" / APP_NAME / f"{APP_NAME}.exe"]


def windows_taskkill_argv(pid: int) -> list[str]:
    return ["taskkill", "/PID", str(pid), "/T", "/F"]


def mac_mount_argv(dmg: Path, mountpoint: Path) -> list[str]:
    return ["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mountpoint), str(dmg)]


def mac_detach_argv(mountpoint: Path) -> list[str]:
    return ["hdiutil", "detach", str(mountpoint), "-force"]


def mac_xattr_argv(app: Path) -> list[str]:
    return ["xattr", "-dr", "com.apple.quarantine", str(app)]


def mac_binary(app: Path) -> Path:
    return app / "Contents" / "MacOS" / APP_NAME


def launch_argv(system: str, path: Path) -> list[str]:
    """Command that starts the app: the exe or macOS binary directly, the AppImage via extract-and-run on Linux."""
    if system == "Linux":
        return [str(path), "--appimage-extract-and-run", "--no-sandbox"]
    return [str(path)]


def temp_root(tmp: str, system_drive: str) -> str:
    """NSIS /D cannot take a path with spaces; fall back to the system drive root when the temp dir has one."""
    return tmp if " " not in tmp else system_drive + os.sep


def passed(run: Run) -> bool:
    return not run.timed_out and run.returncode == 0 and OK_RE.search(run.output) is not None


def failure_reason(run: Run, timeout: int) -> str:
    if run.timed_out:
        return f"app did not print its verdict within {timeout} s"
    if run.returncode != 0:
        return f"app exited with code {run.returncode}"
    return "no 'AGENTIC_QE_SMOKE OK' line in the app output"


# ---- process helpers ----------------------------------------------------------------------------------------------


def kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            windows_taskkill_argv(proc.pid), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)  # the app's SIGTERM handler stops its services, then quits
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.wait()


def _run(argv: list[str], env: dict[str, str], timeout: int) -> Run:
    """Run argv with output going to a temp file (a pipe can stay open through Electron helpers) and kill on timeout."""
    with tempfile.TemporaryFile("w+b") as out:
        proc = subprocess.Popen(
            argv, stdout=out, stderr=subprocess.STDOUT, env=env, start_new_session=os.name != "nt"
        )
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(proc)
        out.seek(0)
        text = out.read().decode("utf-8", "replace")
    return Run(proc.returncode, text, timed_out)


# ---- per-OS runs --------------------------------------------------------------------------------------------------


def windows_local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def temp_dir_without_spaces() -> Path:
    base = temp_root(tempfile.gettempdir(), os.environ.get("SYSTEMDRIVE", "C:"))
    return Path(tempfile.mkdtemp(prefix="aqe-smoke-", dir=base))


def windows_uninstall(install_dir: Path) -> None:
    """Silent uninstall of what this run installed. NSIS uninstallers return before the work is done, so wait for it."""
    uninstaller = install_dir / f"Uninstall {APP_NAME}.exe"
    if not uninstaller.is_file():
        return
    try:
        subprocess.run(
            [str(uninstaller), "/S"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=INSTALL_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pass
    deadline = time.monotonic() + UNINSTALL_WAIT
    while (install_dir / f"{APP_NAME}.exe").exists() and time.monotonic() < deadline:
        time.sleep(1)


def smoke_windows(installer: Path, timeout: int) -> Run:
    local = windows_local_appdata()
    per_user = local / "Programs" / APP_NAME
    if per_user.exists():
        # Some oneClick NSIS builds ignore /D and install per user; never overwrite a real install.
        raise SmokeError(f"{per_user} already exists; refusing to install over it")
    target = temp_dir_without_spaces()
    try:
        install = _run(windows_install_argv(installer, target), smoke_env(), INSTALL_TIMEOUT)
        if install.timed_out or install.returncode != 0:
            raise SmokeError(f"installer exited with code {install.returncode}", install.output)
        exe = next((p for p in windows_app_candidates(target, local) if p.is_file()), None)
        if exe is None:
            raise SmokeError(
                f"{APP_NAME}.exe not found after install (checked {target} and {per_user})", install.output
            )
        try:
            return _run(launch_argv("Windows", exe), smoke_env(), timeout)
        finally:
            windows_uninstall(exe.parent)
    finally:
        shutil.rmtree(target, ignore_errors=True)


def smoke_macos(installer: Path, timeout: int) -> Run:
    work = Path(tempfile.mkdtemp(prefix="aqe-smoke-"))
    mount = work / "mount"
    mount.mkdir()
    try:
        attach = subprocess.run(
            mac_mount_argv(installer, mount),
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT,
            check=False,
        )
        if attach.returncode != 0:
            raise SmokeError(
                f"hdiutil attach exited with code {attach.returncode}", attach.stdout + attach.stderr
            )
        try:
            source = mount / f"{APP_NAME}.app"
            if not source.is_dir():
                raise SmokeError(f"{APP_NAME}.app not found in {installer.name}")
            app = work / "app" / f"{APP_NAME}.app"
            app.parent.mkdir()
            shutil.copytree(source, app, symlinks=True)
        finally:
            subprocess.run(mac_detach_argv(mount), capture_output=True, timeout=120, check=False)
        subprocess.run(mac_xattr_argv(app), capture_output=True, check=False)
        return _run(launch_argv("Darwin", mac_binary(app)), smoke_env(), timeout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def smoke_linux(installer: Path, timeout: int) -> Run:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SmokeError("no display; run under xvfb-run -a on a headless Linux host")
    installer.chmod(installer.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return _run(launch_argv("Linux", installer), smoke_env(), timeout)


RUNNERS = {"Windows": smoke_windows, "Darwin": smoke_macos, "Linux": smoke_linux}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--installer", type=Path, help="installer to test (default: the one in desktop/release)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="seconds the app may take to print its verdict (default: %(default)s)",
    )
    parser.add_argument("--output", type=Path, help="also write the captured output and verdict to this file")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    system = platform.system()
    output = ""
    installer: Path | None = None
    try:
        installer = args.installer or find_installer(system, platform.machine())
        if system not in RUNNERS:
            raise ValueError(f"no desktop installer smoke test for {system}")
        run = RUNNERS[system](installer, args.timeout)
        output = run.output
        ok = passed(run)
        reason = "" if ok else failure_reason(run, args.timeout)
    except SmokeError as err:
        ok, reason, output = False, str(err), err.output
    except (OSError, ValueError) as err:
        ok, reason = False, str(err)

    name = installer.name if installer else "no installer"
    verdict = f"desktop smoke: {'PASS' if ok else 'FAIL'} ({name})" + ("" if ok else f" - {reason}")
    report = f"{output.rstrip()}\n{verdict}\n" if output else f"{verdict}\n"
    print(report, end="", flush=True)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

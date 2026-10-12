"""Launch the packaged desktop installer and check that the app really starts.

uv run python scripts/desktop_smoke.py [--installer PATH] [--timeout SECONDS] [--output PATH]

Windows: silent per-machine MSI install (msiexec /i ... /qn APPLICATIONFOLDER=<temp dir>), run the app, then silent
uninstall (msiexec /x) and check the app is gone. Needs administrator rights, which the GitHub Windows runners have.
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
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = ROOT / "desktop" / "release"
APP_NAME = "Agentic QE"
DEFAULT_TIMEOUT = 180
INSTALL_TIMEOUT = 300
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
        "Windows": "Agentic-QE-*-win-x64.msi",
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


MSI_SUCCESS = (0, 3010)  # 3010: success, reboot required (the smoke does not reboot)


def windows_install_argv(installer: Path, target: Path, log: Path) -> list[str]:
    # electron-builder's WiX template names the install directory APPLICATIONFOLDER (an upper-case directory Id is a
    # public property, settable on the command line). A per-machine install needs administrator rights.
    return [
        "msiexec",
        "/i",
        str(installer),
        "/qn",
        "/norestart",
        "/l*v",
        str(log),
        f"APPLICATIONFOLDER={target}",
    ]


def windows_uninstall_argv(installer: Path, log: Path) -> list[str]:
    return ["msiexec", "/x", str(installer), "/qn", "/norestart", "/l*v", str(log)]


def windows_app_exe(target: Path) -> Path:
    return target / f"{APP_NAME}.exe"


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
    """msiexec APPLICATIONFOLDER is simplest without spaces (quoting in argv is fragile); fall back to the system drive root."""
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


def temp_dir_without_spaces() -> Path:
    base = temp_root(tempfile.gettempdir(), os.environ.get("SYSTEMDRIVE", "C:"))
    return Path(tempfile.mkdtemp(prefix="aqe-smoke-", dir=base))


def read_log_tail(path: Path, limit: int = 4000) -> str:
    """Last part of an msiexec verbose log. Windows Installer writes UTF-16 logs (with a BOM); UTF-8 is the fallback."""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    text = (
        raw.decode("utf-16", "replace")
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff")
        else raw.decode("utf-8", "replace")
    )
    return f"msiexec log {path.name} (last {limit} characters):\n{text[-limit:]}" if text.strip() else ""


def windows_uninstall(installer: Path, exe: Path, log: Path) -> None:
    """Silent msiexec uninstall of the package this run installed; fails unless the app executable is gone."""
    done = _run(windows_uninstall_argv(installer, log), smoke_env(), INSTALL_TIMEOUT)
    if done.timed_out or done.returncode not in MSI_SUCCESS:
        raise SmokeError(f"msiexec uninstall exited with code {done.returncode}", read_log_tail(log))
    if exe.exists():
        raise SmokeError(
            f"msiexec uninstall reported success but {exe.name} is still present", read_log_tail(log)
        )


def smoke_windows(installer: Path, timeout: int) -> Run:
    program_files = Path(os.environ.get("PROGRAMFILES") or r"C:\Program Files")
    if (program_files / APP_NAME).exists():
        # The MSI upgrades any install with the same UpgradeCode in place; never replace a real install on this machine.
        raise SmokeError(f"{program_files / APP_NAME} already exists; refusing to install over it")
    work = temp_dir_without_spaces()
    target = work / "app"
    install_log = work / "msi-install.log"
    uninstall_log = work / "msi-uninstall.log"
    try:
        install = _run(windows_install_argv(installer, target, install_log), smoke_env(), INSTALL_TIMEOUT)
        if install.timed_out or install.returncode not in MSI_SUCCESS:
            reason = "timed out" if install.timed_out else f"exited with code {install.returncode}"
            raise SmokeError(f"msiexec install {reason}", read_log_tail(install_log))
        exe = windows_app_exe(target)
        if not exe.is_file():
            raise SmokeError(f"{exe.name} not found after install in {target}", read_log_tail(install_log))
        try:
            return _run(launch_argv("Windows", exe), smoke_env(), timeout)
        finally:
            windows_uninstall(installer, exe, uninstall_log)
    finally:
        shutil.rmtree(work, ignore_errors=True)


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

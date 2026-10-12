"""Pure parts of scripts/desktop_smoke.py. Nothing here launches an installer or the app."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import desktop_smoke as ds  # noqa: E402


def _touch(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_bytes(b"")


def test_find_installer_windows_and_linux(tmp_path):
    _touch(
        tmp_path,
        "Agentic-QE-1.0.3-win-x64.msi",
        "Agentic-QE-1.0.3-win-x64.exe",  # the retired NSIS build is never picked up
        "Agentic-QE-1.0.3-linux-x86_64.AppImage",
        "builder-debug.yml",
    )
    assert ds.find_installer("Windows", "AMD64", tmp_path).name == "Agentic-QE-1.0.3-win-x64.msi"
    assert ds.find_installer("Linux", "x86_64", tmp_path).name == "Agentic-QE-1.0.3-linux-x86_64.AppImage"


def test_find_installer_macos_prefers_host_arch(tmp_path):
    _touch(tmp_path, "Agentic-QE-1.0.3-mac-arm64.dmg", "Agentic-QE-1.0.3-mac-x64.dmg")
    assert ds.find_installer("Darwin", "arm64", tmp_path).name == "Agentic-QE-1.0.3-mac-arm64.dmg"
    assert ds.find_installer("Darwin", "x86_64", tmp_path).name == "Agentic-QE-1.0.3-mac-x64.dmg"


def test_find_installer_rejects_missing_and_ambiguous(tmp_path):
    with pytest.raises(FileNotFoundError, match="found: none"):
        ds.find_installer("Windows", "AMD64", tmp_path)
    _touch(tmp_path, "Agentic-QE-1.0.2-win-x64.msi", "Agentic-QE-1.0.3-win-x64.msi")
    with pytest.raises(FileNotFoundError, match="pass --installer"):
        ds.find_installer("Windows", "AMD64", tmp_path)


def test_unknown_os_has_no_installer():
    with pytest.raises(ValueError):
        ds.installer_pattern("FreeBSD")


def test_windows_install_command_is_a_silent_per_machine_msi_with_no_directory_override():
    log = Path("C:/aqe-smoke-abc/msi-install.log")
    argv = ds.windows_install_argv(Path("Agentic-QE-1.0.3-win-x64.msi"), log)
    assert argv == ["msiexec", "/i", "Agentic-QE-1.0.3-win-x64.msi", "/qn", "/norestart", "/l*v", str(log)]
    assert not any(
        arg.startswith("APPLICATIONFOLDER") for arg in argv
    )  # the default Program Files folder is used


def test_windows_icacls_lock_and_restore_argv_target_only_this_user():
    install_dir = Path("C:/Program Files/Agentic QE")
    assert ds.windows_lock_argv(install_dir, "runneradmin") == [
        "icacls",
        str(install_dir),
        "/deny",
        "runneradmin:(OI)(CI)(WD,AD,WEA,WA)",
    ]
    assert ds.windows_unlock_argv(install_dir, "runneradmin") == [
        "icacls",
        str(install_dir),
        "/remove:d",
        "runneradmin",
    ]


def test_msi_1618_says_another_installation_is_in_progress():
    text = ds.msi_failure_reason(ds.Run(1618, ""), "install")
    assert "1618" in text and "another installation is in progress" in text
    assert "exited with code 1603" in ds.msi_failure_reason(ds.Run(1603, ""), "uninstall")
    assert "did not finish" in ds.msi_failure_reason(ds.Run(None, "", timed_out=True), "install")


def test_second_failure_is_added_to_the_first_error_not_substituted():
    first = ds.SmokeError("app exited with code 3", "app output")
    ds._note_second_failure(first, ds.SmokeError("msiexec uninstall exited with code 1603", "log tail"))
    assert str(first) == "app exited with code 3"
    assert first.output.startswith("app output\nAlso: msiexec uninstall exited with code 1603")
    assert first.output.endswith("log tail")


def test_smoke_windows_locks_then_restores_before_uninstall_and_keeps_the_launch_error(tmp_path, monkeypatch):
    program_files = tmp_path / "pf"
    program_files.mkdir()
    monkeypatch.setenv("PROGRAMFILES", str(program_files))
    monkeypatch.setenv("USERNAME", "tester")
    work = tmp_path / "work"
    monkeypatch.setattr(ds, "temp_dir_without_spaces", lambda: work)
    work.mkdir()
    calls: list[list[str]] = []

    def fake_run(argv, env, timeout):
        calls.append(argv)
        if argv[:2] == ["msiexec", "/i"]:
            (program_files / "Agentic QE").mkdir()
            (program_files / "Agentic QE" / "Agentic QE.exe").write_bytes(b"")
            return ds.Run(0, "")
        if argv[0] == "icacls":
            return ds.Run(0, "")
        if argv[:2] == ["msiexec", "/x"]:
            return ds.Run(1603, "")
        raise ds.SmokeError("launch broke", "launch output")

    monkeypatch.setattr(ds, "_run", fake_run)
    with pytest.raises(ds.SmokeError) as raised:
        ds.smoke_windows(Path("Agentic-QE-1.0.3-win-x64.msi"), 10)
    assert str(raised.value) == "launch broke"  # the first error is the one reported
    assert "Also: msiexec uninstall exited with code 1603" in raised.value.output
    deny = next(a for a in calls if a[0] == "icacls" and "/deny" in a)
    restore = next(a for a in calls if a[0] == "icacls" and "/remove:d" in a)
    assert (
        calls.index(deny)
        < calls.index(restore)
        < next(i for i, a in enumerate(calls) if a[:2] == ["msiexec", "/x"])
    )


def test_windows_uninstall_command_removes_the_same_package():
    installer = Path("Agentic-QE-1.0.3-win-x64.msi")
    log = Path("msi-uninstall.log")
    assert ds.windows_uninstall_argv(installer, log) == [
        "msiexec",
        "/x",
        str(installer),
        "/qn",
        "/norestart",
        "/l*v",
        str(log),
    ]


def test_windows_app_exe_and_taskkill(tmp_path):
    assert ds.windows_app_exe(tmp_path / "app") == tmp_path / "app" / "Agentic QE.exe"
    assert ds.windows_taskkill_argv(42) == ["taskkill", "/PID", "42", "/T", "/F"]
    assert ds.MSI_SUCCESS == (0, 3010)  # 3010 = installed, reboot pending


def test_read_log_tail_decodes_windows_installer_utf16_and_utf8(tmp_path):
    utf16 = tmp_path / "install.log"
    utf16.write_bytes("MSI (c) start\r\nError 1603: fatal\r\n".encode("utf-16"))  # with BOM
    text = ds.read_log_tail(utf16)
    assert "Error 1603: fatal" in text and text.startswith("msiexec log install.log")
    plain = tmp_path / "plain.log"
    plain.write_text("line one\nline two\n", encoding="utf-8")
    assert ds.read_log_tail(plain, limit=9).endswith("line two\n")
    assert ds.read_log_tail(tmp_path / "missing.log") == ""
    (tmp_path / "empty.log").write_bytes(b"")
    assert ds.read_log_tail(tmp_path / "empty.log") == ""


def test_temp_root_avoids_spaces():
    assert ds.temp_root("C:\\Temp", "D:") == "C:\\Temp"
    assert ds.temp_root("C:\\Users\\John Smith\\Temp", "D:") == "D:" + ds.os.sep


def test_macos_commands_and_binary_path():
    dmg, mount, app = Path("a.dmg"), Path("/tmp/m"), Path("/tmp/w/Agentic QE.app")
    assert ds.mac_mount_argv(dmg, mount)[:5] == ["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint"]
    assert ds.mac_detach_argv(mount)[:2] == ["hdiutil", "detach"]
    assert ds.mac_xattr_argv(app) == ["xattr", "-dr", "com.apple.quarantine", str(app)]
    assert ds.mac_binary(app) == app / "Contents" / "MacOS" / "Agentic QE"


def test_linux_launch_uses_extract_and_run_without_sandbox():
    argv = ds.launch_argv("Linux", Path("/x/Agentic-QE-1.0.3-linux-x64.AppImage"))
    assert argv[1:] == ["--appimage-extract-and-run", "--no-sandbox"]
    assert ds.launch_argv("Darwin", Path("/a/bin")) == [str(Path("/a/bin"))]  # native separators on Windows


def test_smoke_env_sets_flag_without_touching_base():
    base = {"PATH": "x"}
    env = ds.smoke_env(base)
    assert env == {"PATH": "x", "AGENTIC_QE_SMOKE": "1"} and "AGENTIC_QE_SMOKE" not in base


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("boot\nAGENTIC_QE_SMOKE OK backend=8765 ui=3000\n", True),
        ("boot\r\nAGENTIC_QE_SMOKE OK backend=8765 ui=3000\r\n", True),
        ("AGENTIC_QE_SMOKE FAIL timed out\n", False),
        ("prefix AGENTIC_QE_SMOKE OK backend=1 ui=2\n", False),
        ("", False),
    ],
)
def test_ok_line_parsing(text, expected):
    assert (ds.OK_RE.search(text) is not None) is expected


def test_verdict_needs_ok_line_and_zero_exit():
    ok = "AGENTIC_QE_SMOKE OK backend=1 ui=2\n"
    assert ds.passed(ds.Run(0, ok))
    assert not ds.passed(ds.Run(1, ok))
    assert not ds.passed(ds.Run(0, ok, timed_out=True))
    assert not ds.passed(ds.Run(0, "no verdict"))
    assert "exited with code 3" in ds.failure_reason(ds.Run(3, ""), 10)
    assert "within 10 s" in ds.failure_reason(ds.Run(None, "", timed_out=True), 10)


def test_main_dispatches_by_platform_and_sets_exit_code(tmp_path, monkeypatch, capsys):
    installer = tmp_path / "Agentic-QE-1.0.3-linux-x86_64.AppImage"
    _touch(tmp_path, installer.name)
    monkeypatch.setattr(ds.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ds.platform, "machine", lambda: "x86_64")
    seen = {}

    def fake_linux(path, timeout):
        seen["args"] = (path, timeout)
        return ds.Run(0, "AGENTIC_QE_SMOKE OK backend=1 ui=2\n")

    monkeypatch.setitem(ds.RUNNERS, "Linux", fake_linux)
    out = tmp_path / "out.txt"
    assert ds.main(["--installer", str(installer), "--timeout", "7", "--output", str(out)]) == 0
    assert seen["args"] == (installer, 7)
    assert "desktop smoke: PASS" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8").endswith("PASS (Agentic-QE-1.0.3-linux-x86_64.AppImage)\n")

    monkeypatch.setitem(ds.RUNNERS, "Linux", lambda path, timeout: ds.Run(1, "AGENTIC_QE_SMOKE FAIL x\n"))
    assert ds.main(["--installer", str(installer)]) == 1
    assert "desktop smoke: FAIL" in capsys.readouterr().out


def test_main_reports_setup_errors_as_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ds.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ds.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(ds, "RELEASE_DIR", tmp_path)
    assert ds.main([]) == 1
    assert "desktop smoke: FAIL (no installer)" in capsys.readouterr().out

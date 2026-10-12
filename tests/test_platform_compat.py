"""platform_compat: OS-specific decisions, exercised for both branches regardless of the runner's own OS."""

from __future__ import annotations

from pathlib import Path

from maf_test_harness.envs import platform_compat


def test_venv_python_layout_per_os(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    assert platform_compat.venv_python(tmp_path / "venv") == str(tmp_path / "venv" / "Scripts" / "python.exe")
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", False)
    assert platform_compat.venv_python(tmp_path / "venv") == str(tmp_path / "venv" / "bin" / "python")


def test_venv_site_packages_glob_per_os(monkeypatch, tmp_path):
    venv = tmp_path / "venv"
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    assert platform_compat.venv_site_packages_glob(venv) == []
    (venv / "Lib" / "site-packages").mkdir(parents=True)
    assert platform_compat.venv_site_packages_glob(venv) == [venv / "Lib" / "site-packages"]

    monkeypatch.setattr(platform_compat, "IS_WINDOWS", False)
    posix_venv = tmp_path / "venv2"
    assert platform_compat.venv_site_packages_glob(posix_venv) == []
    (posix_venv / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    assert platform_compat.venv_site_packages_glob(posix_venv) == [
        posix_venv / "lib" / "python3.12" / "site-packages"
    ]


def test_pythonpath_join_uses_os_pathsep(monkeypatch):
    monkeypatch.setattr(platform_compat.os, "pathsep", ";")
    assert platform_compat.pythonpath_join(["a", "b"]) == "a;b"


def test_cache_path_uses_pathlib_join():
    assert platform_compat.cache_path("root", "pip") == str(Path("root", "pip"))


def test_resolve_tool_prefers_which_result(monkeypatch):
    monkeypatch.setattr(platform_compat.shutil, "which", lambda name: f"/resolved/{name}.cmd")
    assert platform_compat.resolve_tool("npm") == ["/resolved/npm.cmd"]
    monkeypatch.setattr(platform_compat.shutil, "which", lambda name: None)
    assert platform_compat.resolve_tool("npm") == ["npm"]


def test_resolve_wrapper_prefers_repo_wrapper(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    (tmp_path / "mvnw.cmd").write_text("")
    assert platform_compat.resolve_wrapper(tmp_path, "mvnw", "mvnw.cmd", "mvn") == [
        str(tmp_path / "mvnw.cmd")
    ]

    monkeypatch.setattr(platform_compat, "IS_WINDOWS", False)
    posix_dir = tmp_path / "posix"
    posix_dir.mkdir()
    (posix_dir / "mvnw").write_text("")
    assert platform_compat.resolve_wrapper(posix_dir, "mvnw", "mvnw.cmd", "mvn") == ["./mvnw"]

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(platform_compat.shutil, "which", lambda name: None)
    assert platform_compat.resolve_wrapper(empty, "mvnw", "mvnw.cmd", "mvn") == ["mvn"]


def test_tool_present_python3_is_the_runners_python_detection(monkeypatch):
    """Presence and execution agree: a PATH hit alone (possibly the Store alias) is never a Python."""
    from pathlib import Path

    from maf_test_harness.envs import toolchains
    from maf_test_harness.envs.toolchains import Toolchain

    which_calls = []
    monkeypatch.setattr(
        platform_compat.shutil, "which", lambda name, path=None: which_calls.append(name) or "/usr/bin/python"
    )
    monkeypatch.setattr(toolchains, "detect_pythons", list)
    assert platform_compat.tool_present("python3") is False
    assert which_calls == []
    monkeypatch.setattr(
        toolchains,
        "detect_pythons",
        lambda: [Toolchain("python", "3.12.4", Path("/opt/py/bin/python3"), "system")],
    )
    assert platform_compat.tool_present("python3") is True


def test_fake_home_env_posix_sets_home_only(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", False)
    fake = tmp_path / "home"
    env = platform_compat.fake_home_env(fake)
    assert env == {"HOME": str(fake)}
    assert fake.is_dir()


def test_fake_home_env_windows_sets_profile_and_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    fake = tmp_path / "home"
    env = platform_compat.fake_home_env(fake)
    assert env["USERPROFILE"] == str(fake)
    assert env["HOME"] == str(fake)
    assert env["APPDATA"] == str(fake / "AppData" / "Roaming")
    assert env["LOCALAPPDATA"] == str(fake / "AppData" / "Local")
    assert Path(env["APPDATA"]).is_dir() and Path(env["LOCALAPPDATA"]).is_dir()


def test_new_process_group_kwargs_per_os(monkeypatch):
    import subprocess

    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    # the flag only exists on Windows; raising=False lets this run on a Linux/macOS CI runner too
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    assert platform_compat.new_process_group_kwargs() == {"creationflags": 512}
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", False)
    assert platform_compat.new_process_group_kwargs() == {"start_new_session": True}


def test_kill_process_tree_windows_uses_taskkill(monkeypatch):
    import subprocess

    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))

    class FakeProcess:
        pid = 4321

    platform_compat.kill_process_tree(FakeProcess())
    assert calls and calls[0][0][0] == ["taskkill", "/T", "/F", "/PID", "4321"]


def test_kill_process_tree_posix_uses_killpg(monkeypatch):
    import signal

    monkeypatch.setattr(platform_compat, "IS_WINDOWS", False)
    # os.killpg / signal.SIGKILL only exist on POSIX; raising=False lets this test simulate the POSIX
    # branch on any host, including a Windows CI runner.
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    calls = []
    monkeypatch.setattr(
        platform_compat.os, "killpg", lambda pid, sig: calls.append((pid, sig)), raising=False
    )

    class FakeProcess:
        pid = 1234

    platform_compat.kill_process_tree(FakeProcess())
    assert calls == [(1234, 9)]


def test_playwright_cache_dir_windows_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "ms-playwright").mkdir()
    assert platform_compat.playwright_cache_dir() == tmp_path / "ms-playwright"


def test_playwright_cache_dir_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nope"))
    assert platform_compat.playwright_cache_dir() is None

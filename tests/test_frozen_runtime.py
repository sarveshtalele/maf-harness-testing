"""The frozen desktop backend acts as Python for skill scripts, hooks and venv creation (see paths.python_argv)."""

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

from maf_test_harness import desktop_backend, errors, paths
from maf_test_harness.envs import runners, toolchains
from maf_test_harness.envs.execution import run_command
from maf_test_harness.envs.toolchains import Toolchain
from maf_test_harness.hooks import config
from maf_test_harness.skills import scripts

REPO = Path(__file__).resolve().parents[1]
PROBE = textwrap.dedent(
    """
    import json, pathlib, sys
    data = {"argv": sys.argv, "path": sys.path, "bytecode": sys.dont_write_bytecode}
    pathlib.Path(__file__).with_name("out.json").write_text(json.dumps(data), encoding="utf-8")
    """
)


SHIM = "import sys; from maf_test_harness import desktop_backend as d; sys.exit(d.main(sys.argv[1:]))"


@pytest.fixture(autouse=True)
def _fresh_python_scan():
    toolchains.clear_python_scan()
    yield
    toolchains.clear_python_scan()


@pytest.fixture
def interpreter_state(monkeypatch):
    """Restore what --run-python changes process-wide (argv, sys.path, bytecode switch)."""
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.setattr(sys, "pycache_prefix", None)


def _freeze(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(REPO), raising=False)


def _probe(tmp_path: Path) -> Path:
    script = tmp_path / "tool" / "probe.py"
    script.parent.mkdir()
    script.write_text(PROBE, encoding="utf-8")
    return script


def _seen(script: Path) -> dict:
    return json.loads((script.parent / "out.json").read_text(encoding="utf-8"))


def test_run_python_runs_a_script_with_its_argv_and_folder_on_path(tmp_path, interpreter_state, monkeypatch):
    script = _probe(tmp_path)
    monkeypatch.setattr(paths, "resource_root", lambda: tmp_path)
    assert desktop_backend.main(["--run-python", str(script), "a", "b"]) == 0
    seen = _seen(script)
    assert seen["argv"] == [str(script), "a", "b"]
    assert seen["path"][0] == str(script.parent.resolve())


@pytest.mark.parametrize(
    ("body", "code", "stderr"),
    [
        ("import sys\nsys.exit(3)\n", 3, ""),
        ("raise ValueError('boom')\n", 1, "ValueError: boom"),
        ("import sys\nsys.exit('bad input')\n", 1, "bad input"),
        ("x = 1\n", 0, ""),
    ],
)
def test_run_python_propagates_exit_codes(
    tmp_path, interpreter_state, capsys, body, code, stderr, monkeypatch
):
    script = tmp_path / "exit.py"
    script.write_text(body, encoding="utf-8")
    monkeypatch.setattr(paths, "resource_root", lambda: tmp_path)
    assert desktop_backend.main(["--run-python", str(script)]) == code
    assert stderr in capsys.readouterr().err


def test_run_python_refuses_arbitrary_code_and_modules(tmp_path, monkeypatch, interpreter_state, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "probe_mod.py").write_text("print('MOD')\n", encoding="utf-8")
    assert desktop_backend.main(["--run-python", "-m", "probe_mod", "x"]) == 2
    assert desktop_backend.main(["--run-python", "-c", "import os; print('CODE')"]) == 2
    assert desktop_backend.main(["--run-python", "-c", "raise SystemExit(__import__('os').getcwd())"]) == 2
    assert "CODE" not in capsys.readouterr().out
    assert desktop_backend.main(["--run-python", "-c", "raise SystemExit('no python here')"]) == 1
    assert "no python here" in capsys.readouterr().err


def test_run_python_isolated_flag_keeps_the_script_folder_off_sys_path(
    tmp_path, interpreter_state, monkeypatch
):
    script = _probe(tmp_path)
    monkeypatch.setattr(paths, "resource_root", lambda: tmp_path)
    assert desktop_backend.main(["--run-python", "-I", "-B", str(script)]) == 0
    seen = _seen(script)
    assert str(script.parent.resolve()) not in seen["path"]
    assert seen["bytecode"] is True


def test_run_python_never_starts_the_api(monkeypatch, interpreter_state):
    from maf_test_harness.ui import api

    monkeypatch.setattr(api, "main", lambda: pytest.fail("the API must not start in --run-python mode"))
    assert desktop_backend.main(["--run-python", "-c", "raise SystemExit('stop')"]) == 1


def test_run_python_rejects_unknown_options_and_missing_scripts(tmp_path, interpreter_state, capsys):
    assert desktop_backend.main(["--run-python", "-q", "x.py"]) == 2
    assert desktop_backend.main(["--run-python", str(tmp_path / "missing.py")]) == 2
    assert desktop_backend.main(["--run-python"]) == 2
    assert "usage" in capsys.readouterr().err


def test_python_argv_is_the_interpreter_or_the_backend(monkeypatch):
    assert paths.python_argv() == [sys.executable]
    _freeze(monkeypatch)
    assert paths.python_argv() == [sys.executable, "--run-python"]


def test_frozen_skill_script_runs_through_the_backend(monkeypatch):
    seen = {}

    def fake_run(argv, cwd, output_dir, **kwargs):
        seen["argv"] = argv
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "stdout.log").write_text("{}", encoding="utf-8")
        (output_dir / "stderr.log").write_text("", encoding="utf-8")
        return {"status": "command_succeeded", "exit_code": 0}

    monkeypatch.setattr(scripts, "run_command", fake_run)
    _freeze(monkeypatch)
    runner = scripts.HarnessScriptRunner(REPO / "skills", tiers=("host",))
    runner.run(
        "reverse-engineering",
        "scripts/analyze.py",
        ["--request", "input/request.json", "--output", "output.json"],
        files={"request.json": "{}"},
    )
    script = (REPO / "skills/reverse-engineering/scripts/analyze.py").resolve()
    assert seen["argv"][:8] == [
        sys.executable,
        "--run-python",
        "-B",
        "--check-hash-based-pycs",
        "always",
        "-X",
        seen["argv"][6],
        str(script),
    ]
    assert seen["argv"][6].startswith(
        "pycache_prefix="
    )  # a per-run prefix: no bytecode beside the reviewed script


def _homes(found):
    return [Path(t.home).resolve() for t in found]


def test_detection_registers_the_running_interpreter_only_when_it_is_python(monkeypatch):
    monkeypatch.setattr(toolchains.shutil, "which", lambda name, path=None: sys.executable)
    assert Path(sys.executable).resolve() in _homes(toolchains.detect_pythons())
    _freeze(monkeypatch)  # the frozen executable is the backend: it is never a Python toolchain
    assert Path(sys.executable).resolve() not in _homes(toolchains.detect_pythons())


def test_frozen_detection_skips_candidates_that_do_not_run(monkeypatch):
    _freeze(monkeypatch)
    monkeypatch.setattr(toolchains.shutil, "which", lambda name, path=None: "/nonexistent/bin/python3")
    assert toolchains.detect_pythons() == []


def test_frozen_create_venv_uses_a_detected_python(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    real = Toolchain("python", "3.12.4", Path("/opt/py/bin/python3"), "system")
    monkeypatch.setitem(toolchains.DETECTORS, "python", lambda: [real])
    steps = runners.setup_steps("python", tmp_path, str(tmp_path / "cache"), 60)
    assert steps[0].name == "create-venv"
    assert steps[0].argv == [str(real.home), "-m", "venv", runners.VENV_DIR]


def test_frozen_create_venv_fails_clearly_without_a_python(monkeypatch, tmp_path, capsys):
    _freeze(monkeypatch)
    monkeypatch.setitem(toolchains.DETECTORS, "python", list)
    step = runners.setup_steps("python", tmp_path, str(tmp_path / "cache"), 60)[0]
    assert step.name == "python-missing"
    assert step.argv[:3] == [sys.executable, "--run-python", "-c"]
    assert desktop_backend.run_python(step.argv[2:]) == 1
    assert runners.NO_PYTHON_MESSAGE.format("3.x") in capsys.readouterr().err


def test_missing_python_step_is_classified_with_its_remedy(monkeypatch, tmp_path):
    """The real failing step, run through the real backend, is diagnosed as ENV-PY-011 with the install remedy."""
    _freeze(monkeypatch)
    monkeypatch.setitem(toolchains.DETECTORS, "python", list)
    step = runners.setup_steps("python", tmp_path, str(tmp_path / "cache"), 60)[0]
    monkeypatch.setattr(sys, "frozen", False, raising=False)  # run the subprocess as plain Python below
    result = run_command(
        [sys.executable, "-c", SHIM, *step.argv[1:]],
        tmp_path,
        tmp_path / "logs",
        timeout=60,
        env={"PYTHONPATH": str(REPO / "src")},
    )
    assert result["exit_code"] == 1
    text = (tmp_path / "logs" / "stderr.log").read_text(encoding="utf-8")
    diagnosis = errors.classify(text, "python")[0]
    assert diagnosis.code == "ENV-PY-011"
    assert "python.org" in diagnosis.hint and "retry" in diagnosis.hint


def test_frozen_create_venv_uses_the_newest_python_that_satisfies_the_project(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    found = [
        Toolchain("python", "3.12.4", Path("/opt/py312/bin/python3"), "system"),
        Toolchain("python", "3.11.2", Path("/opt/py311a/bin/python3"), "system"),
        Toolchain("python", "3.11.9", Path("/opt/py311b/bin/python3"), "system"),
    ]
    monkeypatch.setitem(toolchains.DETECTORS, "python", lambda: found)
    (tmp_path / ".python-version").write_text("3.11\n", encoding="utf-8")
    assert runners.setup_steps("python", tmp_path, str(tmp_path / "c"), 60)[0].argv[0] == str(
        Path("/opt/py311b/bin/python3")
    )
    (tmp_path / ".python-version").unlink()
    assert runners.setup_steps("python", tmp_path, str(tmp_path / "c"), 60)[0].argv[0] == str(
        Path("/opt/py312/bin/python3")
    )


def test_frozen_create_venv_names_the_required_version_when_none_satisfies(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    monkeypatch.setitem(
        toolchains.DETECTORS, "python", lambda: [Toolchain("python", "3.12.4", Path("/p"), "system")]
    )
    (tmp_path / ".python-version").write_text("3.11\n", encoding="utf-8")
    step = runners.setup_steps("python", tmp_path, str(tmp_path / "c"), 60)[0]
    assert step.name == "python-missing"
    assert "Python ==3.11 is required to run Python tests" in step.argv[-1]


def test_python_scan_is_cached_per_process(monkeypatch):
    calls = []

    def probe(argv):
        calls.append(argv)
        return ("3.12.4", argv[0])

    monkeypatch.setattr(toolchains, "_probe_python", probe)
    monkeypatch.setattr(toolchains.shutil, "which", lambda name, path=None: f"/opt/{name}")
    toolchains.detect_pythons()
    first = len(calls)
    assert first > 0
    toolchains.detect_pythons()
    assert len(calls) == first


def _windows_path(monkeypatch, path_dirs: list[str]) -> None:
    """Windows PATH rules (";" separator, drive letters) on any host."""
    monkeypatch.setattr(toolchains.platform_compat, "IS_WINDOWS", True)
    monkeypatch.setattr(os, "pathsep", ";")
    monkeypatch.setenv("PATH", ";".join(path_dirs))


def _windows_fs(monkeypatch, files: set[str], path_dirs: list[str], searches: list):
    """Pretend Windows: ``which`` finds ``name.exe`` only in the files given, searching the ``path`` it is passed."""
    _windows_path(monkeypatch, path_dirs)

    def which(name, path=None):
        searches.append((name, path))
        for directory in (path if path is not None else os.environ["PATH"]).split(os.pathsep):
            candidate = f"{directory}\\{name}.exe"
            if candidate in files:
                return candidate
        return None

    monkeypatch.setattr(toolchains.shutil, "which", which)


ALIAS_DIR = r"C:\Users\me\AppData\Local\Microsoft\WindowsApps"
REAL_DIR = r"C:\Python312"


def test_frozen_windows_finds_a_real_python_exe_and_never_runs_the_store_alias(monkeypatch):
    files = {rf"{ALIAS_DIR}\python.exe", rf"{REAL_DIR}\python.exe"}
    searches, probed = [], []
    _windows_fs(monkeypatch, files, [ALIAS_DIR, REAL_DIR], searches)  # the alias comes first on PATH

    def probe(argv):
        probed.append(argv[0])
        assert "windowsapps" not in argv[0].lower(), f"executed the Store alias: {argv}"
        return ("3.12.4", argv[0])

    monkeypatch.setattr(toolchains, "_probe_python", probe)
    _freeze(monkeypatch)
    assert [str(t.home) for t in toolchains.detect_pythons()] == [rf"{REAL_DIR}\python.exe"]
    assert probed == [rf"{REAL_DIR}\python.exe"]
    assert all("windowsapps" not in (path or "").lower() for _, path in searches)


def test_frozen_windows_alias_only_means_no_python_and_nothing_is_probed(monkeypatch):
    files = {rf"{ALIAS_DIR}\python.exe", rf"{ALIAS_DIR}\python3.exe"}
    searches, probed = [], []
    _windows_fs(monkeypatch, files, [ALIAS_DIR], searches)
    monkeypatch.setattr(toolchains, "_probe_python", lambda argv: probed.append(argv) or None)
    _freeze(monkeypatch)
    assert toolchains.detect_pythons() == []
    assert probed == []
    assert searches and all("windowsapps" not in (path or "").lower() for _, path in searches)


def test_frozen_bare_names_are_searched_on_every_os_and_windows_store_folders_are_removed(monkeypatch):
    asked = []
    monkeypatch.setattr(toolchains, "_probe_python", lambda argv: None)
    monkeypatch.setattr(toolchains.shutil, "which", lambda name, path=None: asked.append((name, path)))
    _freeze(monkeypatch)
    monkeypatch.setattr(toolchains.platform_compat, "IS_WINDOWS", False)
    toolchains.detect_pythons()
    assert ("python3", None) in asked and ("python", None) in asked  # POSIX: the normal PATH
    asked.clear()
    toolchains.clear_python_scan()
    _windows_path(monkeypatch, [ALIAS_DIR, REAL_DIR])
    toolchains.detect_pythons()
    names = {name for name, _ in asked}
    assert {"python3", "python"} <= names
    assert all(path is not None and "windowsapps" not in path.lower() for _, path in asked)


def test_frozen_runner_argv_round_trips_non_ascii_through_run_command(tmp_path):
    work = tmp_path / "work"
    (work / "input").mkdir(parents=True)
    (work / "input" / "request.json").write_text('{"name": "h\u00e9llo"}', encoding="utf-8")
    script = tmp_path / "tool" / "echo.py"
    script.parent.mkdir()
    script.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "request = json.loads(Path('input/request.json').read_text(encoding='utf-8'))\n"
        "print(request['name'], '\u2713', sys.argv[1:])\n",
        encoding="utf-8",
    )
    argv = [sys.executable, "-c", SHIM, "--run-python", str(script), "--request", "input/request.json"]
    # The shim runs in a child process: its allowed folders come from the environment (the plugin folder here).
    env = {"PYTHONPATH": str(REPO / "src"), "HARNESS_PLUGIN_DIR": str(tmp_path)}
    result = run_command(argv, work, tmp_path / "logs", timeout=60, env=env)
    assert result["exit_code"] == 0, (tmp_path / "logs" / "stderr.log").read_text(encoding="utf-8")
    out = (tmp_path / "logs" / "stdout.log").read_text(encoding="utf-8")
    assert "h\u00e9llo \u2713" in out and "--request" in out


def test_run_python_passes_pycache_prefix_and_hash_check_options(tmp_path, interpreter_state, monkeypatch):
    monkeypatch.setattr(paths, "resource_root", lambda: tmp_path)
    script = tmp_path / "tool" / "flags.py"
    script.parent.mkdir()
    prefix = tmp_path / "pycache"
    script.write_text(
        "import json, pathlib, sys\n"
        "data = {'prefix': sys.pycache_prefix, 'argv': sys.argv}\n"
        "pathlib.Path(__file__).with_name('flags.json').write_text(json.dumps(data), encoding='utf-8')\n",
        encoding="utf-8",
    )
    code = desktop_backend.main(
        [
            "--run-python",
            "-X",
            f"pycache_prefix={prefix}",
            "--check-hash-based-pycs",
            "always",
            str(script),
            "arg",
        ]
    )
    assert code == 0
    seen = json.loads((script.parent / "flags.json").read_text(encoding="utf-8"))
    assert seen == {"prefix": str(prefix), "argv": [str(script), "arg"]}


def test_self_check_audit_only_counts_module_level_imports(monkeypatch, tmp_path):
    scripts_dir = tmp_path / "skills" / "demo" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "tool.py").write_text(
        "import json\n"
        "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n"
        "try:\n    import no_such_guarded_module\nexcept (ValueError, ModuleNotFoundError):\n    pass\n"
        "def run():\n    import no_such_function_only_module\n"
        "try:\n    import no_such_unguarded_module\nexcept ValueError:\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "skills_dir", lambda: tmp_path / "skills")
    assert desktop_backend._unresolved_skill_imports() == [
        "demo/scripts/tool.py: import no_such_unguarded_module"
    ]


def test_self_check_prints_the_lint_line_even_when_the_import_audit_fails(monkeypatch, capsys):
    from maf_test_harness import cli

    monkeypatch.setattr(cli, "main", lambda argv: 0)
    monkeypatch.setattr(
        desktop_backend, "_unresolved_skill_imports", lambda: ["demo/scripts/x.py: import nope"]
    )
    assert desktop_backend.self_check() == 1
    captured = capsys.readouterr()
    assert "lint-skills OK" in captured.out
    assert "demo/scripts/x.py: import nope" in captured.err


def test_source_create_venv_uses_the_running_interpreter(tmp_path, monkeypatch):
    monkeypatch.setitem(toolchains.DETECTORS, "python", list)
    argv = runners.setup_steps("python", tmp_path, str(tmp_path / "cache"), 60)[0].argv
    assert argv == [sys.executable, "-m", "venv", runners.VENV_DIR]


def test_python_hooks_resolve_through_python_argv(monkeypatch, tmp_path):
    entry = {"_source": str(tmp_path / "hooks.json")}
    assert config._resolve_argv(["python", "check.py"], entry) == [sys.executable, "check.py"]
    _freeze(monkeypatch)
    assert config._resolve_argv(["python", "check.py"], entry) == [sys.executable, "--run-python", "check.py"]


def test_self_check_flags_a_skill_import_that_cannot_be_resolved(monkeypatch, tmp_path):
    scripts_dir = tmp_path / "skills" / "demo" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (scripts_dir / "tool.py").write_text(
        "import json\nimport helper\nfrom . import sibling\nimport no_such_module_for_harness_tests\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "skills_dir", lambda: tmp_path / "skills")
    assert desktop_backend._unresolved_skill_imports() == [
        "demo/scripts/tool.py: import no_such_module_for_harness_tests"
    ]


def _fake_interpreters(monkeypatch, available: dict[str, str]):
    """Interpreters that exist only while the test puts them in ``available`` (name -> version)."""
    monkeypatch.setattr(toolchains.platform_compat, "IS_WINDOWS", False)
    monkeypatch.setattr(
        toolchains.shutil,
        "which",
        lambda name, path=None: f"/opt/{name}/bin/{name}" if name in available else None,
    )

    def probe(argv):
        name = Path(argv[0]).name
        return (available[name], argv[0]) if name in available else None

    monkeypatch.setattr(toolchains, "_probe_python", probe)


def test_a_missing_python_is_not_cached_so_a_retry_finds_a_new_install(monkeypatch):
    _freeze(monkeypatch)
    available: dict[str, str] = {}
    _fake_interpreters(monkeypatch, available)
    assert toolchains.detect_pythons() == []  # the first scan finds none
    available["python3.11"] = "3.11.9"  # the user installs Python 3.11 ...
    found = toolchains.detect_pythons()  # ... and the next call finds it, with no restart
    assert [(t.version, t.home) for t in found] == [("3.11.9", Path("/opt/python3.11/bin/python3.11"))]


def test_retry_after_a_missing_python_finds_the_install_the_project_requires(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    available = {"python3.12": "3.12.4"}  # a suitable-looking interpreter, but not the one the project pins
    _fake_interpreters(monkeypatch, available)
    (tmp_path / ".python-version").write_text("3.11\n", encoding="utf-8")
    assert runners.setup_steps("python", tmp_path, str(tmp_path / "c"), 60)[0].name == "python-missing"
    available["python3.11"] = "3.11.9"  # installed after the failed attempt
    step = runners.setup_steps("python", tmp_path, str(tmp_path / "c"), 60)[0]  # the retry
    assert step.name == "create-venv"
    assert step.argv[0] == str(Path("/opt/python3.11/bin/python3.11"))


# --- user python hooks in the frozen app (a hook script is refused unless it sits in a hook folder) -------------------


def _frozen_hooks(monkeypatch, tmp_path):
    """Frozen app: the backend runs as ``[sys.executable, -c, SHIM, --run-python]``. Returns the user hooks folder."""
    from maf_test_harness.hooks import config as hook_config

    _freeze(monkeypatch)
    data = tmp_path / "data"
    folder = data / ".harness"
    folder.mkdir(parents=True)
    monkeypatch.setattr(paths, "user_data_root", lambda: data)
    monkeypatch.setattr(paths, "python_argv", lambda: [sys.executable, "-c", SHIM, "--run-python"])
    hook_env = hook_config._hook_env
    # The child needs the package on its path; everything else it gets is the harness's own hook environment.
    monkeypatch.setattr(
        hook_config, "_hook_env", lambda entry: {**hook_env(entry), "PYTHONPATH": str(REPO / "src")}
    )
    return folder


def _engine(config_file: Path, audit=None):
    from maf_test_harness.hooks.config import compile_entry, load_config
    from maf_test_harness.hooks.engine import HookEngine

    return HookEngine([compile_entry(e) for e in load_config([config_file]).hooks], "enforce", audit)


def test_frozen_python_hook_beside_hooks_json_runs_and_can_deny(tmp_path, monkeypatch):
    folder = _frozen_hooks(monkeypatch, tmp_path)
    (folder / "check_path.py").write_text(
        "import json, sys\n"
        "event = json.load(sys.stdin)\n"
        "if event['arguments'].get('path', '').endswith('.sh'):\n"
        "    sys.stderr.write('shell scripts are not allowed')\n"
        "    sys.exit(2)\n",
        encoding="utf-8",
    )
    config_file = folder / "hooks.json"
    config_file.write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "event": "PreToolUse",
                        "matcher": "^write_test_file$",
                        "command": ["python", "check_path.py"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    engine = _engine(config_file)
    denied, _ = engine.before_tool("write_test_file", {"path": "tests/run.sh"})
    assert denied.verdict == "deny" and "shell scripts are not allowed" in denied.reason
    allowed, _ = engine.before_tool("write_test_file", {"path": "tests/test_run.py"})
    assert allowed.verdict == "allow"


def test_frozen_python_hook_outside_the_hook_folders_is_refused_with_a_clear_message(tmp_path, monkeypatch):
    folder = _frozen_hooks(monkeypatch, tmp_path)
    elsewhere = tmp_path / "elsewhere" / "check.py"
    elsewhere.parent.mkdir()
    elsewhere.write_text("print('never runs')\n", encoding="utf-8")
    config_file = folder / "hooks.json"
    config_file.write_text(
        json.dumps({"hooks": [{"event": "PreToolUse", "command": ["python", str(elsewhere)]}]}),
        encoding="utf-8",
    )
    decision, _ = _engine(config_file).before_tool("read_file", {"path": "a.py"})
    # Fail closed: the refusal is a deny on an enforcing event, and the reason says where the script must live.
    assert decision.verdict == "deny"
    assert "outside the application, plugin and hook folders" in decision.reason
    assert "hooks.json" in decision.reason


def test_frozen_hook_folder_from_the_home_config_is_allowed_only_when_passed_by_the_harness(
    tmp_path, monkeypatch
):
    home_folder = tmp_path / "home" / ".maf-harness"
    home_folder.mkdir(parents=True)
    script = home_folder / "tool.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.delenv("HARNESS_HOOK_FOLDERS", raising=False)
    monkeypatch.setattr(paths, "resource_root", lambda: tmp_path / "resources")
    monkeypatch.setattr(paths, "plugin_dir", lambda: tmp_path / "plugins")
    assert not desktop_backend._allowed_script(str(script))
    monkeypatch.setenv("HARNESS_HOOK_FOLDERS", os.pathsep.join([str(tmp_path / "x"), str(home_folder)]))
    assert desktop_backend._allowed_script(str(script))
    assert not desktop_backend._allowed_script(str(home_folder.parent / "outside.py"))


def test_a_crashing_hook_fails_closed_on_enforcing_events_and_only_records_on_observers(
    tmp_path, monkeypatch
):
    folder = _frozen_hooks(monkeypatch, tmp_path)
    (folder / "broken.py").write_text("import sys\nsys.stderr.write('boom')\nsys.exit(1)\n", encoding="utf-8")
    config_file = folder / "hooks.json"
    config_file.write_text(
        json.dumps(
            {
                "hooks": [
                    {"event": "PreToolUse", "command": ["python", "broken.py"]},
                    {"event": "PostToolUse", "command": ["python", "broken.py"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    audited = []
    engine = _engine(config_file, audit=lambda event, **data: audited.append((event, data)))
    decision, _ = engine.before_tool("read_file", {"path": "a.py"})
    assert decision.verdict == "deny" and "failed" in decision.reason and "exited 1" in decision.reason
    assert engine.after_tool("read_file", {"path": "a.py"}, "result") == "result"  # an observer cannot block
    assert [event for event, _ in audited].count("hook_error") == 2

"""The frozen desktop backend acts as Python for skill scripts, hooks and venv creation (see paths.python_argv)."""

import json
import sys
import textwrap
from pathlib import Path

import pytest

from maf_test_harness import desktop_backend, paths
from maf_test_harness.envs import runners, toolchains
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


def test_run_python_runs_a_script_with_its_argv_and_folder_on_path(tmp_path, interpreter_state):
    script = _probe(tmp_path)
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
def test_run_python_propagates_exit_codes(tmp_path, interpreter_state, capsys, body, code, stderr):
    script = tmp_path / "exit.py"
    script.write_text(body, encoding="utf-8")
    assert desktop_backend.main(["--run-python", str(script)]) == code
    assert stderr in capsys.readouterr().err


def test_run_python_module_and_code(tmp_path, monkeypatch, interpreter_state, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "probe_mod.py").write_text("import sys\nprint('MOD', sys.argv)\n", encoding="utf-8")
    assert desktop_backend.main(["--run-python", "-m", "probe_mod", "x"]) == 0
    out = capsys.readouterr().out
    assert "MOD" in out and out.strip().endswith("'x']")
    assert desktop_backend.main(["--run-python", "-c", "import sys; print('CODE', sys.argv)", "q"]) == 0
    assert "CODE ['-c', 'q']" in capsys.readouterr().out


def test_run_python_isolated_flag_keeps_the_script_folder_off_sys_path(tmp_path, interpreter_state):
    script = _probe(tmp_path)
    assert desktop_backend.main(["--run-python", "-I", "-B", str(script)]) == 0
    seen = _seen(script)
    assert str(script.parent.resolve()) not in seen["path"]
    assert seen["bytecode"] is True


def test_run_python_never_starts_the_api(monkeypatch, interpreter_state):
    from maf_test_harness.ui import api

    monkeypatch.setattr(api, "main", lambda: pytest.fail("the API must not start in --run-python mode"))
    assert desktop_backend.main(["--run-python", "-c", "pass"]) == 0


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
    assert seen["argv"][:3] == [sys.executable, "--run-python", str(script)]


def _homes(found):
    return [Path(t.home).resolve() for t in found]


def test_detection_registers_the_running_interpreter_only_when_it_is_python(monkeypatch):
    monkeypatch.setattr(toolchains.shutil, "which", lambda name: sys.executable)
    assert Path(sys.executable).resolve() in _homes(toolchains.detect_pythons())
    _freeze(monkeypatch)  # the frozen executable is the backend: it is never a Python toolchain
    assert Path(sys.executable).resolve() not in _homes(toolchains.detect_pythons())


def test_frozen_detection_skips_candidates_that_do_not_run(monkeypatch):
    _freeze(monkeypatch)
    monkeypatch.setattr(toolchains.shutil, "which", lambda name: "/nonexistent/bin/python3")
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
    argv = runners.setup_steps("python", tmp_path, str(tmp_path / "cache"), 60)[0].argv
    assert argv[:3] == [sys.executable, "--run-python", "-c"]
    assert desktop_backend.run_python(argv[2:]) == 1
    assert runners.NO_PYTHON_MESSAGE in capsys.readouterr().err


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

"""The host skill scripts run on varied repositories, on every CI OS, without PyInstaller.

The same smoke as ``desktop_backend.self_check()`` runs twice: once through ``paths.python_argv()`` (non-frozen) and once
through the ``--run-python`` shim the frozen app uses, ``[sys.executable, -c, shim, --run-python, script, ...]``.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from maf_test_harness import desktop_backend, paths
from maf_test_harness.envs.execution import run_command
from maf_test_harness.skills.scripts import HarnessScriptRunner

REPO = Path(__file__).resolve().parents[1]
SHIM = "import sys; from maf_test_harness import desktop_backend as d; sys.exit(d.main(sys.argv[1:]))"


class ShimRunner:
    """Runs reviewed scripts the way the frozen app does, after the same allow-list and hash checks as the host."""

    def __init__(self, runner: HarnessScriptRunner, base: Path):
        self.runner = runner
        self.base = base

    def run(self, skill, rel, args=None, *, files=None, output_json=None):
        path = self.runner.check(skill, rel)
        work = Path(tempfile.mkdtemp(dir=self.base)).resolve()
        for name, content in (files or {}).items():
            target = work / "input" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        argv = [sys.executable, "-c", SHIM, "--run-python", str(path), *(args or [])]
        result = run_command(argv, work, work / "logs", timeout=60, env={"PYTHONPATH": str(REPO / "src")})
        data = None
        if output_json and result["exit_code"] == 0:
            data = json.loads((work / output_json).read_text(encoding="utf-8"))
        return {
            "exit_code": result["exit_code"],
            "stdout": (work / "logs" / "stdout.log").read_text(encoding="utf-8", errors="replace"),
            "stderr": (work / "logs" / "stderr.log").read_text(encoding="utf-8", errors="replace"),
            "data": data,
        }


def _smoke(run, base: Path) -> list[str]:
    lines: list[str] = []
    problems = desktop_backend.run_skill_smoke(base, run, lines.append)
    assert problems == [], "\n".join(problems)
    checked = [line for line in lines if line.startswith("  OK")]
    # 7 repositories x 2 analysis scripts + 4 validator runs; validate_design is reported as skipped, not run
    assert len(checked) == 2 * len(desktop_backend.SAMPLE_REPOS) + 4, lines
    assert any("validate_design.py" in line and line.startswith("  SKIP") for line in lines)
    return lines


def test_skill_scripts_run_on_sample_repositories_through_python_argv(tmp_path):
    assert paths.python_argv() == [sys.executable]  # non-frozen: the running interpreter
    runner = HarnessScriptRunner(paths.skills_dir(), tiers=("host", "model"))
    lines = _smoke(runner.run, tmp_path)
    assert any("reverse-engineering [python repo]" in line for line in lines)


def test_skill_scripts_run_on_sample_repositories_through_the_run_python_shim(tmp_path):
    runner = HarnessScriptRunner(paths.skills_dir(), tiers=("host", "model"))
    _smoke(ShimRunner(runner, tmp_path).run, tmp_path)


def test_validate_design_loads_its_imports(tmp_path):
    # Not in SKILLS.lock.json, so the host never runs it; this only proves its module-level imports resolve.
    script = REPO / "skills" / "test-design-generator" / "scripts" / "validate_design.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--repo-url" in completed.stdout

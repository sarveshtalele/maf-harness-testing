"""The generated architecture map is deterministic, covers every source module and is up to date."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import gen_architecture  # noqa: E402


def test_render_is_deterministic():
    assert gen_architecture.render() == gen_architecture.render()


def test_every_source_module_is_in_the_table():
    content, _, _ = gen_architecture.render()
    for path in (ROOT / "src" / "maf_test_harness").rglob("*.py"):
        assert f"| `{path.relative_to(ROOT).as_posix()}` |" in content


def test_check_passes_on_committed_file():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_architecture.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

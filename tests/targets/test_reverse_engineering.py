"""Real target acceptance checks; opt in with REVERSE_ENGINEER_REPO."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(os.environ.get("REVERSE_ENGINEER_REPO", ".missing-target")).resolve()
pytestmark = pytest.mark.skipif(
    not (REPO / "reverse_engineer_skill.py").is_file(),
    reason="Set REVERSE_ENGINEER_REPO to a reviewed checkout",
)


@pytest.mark.parametrize(
    ("args", "code", "message"),
    [
        (["--help"], 0, "Usage:"),
        ([], 1, "Usage:"),
        (["--heuristic"], 1, "No GitHub URL provided"),
    ],
)
def test_cli_help_and_missing_arguments(args, code, message, tmp_path):
    result = subprocess.run(
        [sys.executable, str(REPO / "reverse_engineer_skill.py"), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == code
    assert message in result.stdout


def test_cli_modes_and_interactive_fallback(tmp_path):
    # Execute actual CLI parsing while replacing only the costly pipeline boundary.
    script = """
import sys, io, contextlib
import reverse_engineer_skill as cli
calls=[]
cli.run_pipeline=lambda url,mode: calls.append((url,mode))
for args,expected in [(['--heuristic'],'heuristic'),(['--no-ai'],'heuristic'),(['--ai'],'ai')]:
    sys.argv=['cli','https://github.com/acme/demo',*args]
    cli.main()
    assert calls[-1] == ('https://github.com/acme/demo',expected)
sys.stdin=io.StringIO('invalid\\n')
sys.argv=['cli','https://github.com/acme/demo']
cli.main()
assert calls[-1][1] == 'heuristic'
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def generated_output(tmp_path_factory):
    work = tmp_path_factory.mktemp("target-pipeline")
    fixture = work / "fixture"
    fixture.mkdir()
    (fixture / "service.py").write_text("class Calculator:\n    def add(self, a, b):\n        return a + b\n")
    for cmd in (
        ["git", "init", str(fixture)],
        ["git", "-C", str(fixture), "add", "."],
        [
            "git",
            "-C",
            str(fixture),
            "-c",
            "user.name=Harness",
            "-c",
            "user.email=harness@example.test",
            "commit",
            "-m",
            "fixture",
        ],
    ):
        subprocess.run(cmd, check=True, capture_output=True)
    result = subprocess.run(
        [sys.executable, str(REPO / "reverse_engineer_skill.py"), str(fixture), "--heuristic"],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=60,
    )
    (work / "pipeline.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Complete!" in result.stdout
    return work / "outputs/fixture"


def test_full_pipeline_generates_seven_nonempty_artifacts(generated_output):
    expected = {
        "fixture_sdd.json",
        "fixture_dashboard.html",
        "fixture_report.md",
        "fixture_block_diagram.svg",
        "fixture_dependency_graph.svg",
        "fixture_evaluation.md",
        "manifest.json",
    }
    assert {p.name for p in generated_output.iterdir()} == expected
    for name in expected:
        assert (generated_output / name).stat().st_size > 0
    sdd = json.loads((generated_output / "fixture_sdd.json").read_text())
    assert isinstance(sdd, dict) and len(sdd) >= 10
    manifest = json.loads((generated_output / "manifest.json").read_text())
    assert isinstance(manifest, dict)


def test_dashboard_navigation_in_real_browser(generated_output, page):
    from playwright.sync_api import expect

    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto((generated_output / "fixture_dashboard.html").as_uri())
    expect(page.locator(".nav-item")).to_have_count(6)
    for section in ("architecture", "api", "deadcode", "howitworks", "dataarch", "overview"):
        page.locator(f'.nav-item[onclick*="{section}"]').click()
        expect(page.locator(f"#section-{section}")).to_be_visible()
    assert not errors, errors
    screenshot = os.environ.get("HARNESS_SCREENSHOT_DIR")
    if screenshot:
        Path(screenshot).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(screenshot) / "reverse-dashboard.png"), full_page=True)

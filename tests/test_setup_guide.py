import os
from pathlib import Path

from maf_test_harness.envs.setup_guide import setup_guide


def test_missing_setup_is_explicit_and_pasted_commands_remain_context(tmp_path):
    note = "Python 3.11 required. Start with python -m app. $(touch should-not-exist)"
    guide = setup_guide(
        {
            "steps": [],
            "missing_prerequisites": ["mvn"],
            "prerequisites": [
                {
                    "tool": "mvn",
                    "present": False,
                    "install_hint": "macOS: brew install maven; Windows: use Maven distribution",
                }
            ],
        },
        tmp_path / "repo with spaces",
        note,
    )
    assert guide["status"] == "manual_action_required"
    assert guide["repository_knowledge"] == note
    assert any(row["title"] == "Automatic setup unavailable" for row in guide["steps"])
    assert any("brew install maven" in row.get("instructions", "") for row in guide["steps"])
    assert not Path("should-not-exist").exists()


def test_detected_commands_keep_arguments_cwd_and_environment(tmp_path):
    guide = setup_guide(
        {
            "steps": [
                {
                    "name": "install",
                    "argv": ["python", "-m", "pip", "install", "-r", "requirements.txt"],
                    "cwd": "backend",
                    "env": {"PIP_DISABLE_PIP_VERSION_CHECK": "1"},
                }
            ]
        },
        tmp_path,
    )
    step = next(s for s in guide["steps"] if s["title"] == "install")
    expected = (
        "& 'python' '-m' 'pip' 'install' '-r' 'requirements.txt'"
        if os.name == "nt"
        else "python -m pip install -r requirements.txt"
    )
    assert step["command"] == expected
    assert step["working_directory"] == str(tmp_path / "backend")
    assert step["environment"] == {"PIP_DISABLE_PIP_VERSION_CHECK": "1"}


def test_preview_commands_target_execution_workspace_not_read_only_clone(tmp_path):
    clone = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    guide = setup_guide(
        {
            "steps": [
                {
                    "name": "venv",
                    "argv": ["python", "-m", "venv", str(clone / ".harness-venv")],
                    "cwd": ".",
                    "env": {"PYTHONPATH": str(clone)},
                }
            ]
        },
        workspace,
        source_root=clone,
    )
    step = next(s for s in guide["steps"] if s["title"] == "venv")
    assert str(workspace / ".harness-venv") in step["command"]
    assert str(clone) not in step["command"]
    assert step["environment"]["PYTHONPATH"] == str(workspace)

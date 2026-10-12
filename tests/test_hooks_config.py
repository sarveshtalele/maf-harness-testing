"""``.harness/hooks.json``: validation, merging, compilation and the engine it produces."""

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest
from test_ui_api import local_api  # noqa: F401

from maf_test_harness.hooks import HookConfig, HookConfigError, build_engine, load_config
from maf_test_harness.hooks.config import compile_entry, config_paths, parse_config, validate_entry


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("nope", "must be an object"),
        ({"event": "PreToolUse", "command": ["x"], "colour": "red"}, "unknown key"),
        ({"event": "OnTuesday", "command": ["x"]}, "'event' must be one of"),
        ({"event": "PreToolUse"}, "exactly one of"),
        ({"event": "PreToolUse", "command": ["x"], "python": "a:b"}, "exactly one of"),
        ({"event": "PreToolUse", "command": "echo hi"}, "argv"),
        ({"event": "PreToolUse", "command": []}, "argv"),
        ({"event": "PreToolUse", "command": ["x", 3]}, "argv"),
        ({"event": "PreToolUse", "python": "nocolon"}, "package.module:function"),
        ({"event": "PreToolUse", "command": ["x"], "matcher": "("}, "not a valid regular expression"),
        ({"event": "PreToolUse", "command": ["x"], "matcher": 5}, "regular expression string"),
        ({"event": "PreToolUse", "command": ["x"], "timeout": 0}, "'timeout'"),
        ({"event": "PreToolUse", "command": ["x"], "timeout": 9999}, "'timeout'"),
        ({"event": "PreToolUse", "command": ["x"], "timeout": True}, "'timeout'"),
    ],
)
def test_invalid_entries_are_rejected_with_the_file_and_index(entry, message):
    with pytest.raises(HookConfigError, match=message) as info:
        validate_entry(entry, "cfg.json", 3)
    assert "cfg.json: hooks[3]" in str(info.value)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "top level must be an object"),
        ({"hookz": []}, "unknown key"),
        ({"mode": "strict"}, "'mode' must be one of"),
        ({"builtins": []}, "'builtins' must be an object"),
        ({"builtins": {"nonsense": True}}, "'builtins' must be an object"),
        ({"builtins": {"audit": "yes"}}, "true, false or an options object"),
        ({"hooks": {}}, "'hooks' must be a list"),
    ],
)
def test_invalid_top_level_is_rejected(data, message):
    with pytest.raises(HookConfigError, match=message):
        parse_config(data, "cfg.json")


def test_no_config_files_is_a_valid_builtins_only_setup(tmp_path):
    config = load_config([tmp_path / "missing.json"])
    assert config == HookConfig() and build_engine(config).hooks  # built-ins are there


def test_unreadable_json_is_an_error_naming_the_file(tmp_path):
    path = write(tmp_path / "hooks.json", "{not json")
    with pytest.raises(HookConfigError, match="hooks.json: cannot read"):
        load_config([path])


def test_files_merge_user_first_then_project(tmp_path):
    user = write(
        tmp_path / "user.json",
        {
            "mode": "evaluate_only",
            "builtins": {"audit": False, "stage_budget": {"per_stage_tokens": 10}},
            "hooks": [{"event": "Stop", "command": [sys.executable, "-c", "pass"], "name": "u"}],
        },
    )
    project = write(
        tmp_path / "project.json",
        {
            "builtins": {"stage_budget": {"per_stage_tokens": 99}},
            "hooks": [{"event": "Stop", "command": [sys.executable, "-c", "pass"], "name": "p"}],
        },
    )
    config = load_config([user, project])
    assert config.mode == "evaluate_only"  # the project file did not set a mode, so the user's stays
    assert config.builtins == {"audit": False, "stage_budget": {"per_stage_tokens": 99}}
    assert [h["name"] for h in config.hooks] == ["u", "p"]
    assert config.sources == [str(user), str(project)]


def test_default_locations_are_the_home_dir_and_the_working_directory(tmp_path):
    user, project = config_paths(tmp_path)
    assert user.parts[-2:] == (".maf-harness", "hooks.json")
    assert project == tmp_path / ".harness" / "hooks.json"


def test_the_cloned_repository_can_not_install_hooks(tmp_path, monkeypatch):
    """Only the working directory and the home directory are read, never the repository under test."""
    repo = tmp_path / "run" / "repo"
    write(repo / ".harness" / "hooks.json", {"hooks": [{"event": "PreToolUse", "command": ["x"]}]})
    monkeypatch.chdir(tmp_path)
    assert all(str(repo) not in str(p) for p in config_paths())


# --- compiling --------------------------------------------------------------------------------------------------


def command_entry(tmp_path, body, **extra):
    script = tmp_path / "check.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return validate_entry(
        {"event": "PreToolUse", "command": [sys.executable, str(script)], **extra},
        str(tmp_path / "hooks.json"),
        0,
    )


def test_a_user_command_hook_runs_inside_the_engine(tmp_path):
    entry = command_entry(
        tmp_path,
        """
        import json, sys
        event = json.load(sys.stdin)
        if event["arguments"].get("path", "").endswith(".sh"):
            sys.stderr.write("no shell scripts")
            sys.exit(2)
        """,
        matcher="^write_test_file$",
    )
    engine = build_engine(HookConfig(hooks=[entry]), context={"writable_roots": [str(tmp_path)]})
    assert engine.before_tool("write_test_file", {"path": "run.sh"})[0].reason == "no shell scripts"
    assert not engine.before_tool("write_test_file", {"path": "test_a.py"})[0].blocked
    assert not engine.before_tool("read_file", {"path": "run.sh"})[0].blocked  # matcher excludes it


def test_command_hook_env_carries_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-hooks")
    entry = command_entry(
        tmp_path,
        """
        import json, os, sys
        json.load(sys.stdin)
        if "OPENAI_API_KEY" in os.environ:
            sys.stderr.write("leaked")
            sys.exit(2)
        """,
    )
    assert (
        not build_engine(HookConfig(hooks=[entry]), context={"writable_roots": []})
        .before_tool("read_file", {})[0]
        .blocked
    )


def test_python_alias_means_the_running_interpreter(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("import sys; sys.stderr.write(sys.executable); sys.exit(2)", encoding="utf-8")
    entry = validate_entry(
        {"event": "PreToolUse", "command": ["python", str(script)]}, str(tmp_path / "h.json"), 0
    )
    assert compile_entry(entry).handler.__name__ == "run"
    engine = build_engine(HookConfig(hooks=[entry]))
    assert engine.before_tool("read_file", {})[0].reason == sys.executable


def test_relative_program_is_resolved_against_the_config_file_not_the_cwd(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    helper = cfg_dir / "helper.py"
    helper.write_text("import sys; sys.stderr.write('ran'); sys.exit(2)", encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # somewhere else
    entry = validate_entry({"event": "PreToolUse", "command": ["helper.py"]}, str(cfg_dir / "hooks.json"), 0)
    # a bare name is looked up on PATH (not found here); a path with a separator is relative to the config file
    with pytest.raises(HookConfigError, match="was not found"):
        compile_entry(entry)
    entry = validate_entry(
        {"event": "PreToolUse", "command": ["./helper.py"]}, str(cfg_dir / "hooks.json"), 0
    )
    assert compile_entry(entry).name == "user:helper.py"


def test_missing_program_is_a_config_error(tmp_path):
    entry = validate_entry({"event": "PreToolUse", "command": ["no-such-program-xyz"]}, "h.json", 0)
    with pytest.raises(HookConfigError, match="no-such-program-xyz' was not found"):
        compile_entry(entry)


def test_python_hook_that_cannot_be_imported_is_a_config_error():
    entry = validate_entry({"event": "PreToolUse", "python": "no_such_mod_xyz:fn"}, "h.json", 0)
    with pytest.raises(HookConfigError, match="cannot load"):
        compile_entry(entry)


# --- the switch ----------------------------------------------------------------------------------------------


def test_setting_off_removes_every_hook_and_says_so_in_the_audit():
    log = []
    engine = build_engine(HookConfig(), audit=lambda e, **f: log.append(e), setting="off")
    assert engine.hooks == [] and log == ["hooks_disabled"]


def test_setting_evaluate_only_overrides_the_file_mode():
    assert build_engine(HookConfig(mode="enforce"), setting="evaluate_only").mode == "evaluate_only"
    assert build_engine(HookConfig(mode="evaluate_only"), setting="enforce").mode == "evaluate_only"
    assert build_engine(HookConfig(), setting="enforce").mode == "enforce"


def test_unknown_setting_is_rejected():
    with pytest.raises(HookConfigError, match="HARNESS_HOOKS"):
        build_engine(HookConfig(), setting="sometimes")


def test_builtins_come_before_user_hooks(tmp_path):
    entry = command_entry(tmp_path, "pass")
    names = [h.name for h in build_engine(HookConfig(hooks=[entry])).hooks]
    assert names[0].startswith("builtin:") and names[-1].startswith("user:")


def test_the_shipped_example_configuration_is_valid():
    from pathlib import Path

    example = Path(".harness/hooks.json.example")
    config = parse_config(json.loads(example.read_text(encoding="utf-8")), str(example))
    assert config.mode == "enforce" and len(config.hooks) == 3


def test_a_python_hook_script_is_resolved_beside_its_hooks_json_not_the_cwd(tmp_path, monkeypatch):
    from maf_test_harness import paths
    from maf_test_harness.hooks.config import HOOK_FOLDERS_ENV, _hook_env, _resolve_argv

    folder = tmp_path / "cfg"
    folder.mkdir()
    (folder / "check.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # the harness's working directory must not decide which script runs
    monkeypatch.setattr(paths, "user_data_root", lambda: tmp_path / "data")
    entry = {"_source": str(folder / "hooks.json")}
    assert _resolve_argv(["python", "check.py", "--flag"], entry) == [
        sys.executable,
        str((folder / "check.py").resolve()),
        "--flag",
    ]
    assert _resolve_argv(["python", "-c", "pass"], entry) == [sys.executable, "-c", "pass"]
    folders = _hook_env(entry)[HOOK_FOLDERS_ENV].split(os.pathsep)
    assert folders == [str(tmp_path / "data" / ".harness"), str(folder.resolve())]


def test_a_working_directory_hooks_file_is_not_loaded_and_is_reported_with_the_new_path(
    tmp_path, monkeypatch, caplog
):
    import logging

    from maf_test_harness import paths
    from maf_test_harness.hooks import config as hook_config

    data = tmp_path / "data"
    loaded = data / ".harness" / "hooks.json"
    monkeypatch.setattr(paths, "user_data_root", lambda: data)
    monkeypatch.setattr(hook_config, "config_paths", lambda project_root=None: [loaded])
    checkout = tmp_path / "checkout"
    legacy = write(
        checkout / ".harness" / "hooks.json", {"hooks": [{"event": "PreToolUse", "command": ["x"]}]}
    )
    monkeypatch.chdir(checkout)
    with caplog.at_level(logging.WARNING, logger="maf_test_harness.hooks.config"):
        config = load_config()
    assert config.hooks == []  # the checkout's file never installs hooks
    assert "is ignored" in caplog.text and str(loaded) in caplog.text
    status = hook_config.hook_status()
    assert Path(status["legacy_file"]).resolve() == legacy.resolve()
    assert status["project_file"] == str(loaded) and "is ignored" in status["warning"]
    assert hook_config.legacy_hooks_file(cwd=data) is None  # a file that is loaded is not a legacy file


def test_no_warning_when_the_working_directory_has_no_hooks_file(tmp_path, monkeypatch):
    from maf_test_harness.hooks import config as hook_config

    monkeypatch.chdir(tmp_path)
    assert hook_config.legacy_hooks_file() is None
    assert hook_config.hook_status()["legacy_file"] is None


def test_the_configuration_api_reports_the_hooks_location(local_api):  # noqa: F811
    from maf_test_harness import paths

    assert "hooks" in local_api.config() and local_api.config()["hooks"]["project_file"] == str(
        paths.user_data_root() / ".harness" / "hooks.json"
    )

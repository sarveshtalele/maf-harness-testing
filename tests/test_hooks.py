"""Hook engine: verdict combination, fail-closed behaviour, evaluate-only mode, command and python handlers."""

import os
import sys
import textwrap

import pytest

from maf_test_harness.hooks import (
    POST_TOOL,
    PRE_MODEL,
    PRE_TOOL,
    STOP,
    Decision,
    HookEngine,
    HookEvent,
    HookSpec,
    command_handler,
    python_handler,
)


def spec(handler, event=PRE_TOOL, name="t", matcher=None):
    return HookSpec(event, handler, name, matcher)


def audit_into(log):
    return lambda event, **fields: log.append((event, fields))


def test_no_hooks_allows():
    decision, arguments = HookEngine().before_tool("read_file", {"path": "a"})
    assert decision.verdict == "allow" and not decision.blocked and arguments == {"path": "a"}


def test_deny_beats_ask_and_transform_and_allow():
    engine = HookEngine(
        [
            spec(lambda e: Decision("allow"), name="a"),
            spec(lambda e: Decision("transform", "t", {"arguments": {"path": "x"}}), name="b"),
            spec(lambda e: Decision("ask", "please"), name="c"),
            spec(lambda e: Decision("deny", "no way"), name="d"),
        ],
        approver=lambda event, reason: True,
    )
    decision, _ = engine.before_tool("t", {})
    assert decision.verdict == "deny" and decision.source == "d" and decision.blocked


def test_first_enforced_deny_ends_the_event():
    seen = []
    engine = HookEngine(
        [
            spec(lambda e: Decision("deny", "first"), name="one"),
            spec(lambda e: seen.append("two") or Decision(), name="two"),
        ]
    )
    decision, _ = engine.before_tool("t", {})
    assert decision.reason == "first" and seen == []


def test_transforms_chain_in_order():
    def add(key):
        return lambda e: Decision("transform", key, {"arguments": {**e.arguments, key: True}})

    engine = HookEngine([spec(add("a"), name="a"), spec(add("b"), name="b")])
    decision, arguments = engine.before_tool("t", {"x": 1})
    assert decision.verdict == "transform" and arguments == {"x": 1, "a": True, "b": True}


def test_transform_does_not_alias_caller_arguments():
    original = {"nested": {"k": 1}}
    engine = HookEngine([spec(lambda e: Decision("transform", "", {"arguments": {"nested": {"k": 2}}}))])
    _, arguments = engine.before_tool("t", original)
    assert original == {"nested": {"k": 1}} and arguments == {"nested": {"k": 2}}


def test_post_tool_transform_rewrites_result():
    engine = HookEngine([spec(lambda e: Decision("transform", "", {"result": e.result.upper()}), POST_TOOL)])
    assert engine.after_tool("t", {}, "abc") == "ABC"


def test_matcher_selects_tools_and_stages():
    hit = []
    engine = HookEngine(
        [
            spec(lambda e: hit.append(e.tool) or Decision(), matcher="^write_"),
            spec(
                lambda e: hit.append("stage:" + e.stage) or Decision(), event=PRE_MODEL, matcher="generation$"
            ),
        ]
    )
    engine.before_tool("write_test_file", {})
    engine.before_tool("read_file", {})
    engine.notify(PRE_MODEL, "test_generation")
    engine.notify(PRE_MODEL, "requirements")
    assert hit == ["write_test_file", "stage:test_generation"]


def test_crashing_hook_on_an_enforcing_event_denies_and_is_audited():
    log = []

    def boom(event):
        raise RuntimeError("bug in hook")

    engine = HookEngine([spec(boom, name="boom")], audit=audit_into(log))
    decision, _ = engine.before_tool("t", {})
    assert decision.blocked and "boom" in decision.reason and "RuntimeError" in decision.reason
    assert [name for name, _ in log] == ["hook_error", "hook_decision"]


def test_crashing_hook_on_an_observer_event_is_recorded_not_enforced():
    log = []

    def boom(event):
        raise RuntimeError("bug")

    engine = HookEngine([spec(boom, STOP, "boom")], audit=audit_into(log))
    decision = engine.notify(STOP, reason="x")
    assert not decision.blocked and log and log[0][0] == "hook_error"


def test_a_deny_on_an_observer_event_does_not_block():
    log = []
    engine = HookEngine([spec(lambda e: Decision("deny", "meh"), STOP)], audit=audit_into(log))
    decision = engine.notify(STOP)
    assert decision.verdict == "deny" and decision.would_block and not decision.blocked
    assert log[0][1]["enforced"] is False


def test_ask_on_an_observer_event_never_reaches_the_approver():
    asked = []
    engine = HookEngine(
        [spec(lambda e: Decision("ask", "x"), STOP)], approver=lambda e, r: asked.append(r) or True
    )
    assert not engine.notify(STOP).blocked and asked == []


def test_hook_returning_garbage_is_a_failure():
    engine = HookEngine([spec(lambda e: 42, name="junk")])
    decision, _ = engine.before_tool("t", {})
    assert decision.blocked and "junk" in decision.reason


def test_ask_without_an_approver_is_a_deny():
    engine = HookEngine([spec(lambda e: Decision("ask", "download 17"), name="consent")])
    decision, _ = engine.before_tool("t", {})
    assert decision.blocked and "approval required" in decision.reason


def test_ask_with_an_approver_can_allow_or_deny():
    asked = []

    def approver(event, reason):
        asked.append(reason)
        return event.arguments.get("ok", False)

    engine = HookEngine([spec(lambda e: Decision("ask", "why"))], approver=approver)
    assert engine.before_tool("t", {"ok": True})[0].verdict == "allow"
    assert engine.before_tool("t", {"ok": False})[0].blocked
    assert asked == ["why", "why"]


def test_evaluate_only_records_but_never_blocks_or_transforms():
    log = []
    engine = HookEngine(
        [
            spec(lambda e: Decision("deny", "would block"), name="strict"),
            spec(lambda e: Decision("transform", "", {"arguments": {"changed": 1}}), name="rewrite"),
        ],
        mode="evaluate_only",
        audit=audit_into(log),
    )
    decision, arguments = engine.before_tool("t", {"a": 1})
    assert not decision.blocked and decision.would_block
    assert arguments == {"a": 1}  # the transform was not applied
    recorded = [f for name, f in log if name == "hook_decision"]
    assert recorded[0]["verdict"] == "deny" and recorded[0]["enforced"] is False


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError, match="mode"):
        HookEngine(mode="whatever")


def test_audit_records_only_interesting_decisions():
    log = []
    engine = HookEngine([spec(lambda e: Decision())], audit=audit_into(log))
    engine.before_tool("t", {})
    assert log == []


def test_decision_from_any():
    assert Decision.from_any(None).verdict == "allow"
    assert Decision.from_any({"verdict": "DENY", "reason": "r"}).verdict == "deny"
    with pytest.raises(ValueError, match="unknown verdict"):
        Decision.from_any({"verdict": "maybe"})
    with pytest.raises(TypeError):
        Decision.from_any("allow")


# --- command hooks: real subprocesses -------------------------------------------------------------------------


def script(tmp_path, body, name="hook.py"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return [sys.executable, str(path)]


def fire(handler, **fields):
    return handler(HookEvent(PRE_TOOL, "test_generation", "write_test_file", {"path": "a.py"}, **fields))


def test_command_hook_receives_the_event_as_json(tmp_path):
    argv = script(
        tmp_path,
        """
        import json, sys
        event = json.load(sys.stdin)
        print(json.dumps({"verdict": "deny", "reason": event["tool"] + ":" + event["arguments"]["path"]}))
        """,
    )
    decision = fire(command_handler(argv))
    assert decision.verdict == "deny" and decision.reason == "write_test_file:a.py"


def test_command_hook_exit_2_denies_with_stderr_reason(tmp_path):
    argv = script(tmp_path, "import sys; sys.stderr.write('not on fridays'); sys.exit(2)")
    decision = fire(command_handler(argv))
    assert decision.verdict == "deny" and decision.reason == "not on fridays"


def test_command_hook_silent_success_allows(tmp_path):
    assert fire(command_handler(script(tmp_path, "pass"))).verdict == "allow"


def test_command_hook_can_transform(tmp_path):
    argv = script(
        tmp_path,
        """
        import json
        print(json.dumps({"verdict": "transform", "payload": {"arguments": {"path": "safe.py"}}}))
        """,
    )
    engine = HookEngine([spec(command_handler(argv), name="cmd")])
    _, arguments = engine.before_tool("write_test_file", {"path": "a.py"})
    assert arguments == {"path": "safe.py"}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("import sys; sys.exit(1)", "exited 1"),
        ("print('not json')", "Expecting value"),
        ("import time; time.sleep(30)", "timed out"),
    ],
)
def test_broken_command_hook_denies_on_an_enforcing_event(tmp_path, body, expected):
    engine = HookEngine([spec(command_handler(script(tmp_path, body), timeout=1), name="cmd")])
    decision, _ = engine.before_tool("t", {})
    assert decision.blocked and expected in decision.reason


def test_command_hook_needs_an_argv_not_a_shell_line():
    with pytest.raises(ValueError, match="argv"):
        command_handler([])
    with pytest.raises(ValueError, match="argv"):
        command_handler("echo hi")  # type: ignore[arg-type]


def test_command_hook_does_not_inherit_the_environment_unless_given(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_FOR_TEST", "leak-me")
    argv = script(
        tmp_path,
        """
        import json, os
        print(json.dumps({"verdict": "deny", "reason": os.environ.get("SECRET_FOR_TEST", "absent")}))
        """,
    )
    decision = fire(command_handler(argv, env={"PATH": "", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}))
    assert decision.reason == "absent"


# --- python hooks --------------------------------------------------------------------------------------------


def test_python_hook_gets_a_dict_and_may_return_a_dict(tmp_path, monkeypatch):
    (tmp_path / "my_hook_mod.py").write_text(
        "def check(event):\n    return {'verdict': 'deny', 'reason': event['event'] + '/' + event['tool']}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    engine = HookEngine([spec(python_handler("my_hook_mod:check"), name="py")])
    decision, _ = engine.before_tool("write_test_file", {})
    assert decision.blocked and decision.reason == "PreToolUse/write_test_file"


@pytest.mark.parametrize("target", ["nocolon", ":fn", "mod:"])
def test_python_hook_target_must_be_module_colon_function(target):
    with pytest.raises(ValueError, match="package.module:function"):
        python_handler(target)


def test_python_hook_missing_module_or_function():
    with pytest.raises(ImportError):
        python_handler("definitely_not_a_module_xyz:fn")
    with pytest.raises(AttributeError):
        python_handler("json:no_such_function")

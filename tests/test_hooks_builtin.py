"""Built-in hooks: secret redaction, write scope, download consent, stage budget, audit."""

import json

import pytest

from maf_test_harness.hooks import (
    ON_ENV_ERROR,
    POST_TOOL,
    PRE_MODEL,
    STAGE_GATE,
    HookEngine,
)
from maf_test_harness.hooks import builtin as B

# credential-shaped values are assembled at run time so the repository secrets scan (CI) can stay strict
FAKE_KEY = "sk" + "-" + "abcdefghijklmnop1234"
FAKE_TOKEN = "ghp" + "_" + "abcdefghijklmnopqrstuvwx"


def engine(*hooks, **kwargs):
    return HookEngine(list(hooks), **kwargs)


# --- redaction ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        FAKE_KEY,
        FAKE_TOKEN,
        "AKIA" "ABCDEFGHIJKLMNOP",  # split so the repo secrets scan does not flag the fixture
        "Bearer abcdefghijklmnopqrstuvwxyz0123",
    ],
)
def test_token_shapes_are_redacted(secret):
    text, count = B.redact(f"before {secret} after")
    assert secret not in text and B.REDACTED in text and count == 1 and text.startswith("before")


@pytest.mark.parametrize(
    "line",
    ['password="hunter2hunter2"', "api_key: abcdef123456", "TOKEN=abcdefgh1234", "'secret': 'abcdefgh1234'"],
)
def test_keyed_secrets_lose_their_value_but_keep_the_key(line):
    text, count = B.redact(line)
    assert count == 1 and B.REDACTED in text and "abcdef" not in text and "hunter2" not in text
    assert text.split("=")[0].split(":")[0].strip("'\" ").lower() in {
        "password",
        "api_key",
        "token",
        "secret",
    }


def test_basic_credentials_are_redacted_only_in_an_authorization_context():
    prose = "Basic internationalization support"
    assert B.redact(prose) == (prose, 0)
    text, count = B.redact("Authorization: Basic dXNlcjp0b2tlbjEyMzQ1Ng==")
    assert text == f"Authorization: Basic {B.REDACTED}" and count == 1
    payload = json.dumps({"authorization": "Basic dXNlcjp0b2tlbjEyMzQ1Ng=="})
    text, count = B.redact(payload)
    assert json.loads(text) == {"authorization": f"Basic {B.REDACTED}"} and count == 1


def test_redacted_json_is_still_valid_json():
    payload = json.dumps({"password": "hunter2hunter2", "path": "a.py", "nested": {"token": "abcdefgh1234"}})
    text, count = B.redact(payload)
    parsed = json.loads(text)
    assert count == 2 and parsed["password"] == B.REDACTED and parsed["nested"]["token"] == B.REDACTED
    assert parsed["path"] == "a.py"


@pytest.mark.parametrize(
    "text", ["nothing secret here", "the token was refreshed", "password policy: strong", ""]
)
def test_ordinary_text_is_untouched(text):
    assert B.redact(text) == (text, 0)


def test_values_of_well_known_secret_environment_variables_are_redacted(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "plain-key-without-known-shape")
    text, count = B.redact("using plain-key-without-known-shape for the call")
    assert "plain-key" not in text and count == 1


def test_short_environment_values_are_not_treated_as_secrets(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    assert B.redact("abc is fine")[1] == 0


def test_redaction_hook_rewrites_arguments_and_results():
    hooked = engine(*B.secret_redaction())
    decision, arguments = hooked.before_tool(
        "write_test_file",
        {"path": "a.py", "content": f"KEY = '{FAKE_KEY}'", "list": ["Bearer " + "x" * 30]},
    )
    assert decision.verdict == "transform" and not decision.blocked
    assert "sk-abc" not in arguments["content"] and B.REDACTED in arguments["list"][0]
    assert arguments["path"] == "a.py"
    assert B.REDACTED in hooked.after_tool("read_file", {}, f"found {FAKE_TOKEN} in log")


def test_redaction_hook_leaves_clean_calls_alone():
    hooked = engine(*B.secret_redaction())
    decision, arguments = hooked.before_tool("read_file", {"path": "a.py"})
    assert decision.verdict == "allow" and arguments == {"path": "a.py"}
    assert hooked.after_tool("read_file", {}, "all clear") == "all clear"


# --- write scope ----------------------------------------------------------------------------------------------


@pytest.fixture
def scoped(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return engine(*B.path_scope({"writable_roots": [str(root)]})), root


def test_write_inside_the_root_is_allowed(scoped):
    hooked, _ = scoped
    assert not hooked.before_tool("write_test_file", {"path": "tests/test_a.py"})[0].blocked


@pytest.mark.parametrize("path", ["../outside.py", "tests/../../outside.py", "a/../../../etc/passwd"])
def test_dotdot_escapes_are_denied(scoped, path):
    hooked, _ = scoped
    decision, _ = hooked.before_tool("write_test_file", {"path": path})
    assert decision.blocked and "outside the writable" in decision.reason


def test_absolute_path_elsewhere_is_denied_and_inside_is_allowed(scoped, tmp_path):
    hooked, root = scoped
    assert hooked.before_tool("delete_file", {"path": str(tmp_path / "elsewhere.py")})[0].blocked
    assert not hooked.before_tool("delete_file", {"path": str(root / "tests" / "t.py")})[0].blocked


def test_no_configured_root_denies_every_write():
    decision, _ = engine(*B.path_scope({})).before_tool("write_test_file", {"path": "a.py"})
    assert decision.blocked and "no writable directory" in decision.reason


def test_read_tools_are_not_subject_to_the_write_scope(scoped):
    hooked, _ = scoped
    assert not hooked.before_tool("read_file", {"path": "../anything"})[0].blocked


def test_scope_sees_context_changes_made_after_the_hook_was_built(tmp_path):
    context = {}
    hooked = engine(*B.path_scope(context))
    assert hooked.before_tool("write_test_file", {"path": "a.py"})[0].blocked
    context["writable_roots"] = [str(tmp_path)]
    assert not hooked.before_tool("write_test_file", {"path": "a.py"})[0].blocked


# --- download consent ----------------------------------------------------------------------------------------


def test_download_needs_consent_by_default():
    hooked = engine(*B.download_consent({}))
    decision, _ = hooked.before_tool("provision_toolchain", {"kind": "jdk", "version": "17"})
    assert decision.blocked and "jdk 17" in decision.reason


def test_download_allowed_after_the_environment_approval():
    hooked = engine(*B.download_consent({"allow_downloads": True}))
    assert not hooked.before_tool("provision_toolchain", {"kind": "jdk", "version": "17"})[0].blocked


def test_download_consent_can_come_from_an_approver():
    hooked = engine(*B.download_consent({}), approver=lambda event, reason: "jdk" in reason)
    assert not hooked.before_tool("provision_toolchain", {"kind": "jdk", "version": "17"})[0].blocked
    assert hooked.before_tool("provision_toolchain", {"kind": "maven", "version": "3.9.9"})[0].blocked


def test_download_consent_only_concerns_provisioning():
    assert not engine(*B.download_consent({})).before_tool("read_file", {"path": "a"})[0].blocked


# --- stage budget -------------------------------------------------------------------------------------------


def test_stage_budget_is_off_without_a_cap():
    assert B.stage_budget({}) == []


def test_stage_budget_denies_a_model_call_after_the_cap():
    hooked = engine(*B.stage_budget({"per_stage_tokens": 1000}))
    assert not hooked.notify(PRE_MODEL, "test_generation", stage_tokens=999).blocked
    over = hooked.notify(PRE_MODEL, "test_generation", stage_tokens=1000)
    assert over.blocked and "1,000" in over.reason


# --- audit -------------------------------------------------------------------------------------------------


def test_gates_and_errors_are_audited_without_the_bulky_state():
    log = []
    hooked = engine(*B.audit_events(lambda event, **f: log.append((event, f))))
    hooked.notify(STAGE_GATE, "requirements", kind="review", state={"huge": "x" * 1000})
    hooked.notify(ON_ENV_ERROR, "environment_setup", status="setup_failed")
    assert [f["hook_event"] for _, f in log] == [STAGE_GATE, ON_ENV_ERROR]
    assert all(event == "hook_event" for event, _ in log)
    assert "state" not in log[0][1]["data"] and log[0][1]["data"]["kind"] == "review"


def test_audit_hook_is_a_noop_without_an_audit_function():
    engine(*B.audit_events(None)).notify(STAGE_GATE, "x")  # must not raise


# --- selection -----------------------------------------------------------------------------------------------


def names(hooks):
    return {h.name for h in hooks}


def test_every_builtin_is_on_by_default():
    hooks = B.build_builtins({}, {}, lambda *a, **k: None)
    assert {
        "builtin:secret_redaction",
        "builtin:path_scope",
        "builtin:download_consent",
        "builtin:audit",
    } <= names(hooks)


def test_builtins_can_be_switched_off_individually():
    hooks = B.build_builtins({"secret_redaction": False, "audit": False}, {}, None)
    assert "builtin:secret_redaction" not in names(hooks) and "builtin:audit" not in names(hooks)
    assert "builtin:path_scope" in names(hooks)


def test_stage_budget_builtin_takes_options():
    hooks = B.build_builtins({"stage_budget": {"per_stage_tokens": 5}}, {}, None)
    assert "builtin:stage_budget" in names(hooks)
    assert "builtin:stage_budget" not in names(B.build_builtins({}, {}, None))


def test_post_tool_event_constant_is_used_for_results():
    assert any(h.event == POST_TOOL for h in B.secret_redaction())

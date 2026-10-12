"""Desktop hardening that cannot run here (no Electron window in the gate): the packaging fuses and the shell's source
rules (F11 sender checks, frame navigation, permissions; F4 no MCP secret in the spawn environment)."""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
MAIN = (REPO / "desktop" / "src" / "main.ts").read_text(encoding="utf-8")


def test_electron_fuses_are_set_and_runasnode_stays_for_the_ui_spawn():
    config = yaml.safe_load((REPO / "desktop" / "electron-builder.yml").read_text(encoding="utf-8"))
    fuses = config["electronFuses"]
    assert fuses["runAsNode"] is True  # the UI runs as process.execPath with ELECTRON_RUN_AS_NODE=1
    assert fuses["enableNodeOptionsEnvironmentVariable"] is False
    assert fuses["enableNodeCliInspectArguments"] is False
    assert fuses["onlyLoadAppFromAsar"] is True
    assert "ELECTRON_RUN_AS_NODE: '1'" in MAIN  # the reason runAsNode cannot be turned off


def test_every_ipc_handler_goes_through_the_sender_check():
    assert len(re.findall(r"ipcMain\.handle\(", MAIN)) == 1  # only the guarded helper registers handlers
    assert "return listener(...(args as A));" in MAIN
    assert "if (!trustedSender(e))" in MAIN
    assert "!isEvidenceUrl(url)" in MAIN


def test_frames_cannot_navigate_off_the_app_origin_and_permissions_are_denied():
    assert "will-frame-navigate" in MAIN and "details.preventDefault()" in MAIN
    assert "setPermissionCheckHandler(() => false)" in MAIN
    assert "setPermissionRequestHandler((_wc, _perm, cb) => cb(false))" in MAIN


def test_mcp_secrets_never_enter_the_spawn_environment():
    keystore = (REPO / "desktop" / "src" / "keystore.ts").read_text(encoding="utf-8")
    assert "HARNESS_MCP_SECRET_" not in keystore  # no env name is built on the desktop side any more
    assert "store.env()" in MAIN and "HARNESS_MCP_SECRET" not in MAIN
    assert "provisionMcp(apiPort, token)" in MAIN  # secrets arrive by POST, after the fingerprint check

"""Desktop packaging facts that the gate can check without an Electron window: the fuses and the macOS re-sign.
The window, IPC and permission rules are tested with node:test in desktop/src/guards.test.ts."""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_electron_fuses_are_set_and_macos_is_resigned_after_the_flip():
    config = yaml.safe_load((REPO / "desktop" / "electron-builder.yml").read_text(encoding="utf-8"))
    fuses = config["electronFuses"]
    # identity null skips electron-builder's signing; the fuse flip then invalidates Electron's ad-hoc signature on
    # arm64 macOS, so the fuse step must re-sign ad hoc.
    assert config["mac"]["identity"] is None
    assert fuses["resetAdHocDarwinSignature"] is True
    # runAsNode stays on: the UI is spawned as process.execPath with ELECTRON_RUN_AS_NODE=1.
    assert fuses["runAsNode"] is True
    assert fuses["enableNodeOptionsEnvironmentVariable"] is False
    assert fuses["enableNodeCliInspectArguments"] is False
    assert fuses["onlyLoadAppFromAsar"] is True

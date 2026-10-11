"""Single resolver for bundled (read-only) resources and per-user (writable) data.

Source checkout: both live under the repository root (``.maf-harness`` for data). Frozen by PyInstaller: resources come
from ``sys._MEIPASS`` and data from the per-OS user directory. ``HARNESS_RESOURCE_ROOT`` / ``HARNESS_DATA_DIR`` override.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[2]


def _env_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    env = _env_path("HARNESS_RESOURCE_ROOT")
    if env:
        return env
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).resolve().parent)
    return _SOURCE_ROOT


def user_data_root() -> Path:
    env = _env_path("HARNESS_DATA_DIR")
    if env:
        return env
    if not is_frozen():
        return _SOURCE_ROOT / ".maf-harness"
    if sys.platform == "win32":
        return Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming") / "AgenticQE"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AgenticQE"
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "agentic-qe"


def skills_dir() -> Path:
    return resource_root() / "skills"


def prompts_dir() -> Path:
    return resource_root() / "prompts"


def runs_dir() -> Path:
    return _env_path("HARNESS_RUNS_DIR") or user_data_root() / "runs"


def cache_dir() -> Path:
    return user_data_root() / "cache"


def plugin_dir() -> Path:
    return _env_path("HARNESS_PLUGIN_DIR") or user_data_root() / "plugins"


def env_file() -> Path:
    return user_data_root() / ".env" if is_frozen() else _SOURCE_ROOT / ".env"

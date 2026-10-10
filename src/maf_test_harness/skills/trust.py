"""Trust policy for skill packages.

* A repository skill with an entry in ``skills/SKILLS.lock.json`` whose files still match is **verified**.
* Every other skill (a repository folder without a lock entry, or anything in the user plugin directory) is
  **unverified**. Unverified skills are neither selected nor loaded, and their scripts never run, until a user
  **approves** them. An approval records ``{name, sha256 of every file, approved_at}`` in the approvals store; any later
  file change makes the hash differ and the skill is unverified again.
* Approved scripts run only through the same hash-checking ``HarnessScriptRunner`` (model tier); symlinks anywhere in
  a package make it unapprovable.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .. import paths
from .registry import NAME_RE, discover_skills, lint_plugins, lint_skill, load_lock, load_plugin, verify_lock

MAX_IMPORT_BYTES = 20 * 1024 * 1024
MAX_IMPORT_FILES = 500
_LOCK = threading.Lock()  # approvals read-modify-write and plugin installs (ThreadingHTTPServer)
_WIN_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.I)


def plugin_dir() -> Path:
    return paths.plugin_dir()


def approvals_path() -> Path:
    env = os.getenv("HARNESS_PLUGIN_APPROVALS", "").strip()
    # Outside the target workspace on purpose: a repository under test must not be able to pre-approve its own skills.
    return Path(env).expanduser() if env else Path.home() / ".maf-harness" / "plugins.lock.json"


def load_approvals() -> dict[str, dict]:
    try:
        data = json.loads(approvals_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (
        data.get("approved", {}) if isinstance(data, dict) and isinstance(data.get("approved"), dict) else {}
    )


def full_tree_sha256(directory: Path) -> str:
    """Hash of every file (path + bytes) with no exclusions, so bytecode or caches cannot hide from an approval."""
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(directory).rglob("*") if p.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def has_bytecode(directory: Path) -> bool:
    return any("__pycache__" in p.parts or p.suffix in (".pyc", ".pyo") for p in Path(directory).rglob("*"))


def _bad_part(part: str) -> bool:
    """Windows-hostile path components (also unsafe on other systems when a package is moved around)."""
    return bool(
        ":" in part or part != part.rstrip(". ") or _WIN_RESERVED.match(part.split(".")[0]) or "\0" in part
    )


def has_symlink(directory: Path) -> bool:
    return any(p.is_symlink() for p in [directory, *directory.rglob("*")])


def source_of(directory: Path, repo_skills: Path) -> str:
    return "repo" if Path(directory).resolve().parent == Path(repo_skills).resolve() else "plugin"


def is_verified(repo_skills: Path, directory: Path) -> bool:
    """Repository skill that is in the lock and unmodified."""
    name = Path(directory).name
    return (
        source_of(directory, repo_skills) == "repo"
        and name in load_lock(repo_skills).get("skills", {})
        and not verify_lock(repo_skills, [name])
    )


def is_approved(directory: Path) -> bool:
    directory = Path(directory)
    entry = load_approvals().get(directory.name)
    if not entry or not directory.is_dir() or has_symlink(directory):
        return False
    return not has_bytecode(directory) and entry.get("sha256") == full_tree_sha256(directory)


def trust(repo_skills: Path, directory: Path) -> dict:
    verified = is_verified(repo_skills, directory)
    source = source_of(directory, repo_skills)
    approved = not verified and is_approved(directory)
    if approved and source == "plugin" and plugin_policy_problems(load_plugin(directory)):
        approved = False  # a hand-dropped primary/host/entrypoint plugin never becomes trusted
    return {
        "source": source,
        "verified": verified,
        "approved": approved,
        "trusted": verified or approved,
    }


def find_skill(repo_skills: Path, name: str) -> Path:
    for plugin in discover_skills([repo_skills, plugin_dir()]):
        if plugin.name == name:
            return plugin.path
    raise FileNotFoundError(f"Skill not found: {name}")


def plugin_policy_problems(plugin) -> list[str]:
    """Plugin-sourced skills are instruction packages: companions only, model kind, no entrypoint."""
    return [
        *(
            ["plugins may only be companion skills (role: primary is not allowed)"]
            if plugin.role == "primary"
            else []
        ),
        *(["plugins must be kind 'model'"] if plugin.kind != "model" else []),
        *(["plugins cannot declare an entrypoint"] if plugin.entrypoint else []),
    ]


def approve(repo_skills: Path, name: str) -> dict:
    """User action: record the current hash of an unverified skill. Verified skills need no approval."""
    directory = find_skill(repo_skills, name)
    if is_verified(repo_skills, directory):
        raise ValueError(f"{name} is verified by the repository lock; no approval needed.")
    if name in load_lock(repo_skills).get("skills", {}):
        raise ValueError(f"{name} differs from the repository lock and cannot be approved.")
    if has_symlink(directory):
        raise ValueError(f"{name} contains symbolic links and cannot be approved.")
    if has_bytecode(directory):
        raise ValueError(f"{name} contains compiled bytecode (__pycache__/.pyc) and cannot be approved.")
    report = lint_skill(directory)
    everything = discover_skills([repo_skills, plugin_dir()])
    plugin = next(p for p in everything if p.name == name)
    if source_of(directory, repo_skills) == "plugin":
        policy = plugin_policy_problems(plugin)
        if policy:  # checked before lint_plugins, which would import an entrypoint
            raise ValueError(f"{name} is not allowed as a plugin: {'; '.join(policy)}")
    others = [p for p in everything if p.name != name and source_of(p.path, repo_skills) == "repo"]
    problems = [*report.errors, *lint_plugins([*others, plugin]).get(name, [])]
    if problems:
        raise ValueError(f"{name} is invalid: {'; '.join(problems)}")
    with _LOCK:
        approvals = load_approvals()
        approvals[name] = {
            "name": name,
            "sha256": full_tree_sha256(directory),
            "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        path = approvals_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".plugins-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"approved": approvals}, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    return trust(repo_skills, directory)


# ---- import ------------------------------------------------------------------------------------------------------


def _extract_zip(data: bytes, target: Path) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a valid zip file.") from exc
    infos = archive.infolist()
    if not infos:
        raise ValueError("Archive is empty")
    if len(infos) > MAX_IMPORT_FILES or sum(i.file_size for i in infos) > MAX_IMPORT_BYTES:
        raise ValueError("Skill archive is too large (max 20 MB, 500 files).")
    total = 0
    for info in infos:
        name = info.filename
        parts = name.replace("\\", "/").split("/")
        if (
            name.startswith(("/", "\\"))
            or ".." in parts
            or any(_bad_part(p) for p in parts if p)
            or info.flag_bits & 0x1
        ):
            raise ValueError(f"Unsafe path in archive: {name!r}")
        if "__pycache__" in parts or name.endswith((".pyc", ".pyo")):
            raise ValueError(f"Compiled bytecode is not allowed in a skill archive: {name!r}")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError(f"Symbolic links are not allowed in a skill archive: {name!r}")
        dest = (target / name).resolve()
        if not dest.is_relative_to(target.resolve()):
            raise ValueError(f"Unsafe path in archive: {name!r}")
        if info.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, dest.open("wb") as out:
            while chunk := src.read(65536):  # count real bytes: headers can lie
                total += len(chunk)
                if total > MAX_IMPORT_BYTES:
                    raise ValueError("Skill archive is too large (decompressed size over 20 MB).")
                out.write(chunk)


def _copy_folder(source: Path, target: Path) -> None:
    if not source.is_dir() or has_symlink(source):
        raise ValueError("Skill folder must be a real directory without symbolic links.")
    files = [p for p in source.rglob("*") if p.is_file()]
    if len(files) > MAX_IMPORT_FILES or sum(p.stat().st_size for p in files) > MAX_IMPORT_BYTES:
        raise ValueError("Skill folder is too large (max 20 MB, 500 files).")
    shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns(".git", ".DS_Store"))
    if has_symlink(target):  # the source changed between the check and the copy
        raise ValueError("Skill folder must be a real directory without symbolic links.")
    if has_bytecode(target):
        raise ValueError("Compiled bytecode (__pycache__/.pyc) is not allowed in a skill.")
    if any(_bad_part(part) for p in target.rglob("*") for part in p.relative_to(target).parts):
        raise ValueError("Unsafe file name in skill folder.")


def import_skill(repo_skills: Path, *, zip_base64: str | None = None, folder: str | None = None) -> dict:
    """Validate a skill package (zip or local folder) and copy it into the plugin directory, unverified."""
    if bool(zip_base64) == bool(folder):
        raise ValueError("Provide exactly one of zip_base64 or path.")
    with tempfile.TemporaryDirectory(prefix="skill-import-") as tmp:
        staging = Path(tmp).resolve() / "pkg"
        if zip_base64:
            try:
                data = base64.b64decode(zip_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("zip_base64 is not valid base64.") from exc
            if len(data) > MAX_IMPORT_BYTES:
                raise ValueError("Skill archive is too large (max 20 MB).")
            _extract_zip(data, staging)
            tops = [p for p in staging.iterdir()]
            if not (staging / "SKILL.md").is_file() and len(tops) == 1 and (tops[0] / "SKILL.md").is_file():
                staging = tops[0]
        else:
            _copy_folder(Path(folder).expanduser().resolve(), staging)
        try:
            meta = yaml.safe_load((staging / "plugin.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
            raise ValueError("plugin.yaml is missing or not valid YAML.") from exc
        name = meta.get("name") if isinstance(meta, dict) else None
        if not isinstance(name, str) or len(name) > 64 or not NAME_RE.fullmatch(name):
            raise ValueError(
                "plugin.yaml name must be 1-64 chars of lowercase letters/digits with single hyphens."
            )
        named = Path(tmp).resolve() / "out" / name
        named.parent.mkdir()
        staging.rename(named)
        report = lint_skill(named)
        # host plugins would name an importable entrypoint; imported skills are instruction packages only
        if isinstance(meta, dict) and (meta.get("entrypoint") or meta.get("kind") == "host"):
            raise ValueError("Imported skills must be kind 'model' without an entrypoint.")
        plugin = discover_skills([named.parent])[0]
        problems = [*report.errors, *plugin.errors, *plugin_policy_problems(plugin)]
        if problems:
            raise ValueError("Invalid skill: " + "; ".join(problems))
        if (repo_skills / name).exists():
            raise ValueError(f"A repository skill named {name} already exists.")
        dest_root = plugin_dir()
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / name
        with _LOCK:
            partial = Path(tempfile.mkdtemp(dir=dest_root, prefix=f".{name}.importing-"))
            shutil.rmtree(partial)
            shutil.copytree(named, partial)
            aside = None
            if dest.exists():
                aside = Path(tempfile.mkdtemp(dir=dest_root, prefix=f".{name}.old-"))
                shutil.rmtree(aside)
                dest.rename(aside)
            partial.rename(dest)
        if aside:
            shutil.rmtree(aside, ignore_errors=True)
    return {"name": name, **trust(repo_skills, dest)}

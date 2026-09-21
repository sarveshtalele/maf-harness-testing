"""Find toolchains on the machine or provision them into a private cache, without touching the system.

Supported kinds: ``jdk`` (Eclipse Temurin), ``maven``, ``gradle``, ``dotnet`` (SDK), ``python`` (found only; a missing
interpreter is provisioned through ``uv`` when it is installed). Downloads are checksum-verified, extracted safely and
recorded in a provenance file. Nothing here edits PATH or any system setting: a toolchain is used by handing its
environment variables (``Toolchain.env``) to the commands the harness runs.

``HARNESS_TOOLCHAIN_DIR`` moves the cache, ``HARNESS_TOOLCHAIN_MIRROR`` sends every download and metadata request to a
mirror that serves the same paths (corporate proxies, offline machines).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from filelock import FileLock

from .. import platform_compat
from .spec import at_least, version_tuple

PINNED_MAVEN = {"3": "3.9.9", "3.9": "3.9.9", "3.8": "3.8.8", "3.6": "3.6.3"}
ADOPTIUM = "https://api.adoptium.net"
APACHE = "https://archive.apache.org"
GRADLE = "https://services.gradle.org"
DOTNET = "https://builds.dotnet.microsoft.com"


class ToolchainError(RuntimeError):
    """A toolchain could not be found or provisioned; the message says what to do."""


@dataclass
class Toolchain:
    kind: str
    version: str
    home: Path
    source: str  # system | cache

    def bin_dirs(self) -> list[Path]:
        if self.kind == "python":  # home is the interpreter itself, nothing to put on PATH
            return []
        if self.kind == "dotnet":
            return [self.home]
        return [self.home / "bin"]

    def executable(self, name: str) -> Path | None:
        # Windows cannot start an extensionless script (Maven ships both `mvn` and `mvn.cmd`): prefer real launchers.
        order = (
            (f"{name}.exe", f"{name}.cmd", f"{name}.bat", name)
            if platform_compat.IS_WINDOWS
            else (name, f"{name}.exe", f"{name}.cmd", f"{name}.bat")
        )
        for folder in self.bin_dirs():
            for candidate in order:
                path = folder / candidate
                if path.is_file():
                    return path
        return None

    def env(self, cache: Path | None = None) -> dict[str, str]:
        """Environment variables that make this toolchain the one commands use (PATH is prepended, not replaced)."""
        if self.kind == "python":
            return {}
        path = os.pathsep.join([*(str(d) for d in self.bin_dirs()), os.environ.get("PATH", "")])
        env = {"PATH": path}
        if self.kind == "jdk":
            env["JAVA_HOME"] = str(self.home)
        elif self.kind == "maven":
            env["MAVEN_HOME"] = str(self.home)
        elif self.kind == "gradle":
            env["GRADLE_HOME"] = str(self.home)
        elif self.kind == "dotnet":
            root = (cache or cache_root()) / "dotnet-state"
            env.update(
                DOTNET_ROOT=str(self.home),
                DOTNET_CLI_HOME=str(root / "cli"),
                NUGET_PACKAGES=str(root / "nuget"),
                DOTNET_CLI_TELEMETRY_OPTOUT="1",
                DOTNET_NOLOGO="1",
                DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
            )
        return env

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "version": self.version, "home": str(self.home), "source": self.source}


# ---------------------------------------------------------------------------------------------- platform
def platform_key() -> tuple[str, str]:
    """(os, arch) in the vocabulary of the download catalogues: windows|mac|linux, x64|aarch64."""
    system = {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(platform.system(), "linux")
    machine = platform.machine().lower()
    arch = "aarch64" if machine in {"arm64", "aarch64"} else "x64"
    return system, arch


def cache_root() -> Path:
    return Path(os.environ.get("HARNESS_TOOLCHAIN_DIR") or Path.home() / ".maf-harness" / "toolchains")


# ------------------------------------------------------------------------------------ local detection
def _run(argv: list[str], timeout: float = 20) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def parse_java_version(output: str) -> str | None:
    match = re.search(r'version "([^"]+)"', output)
    if not match:
        return None
    parts = version_tuple(match.group(1))
    return ".".join(str(p) for p in parts[:3]) if parts else None


def _java_home_of(java: Path) -> Path:
    resolved = java.resolve()
    return resolved.parent.parent


def detect_jdks() -> list[Toolchain]:
    found: dict[str, Toolchain] = {}
    candidates: list[Path] = []
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]) / "bin" / ("java.exe" if os.name == "nt" else "java"))
    if which := shutil.which("java"):
        candidates.append(Path(which))
    for java in candidates:
        if not java.is_file():
            continue
        version = parse_java_version(_run([str(java), "-version"]))
        home = _java_home_of(java)
        has_compiler = any((home / "bin" / n).is_file() for n in ("javac", "javac.exe"))
        if version and has_compiler:  # a JRE cannot compile: not a usable JDK
            found.setdefault(str(home), Toolchain("jdk", version, home, "system"))
    return list(found.values())


def detect_maven() -> list[Toolchain]:
    mvn = shutil.which("mvn")
    match = re.search(r"Apache Maven ([\d.]+)", _run([mvn, "-v"])) if mvn else None
    if not (mvn and match):
        return []
    return [Toolchain("maven", match.group(1), Path(mvn).resolve().parent.parent, "system")]


def detect_gradle() -> list[Toolchain]:
    gradle = shutil.which("gradle")
    match = re.search(r"Gradle ([\d.]+)", _run([gradle, "-v"])) if gradle else None
    if not (gradle and match):
        return []
    return [Toolchain("gradle", match.group(1), Path(gradle).resolve().parent.parent, "system")]


def detect_dotnet() -> list[Toolchain]:
    dotnet = shutil.which("dotnet")
    found = []
    if dotnet:
        for line in _run([dotnet, "--list-sdks"]).splitlines():
            match = re.match(r"\s*(\d+\.\d+\.\d+\S*)\s+\[(.+)\]", line)
            if match:
                found.append(Toolchain("dotnet", match.group(1), Path(match.group(2)).parent, "system"))
    return found


def detect_pythons() -> list[Toolchain]:
    found = {
        sys.executable: Toolchain(
            "python", ".".join(map(str, sys.version_info[:3])), Path(sys.executable), "system"
        )
    }
    for minor in range(8, 15):
        name = f"python3.{minor}"
        if path := shutil.which(name):
            out = (
                _run([path, "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"])
                .strip()
                .splitlines()
            )
            if out and re.fullmatch(r"\d+\.\d+\.\d+", out[0]):
                found.setdefault(path, Toolchain("python", out[0], Path(path), "system"))
    if platform_compat.IS_WINDOWS and (py := shutil.which("py")):
        for line in _run([py, "-0p"]).splitlines():
            match = re.search(r"-V:?(\d+\.\d+)\S*\s+\*?\s*(\S.*python\S*)$", line.strip(), re.I) or re.search(
                r"-(\d+\.\d+)\S*\s+\*?\s*(\S.*)$", line.strip()
            )
            if match and Path(match.group(2)).is_file():
                found.setdefault(
                    match.group(2), Toolchain("python", match.group(1), Path(match.group(2)), "system")
                )
    return list(found.values())


DETECTORS: dict[str, Callable[[], list[Toolchain]]] = {
    "jdk": detect_jdks,
    "maven": detect_maven,
    "gradle": detect_gradle,
    "dotnet": detect_dotnet,
    "python": detect_pythons,
}


def satisfies(kind: str, have: str, want: str) -> bool:
    """Is an installed ``have`` good enough for a requirement of ``want``?"""
    have_v, want_v = version_tuple(have), version_tuple(want)
    if not have_v or not want_v:
        return False
    if kind in {"jdk", "gradle", "maven"}:
        return at_least(have, want) if kind == "jdk" else have_v[: len(want_v)] >= want_v
    if kind == "python":
        return have_v[0] == want_v[0] and have_v[1] >= (want_v[1] if len(want_v) > 1 else 0)
    if kind == "dotnet":  # an SDK builds anything up to its own major.minor
        return have_v[:2] >= want_v[:2]
    return False


# ---------------------------------------------------------------------------------------------- cache
def _dir_for(kind: str, version: str, root: Path) -> Path:
    system, arch = platform_key()
    return root / kind / f"{version}-{system}-{arch}"


def _home_in(kind: str, folder: Path) -> Path | None:
    """The toolchain home inside an extracted archive (archives usually add one wrapper directory)."""
    marker = {"jdk": "javac", "maven": "mvn", "gradle": "gradle", "dotnet": "dotnet"}[kind]
    for depth in range(4):
        for path in [folder, *folder.glob("/".join(["*"] * depth))] if depth else [folder]:
            if not path.is_dir():
                continue
            bin_dir = path if kind == "dotnet" else path / "bin"
            if any((bin_dir / f"{marker}{ext}").is_file() for ext in ("", ".exe", ".cmd", ".bat")):
                return path
    mac_home = next(folder.glob("**/Contents/Home"), None) if kind == "jdk" else None
    return mac_home


def cached(kind: str, want: str, root: Path | None = None) -> Toolchain | None:
    root = root or cache_root()
    base = root / kind
    best: Toolchain | None = None
    for entry in sorted(base.glob("*")) if base.is_dir() else []:
        version = entry.name.rsplit("-", 2)[0]
        if entry.name != _dir_for(kind, version, root).name or not (entry / ".complete").is_file():
            continue
        home = _home_in(kind, entry)
        if home and satisfies(kind, version, want):
            candidate = Toolchain(kind, version, home, "cache")
            if best is None or version_tuple(version) < version_tuple(best.version):  # closest fit
                best = candidate
    return best


def find(kind: str, want: str, root: Path | None = None) -> Toolchain | None:
    """A toolchain that already satisfies ``want``: the system's first, then the cache."""
    for candidate in DETECTORS[kind]():
        if satisfies(kind, candidate.version, want):
            return candidate
    return cached(kind, want, root) if kind != "python" else None


# ---------------------------------------------------------------------------------------------- network
Fetch = Callable[[str], bytes]


def apply_mirror(url: str, mirror: str | None = None) -> str:
    mirror = mirror if mirror is not None else os.environ.get("HARNESS_TOOLCHAIN_MIRROR")
    if not mirror:
        return url
    base, source = urlsplit(mirror), urlsplit(url)
    return urlunsplit((base.scheme, base.netloc, (base.path.rstrip("/") + source.path), source.query, ""))


def http_get(url: str, timeout: float = 60) -> bytes:
    request = urllib.request.Request(apply_mirror(url), headers={"User-Agent": "maf-test-harness"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # HTTP(S)_PROXY is honoured
        return response.read()


@dataclass
class Download:
    kind: str
    version: str
    url: str
    checksum: str  # hex digest
    algorithm: str  # sha256 | sha512
    size: int | None = None
    archive: str = "zip"  # zip | tar.gz
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "version": self.version,
            "url": self.url,
            "size": self.size,
            "source": self.source,
        }


def _archive_kind(name: str) -> str:
    return "tar.gz" if name.endswith((".tar.gz", ".tgz")) else "zip"


def resolve_download(kind: str, want: str, fetch: Fetch = http_get) -> Download:
    """Where to download ``kind`` >= ``want`` for this platform (queries the vendor's metadata)."""
    system, arch = platform_key()
    if kind == "jdk":
        major = version_tuple(want)[0]
        url = (
            f"{ADOPTIUM}/v3/assets/latest/{major}/hotspot?architecture={arch}&image_type=jdk&os={system}"
            "&vendor=eclipse"
        )
        items = json.loads(fetch(url))
        if not items:
            raise ToolchainError(f"Eclipse Temurin has no JDK {major} for {system}/{arch}.")
        package = items[0]["binary"]["package"]
        return Download(
            kind,
            items[0].get("version", {}).get("semver", str(major)).split("+")[0],
            package["link"],
            package["checksum"],
            "sha256",
            package.get("size"),
            _archive_kind(package["name"]),
            "Eclipse Temurin",
        )
    if kind == "maven":
        version = PINNED_MAVEN.get(
            ".".join(map(str, version_tuple(want)[:2])), want if len(version_tuple(want)) >= 3 else "3.9.9"
        )
        url = f"{APACHE}/dist/maven/maven-{version[0]}/{version}/binaries/apache-maven-{version}-bin.zip"
        digest = fetch(url + ".sha512").decode().split()[0]
        return Download(kind, version, url, digest, "sha512", None, "zip", "Apache Maven")
    if kind == "gradle":
        version = want
        url = f"{GRADLE}/distributions/gradle-{version}-bin.zip"
        digest = fetch(url + ".sha256").decode().split()[0]
        return Download(kind, version, url, digest, "sha256", None, "zip", "Gradle")
    if kind == "dotnet":
        parts = version_tuple(want)
        channel = f"{parts[0]}.{parts[1] if len(parts) > 1 else 0}"
        meta = json.loads(fetch(f"{DOTNET}/dotnet/release-metadata/{channel}/releases.json"))
        rid = {"windows": "win", "mac": "osx", "linux": "linux"}[
            system
        ] + f"-{'arm64' if arch == 'aarch64' else 'x64'}"
        for release in meta.get("releases", []):
            for sdk in [release.get("sdk") or {}, *(release.get("sdks") or [])]:
                for entry in sdk.get("files", []):
                    name = entry.get("name", "")
                    if (
                        entry.get("rid") == rid
                        and name.endswith((".zip", ".tar.gz"))
                        and "sdk" in name.lower()
                    ):
                        return Download(
                            kind,
                            sdk.get("version", channel),
                            entry["url"],
                            entry["hash"],
                            "sha512",
                            None,
                            _archive_kind(name),
                            "Microsoft .NET",
                        )
        raise ToolchainError(f".NET SDK {channel} has no archive for {rid} in the release metadata.")
    raise ToolchainError(f"Cannot provision {kind}.")


def describe(kind: str, want: str) -> dict[str, str]:
    """Where a missing toolchain would come from (shown in the approval; no network needed)."""
    origin = {
        "jdk": "Eclipse Temurin via api.adoptium.net",
        "maven": "Apache Maven via archive.apache.org",
        "gradle": "Gradle via services.gradle.org",
        "dotnet": "Microsoft .NET via builds.dotnet.microsoft.com",
        "python": "uv python install (needs uv on PATH)",
    }[kind]
    return {"kind": kind, "version": want, "source": origin}


def _verify_and_download(download: Download, target: Path, opener: Fetch | None) -> None:
    digest = hashlib.new(download.algorithm)
    target.parent.mkdir(parents=True, exist_ok=True)
    if opener is not None:
        data = opener(download.url)
        digest.update(data)
        target.write_bytes(data)
    else:
        request = urllib.request.Request(
            apply_mirror(download.url), headers={"User-Agent": "maf-test-harness"}
        )
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as out:
            while chunk := response.read(1 << 20):
                digest.update(chunk)
                out.write(chunk)
    if digest.hexdigest().lower() != download.checksum.lower():
        target.unlink(missing_ok=True)
        raise ToolchainError(
            f"Checksum mismatch for {download.url}: expected {download.checksum[:16]}..., got {digest.hexdigest()[:16]}...; "
            "the download was discarded."
        )


def _extract(archive: Path, kind: str, destination: Path) -> None:
    destination = destination.resolve()

    def check(name: str) -> None:
        if not (destination / name).resolve().is_relative_to(destination):
            raise ToolchainError(f"Archive entry escapes the target directory: {name}")

    if kind == "zip":
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                check(name)
            bundle.extractall(destination)
            for info in bundle.infolist():  # zip does not keep the executable bit
                if not platform_compat.IS_WINDOWS and not info.is_dir():
                    (destination / info.filename).chmod(
                        0o755 if "/bin/" in info.filename or info.filename.endswith(".sh") else 0o644
                    )
    else:
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                check(member.name)
                if member.issym() or member.islnk():
                    check(str(Path(member.name).parent / member.linkname))
            bundle.extractall(destination)


def provision(download: Download, root: Path | None = None, opener: Fetch | None = None) -> Toolchain:
    """Download, verify, extract and register one toolchain in the cache (idempotent, safe to run concurrently)."""
    root = root or cache_root()
    folder = _dir_for(download.kind, download.version, root)
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root / ".lock")):
        if not (folder / ".complete").is_file():
            shutil.rmtree(folder, ignore_errors=True)
            staging = root / ".staging" / f"{download.kind}-{download.version}-{os.getpid()}"
            shutil.rmtree(staging, ignore_errors=True)
            archive = staging / ("download." + download.archive)
            try:
                _verify_and_download(download, archive, opener)
                _extract(archive, download.archive, folder)
            except Exception:
                shutil.rmtree(folder, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            (folder / "provenance.json").write_text(
                json.dumps(
                    {**download.to_dict(), "checksum": download.checksum, "at": time.time()}, indent=2
                ),
                encoding="utf-8",
            )
            (folder / ".complete").write_text("ok", encoding="utf-8")
    home = _home_in(download.kind, folder)
    if not home:
        raise ToolchainError(
            f"Downloaded {download.kind} {download.version} but found no executable inside it."
        )
    return Toolchain(download.kind, download.version, home, "cache")


@dataclass
class Provisioned:
    toolchains: list[Toolchain] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    def env(self, root: Path | None = None) -> dict[str, str]:
        """One merged environment: every toolchain's variables, PATH entries in requirement order."""
        merged: dict[str, str] = {}
        path_parts: list[str] = []
        for toolchain in self.toolchains:
            values = toolchain.env(root)
            for key, value in values.items():
                if key == "PATH":
                    path_parts = [*(str(d) for d in toolchain.bin_dirs()), *path_parts]
                else:
                    merged[key] = value
        if path_parts:
            merged["PATH"] = os.pathsep.join([*path_parts, os.environ.get("PATH", "")])
        return merged

    def executable(self, name: str) -> str | None:
        for toolchain in self.toolchains:
            if path := toolchain.executable(name):
                return str(path)
        return None

    def tools(self) -> frozenset[str]:
        """Names from ``stack.REQUIRED_TOOLS`` these toolchains cover, so a missing system tool is not an error."""
        covered = {"jdk": ("java",), "maven": ("mvn",), "dotnet": ("dotnet",), "python": ("python3",)}
        return frozenset(name for t in self.toolchains for name in covered.get(t.kind, ()))

    def to_dicts(self) -> list[dict[str, str]]:
        return [t.to_dict() for t in self.toolchains]

    @classmethod
    def from_dicts(cls, rows: list[dict[str, str]] | None) -> Provisioned:
        return cls(
            [
                Toolchain(r["kind"], r["version"], Path(r["home"]), r.get("source", "cache"))
                for r in rows or []
            ]
        )


def python_via_uv(want: str, runner: Callable[[list[str]], str] = _run) -> Toolchain | None:
    """Ask ``uv`` for a matching interpreter (installing it into uv's own store); None when uv is missing."""
    uv = shutil.which("uv")
    if not uv:
        return None
    runner([uv, "python", "install", want])
    out = runner([uv, "python", "find", want]).strip().splitlines()
    path = next((line.strip() for line in out if Path(line.strip()).is_file()), None)
    return Toolchain("python", want, Path(path), "cache") if path else None


def ensure(
    requirements: list,
    *,
    allow_download: bool,
    root: Path | None = None,
    fetch: Fetch = http_get,
    opener: Fetch | None = None,
) -> Provisioned:
    """Make every requirement available: reuse what exists, download the rest only when ``allow_download``."""
    result = Provisioned()
    for req in requirements:
        have = find(req.kind, req.version, root)
        if have:
            result.toolchains.append(have)
            result.events.append(
                {"kind": req.kind, "version": have.version, "action": f"using {have.source}"}
            )
            continue
        if not allow_download:
            raise ToolchainError(
                f"{req.kind} {req.version} is not installed and downloading was not approved."
            )
        if req.kind == "python":
            toolchain = python_via_uv(req.version)
            if not toolchain:
                raise ToolchainError(
                    f"Python {req.version} is not installed. Install it (or the 'uv' tool) and retry."
                )
        else:
            toolchain = provision(resolve_download(req.kind, req.version, fetch), root, opener)
        result.toolchains.append(toolchain)
        result.events.append(
            {
                "kind": req.kind,
                "version": toolchain.version,
                "action": "provisioned",
                "home": str(toolchain.home),
            }
        )
    return result


def resolve_argv(argv: list[str], provisioned: Provisioned) -> list[str]:
    """Point argv[0] at a provisioned tool when the command names one (mvn, gradle, java, dotnet, python for venv)."""
    if not argv:
        return argv
    name = Path(argv[0]).name.lower().split(".")[0]
    if name in {"mvn", "gradle", "java", "javac", "dotnet"} and (path := provisioned.executable(name)):
        return [path, *argv[1:]]
    if argv[1:3] == ["-m", "venv"]:  # create-venv: build the venv with the interpreter the project asks for
        python = next(
            (t.home for t in provisioned.toolchains if t.kind == "python" and t.home.is_file()), None
        )
        if python:
            return [str(python), *argv[1:]]
    return argv


def apply_toolchains(steps: list, provisioned: Provisioned, root: Path | None = None) -> None:
    """Rewrite steps in place so they run with the provisioned toolchains (absolute executables, JAVA_HOME, PATH...).

    Called on the plan *before* it is shown for approval or hashed, so the human sees exactly what will run.
    """
    if not provisioned.toolchains:
        return
    env = provisioned.env(root)
    for step in steps:
        step.argv = resolve_argv(step.argv, provisioned)
        step.env = {**env, **step.env}

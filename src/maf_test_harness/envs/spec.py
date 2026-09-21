"""What toolchains a repository needs, read from its own files (no network, no model).

The result is a list of :class:`ToolRequirement` (kind + minimum version + the file that said so). Whether the machine
already satisfies them, and how to provision what is missing, is decided in ``toolchains.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_JDK = "17"
DEFAULT_MAVEN = "3.9.9"
DEFAULT_GRADLE = "8.10.2"


@dataclass(frozen=True)
class ToolRequirement:
    kind: str  # python | jdk | maven | gradle | dotnet
    version: str  # minimum: "3.11", "17", "3.9.9", "8.0"
    reason: str = ""  # the repository file (or default) that decided it


@dataclass
class EnvSpec:
    ecosystem: str
    requirements: list[ToolRequirement] = field(default_factory=list)
    build_tool: str | None = None  # maven | gradle | dotnet | pip
    wrapper: bool = False  # mvnw / gradlew present: the repository brings its own build tool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ecosystem": self.ecosystem,
            "build_tool": self.build_tool,
            "wrapper": self.wrapper,
            "requirements": [r.__dict__ for r in self.requirements],
            "notes": self.notes,
        }


def version_tuple(text: str) -> tuple[int, ...]:
    """'17.0.9' -> (17, 0, 9); '1.8.0_392' -> (8, 0, 392) (legacy Java numbering); junk -> ()."""
    text = text.strip().lstrip("v")
    if text.startswith("1.") and re.match(r"1\.\d", text):
        text = text[2:]
    return tuple(int(n) for n in re.findall(r"\d+", text)[:4])


def at_least(have: str, want: str) -> bool:
    return version_tuple(have) >= version_tuple(want)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ------------------------------------------------------------------------------------------------ python
def python_requirement(ws: Path) -> ToolRequirement | None:
    for name in (".python-version", "runtime.txt"):
        text = _read(ws / name).strip()
        match = re.search(r"(\d+\.\d+)(?:\.\d+)?", text)
        if match:
            return ToolRequirement("python", match.group(1), name)
    for name, pattern in (
        ("pyproject.toml", r"requires-python\s*=\s*[\"']([^\"']+)[\"']"),
        ("setup.cfg", r"python_requires\s*=\s*(\S+)"),
        ("setup.py", r"python_requires\s*=\s*[\"']([^\"']+)[\"']"),
    ):
        match = re.search(pattern, _read(ws / name))
        if match:
            lower = minimum_from_specifier(match.group(1))
            if lower:
                return ToolRequirement("python", lower, f"{name} ({match.group(1)})")
    return None


def minimum_from_specifier(specifier: str) -> str | None:
    """The lowest version a specifier like '>=3.10,<3.13' allows; None when it only excludes."""
    for clause in specifier.split(","):
        match = re.match(r"\s*(>=|==|~=)\s*(\d+\.\d+)", clause)
        if match:
            return match.group(2)
        match = re.match(r"\s*>\s*(\d+)\.(\d+)", clause)
        if match:
            return f"{match.group(1)}.{int(match.group(2)) + 1}"
    return None


def specifier_allows(specifier: str, version: str) -> bool:
    """Whether ``version`` (e.g. '3.12') satisfies every clause of a specifier such as '>=3.10,<3.13'."""
    have = version_tuple(version)
    for clause in specifier.split(","):
        match = re.match(r"\s*(>=|<=|==|!=|~=|>|<)\s*(\d+(?:\.\d+)*)(\.\*)?", clause)
        if not match:
            continue
        op, want = match.group(1), version_tuple(match.group(2))
        cut = have[: len(want)]
        ok = {
            ">=": have >= want,
            "<=": cut <= want,
            ">": have > want,
            "<": have < want,
            "==": cut == want,
            "!=": cut != want,
            "~=": have >= want and have[: len(want) - 1] == want[:-1],
        }[op]
        if not ok:
            return False
    return True


# -------------------------------------------------------------------------------------------------- java
def _java_int(text: str) -> str | None:
    version = version_tuple(text)
    return str(version[0]) if version and version[0] >= 6 else None


def java_release(ws: Path) -> tuple[str, str] | None:
    """(major release, file) the build compiles for, from Maven/Gradle/version-manager files."""
    pom = _read(ws / "pom.xml")
    for tag in (
        "maven.compiler.release",
        "release",
        "maven.compiler.source",
        "maven.compiler.target",
        "java.version",
    ):
        match = re.search(rf"<{re.escape(tag)}>\s*([\d.]+)\s*</{re.escape(tag)}>", pom)
        if match and (major := _java_int(match.group(1))):
            return major, f"pom.xml <{tag}>"
    for name in ("build.gradle", "build.gradle.kts"):
        text = _read(ws / name)
        for pattern in (
            r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)",
            r"(?:source|target)Compatibility\s*=?\s*(?:JavaVersion\.VERSION_)?[\"']?([\d._]+)",
            r"jvmTarget\s*=?\s*[\"']?([\d.]+)",
            r"jvmToolchain\(\s*(\d+)\s*\)",
        ):
            match = re.search(pattern, text)
            if match and (major := _java_int(match.group(1).replace("_", "."))):
                return major, name
    for name, pattern in (
        (".java-version", r"(\d+(?:\.\d+)*)"),
        (".sdkmanrc", r"java\s*=\s*(\d+(?:\.\d+)*)"),
        (".tool-versions", r"java\s+(?:[a-z]+-)?(\d+(?:\.\d+)*)"),
    ):
        match = re.search(pattern, _read(ws / name))
        if match and (major := _java_int(match.group(1))):
            return major, name
    return None


def gradle_wrapper_version(ws: Path) -> str | None:
    match = re.search(
        r"gradle-([\d.]+)-(?:bin|all)\.zip", _read(ws / "gradle" / "wrapper" / "gradle-wrapper.properties")
    )
    return match.group(1) if match else None


def java_spec(ws: Path, ecosystem: str) -> EnvSpec:
    gradle = ecosystem == "gradle" or (
        not (ws / "pom.xml").is_file()
        and any((ws / n).is_file() for n in ("build.gradle", "build.gradle.kts"))
    )
    spec = EnvSpec("gradle" if gradle else "maven", build_tool="gradle" if gradle else "maven")
    release = java_release(ws)
    if release:
        spec.requirements.append(ToolRequirement("jdk", release[0], release[1]))
    else:
        spec.requirements.append(
            ToolRequirement("jdk", DEFAULT_JDK, "default (no release found in the build files)")
        )
    wrapper = (ws / ("gradlew" if gradle else "mvnw")).is_file()
    spec.wrapper = wrapper
    if not wrapper:
        if gradle:
            spec.requirements.append(
                ToolRequirement("gradle", gradle_wrapper_version(ws) or DEFAULT_GRADLE, "no gradlew wrapper")
            )
        else:
            spec.requirements.append(ToolRequirement("maven", DEFAULT_MAVEN, "no mvnw wrapper"))
    return spec


# ------------------------------------------------------------------------------------------------- dotnet
# Only the dotted forms (net6.0, net8.0, netcoreapp3.1): net48/net472 are .NET Framework, not an SDK to provision.
_TFM = re.compile(r"\bnet(?:coreapp)?(\d+\.\d+)(?![\w.])", re.I)


def dotnet_requirement(ws: Path) -> ToolRequirement | None:
    global_json = _read(ws / "global.json")
    if global_json:
        try:
            version = (json.loads(global_json).get("sdk") or {}).get("version")
        except json.JSONDecodeError:
            version = None
        if isinstance(version, str) and (parts := version_tuple(version)):
            return ToolRequirement("dotnet", f"{parts[0]}.{parts[1] if len(parts) > 1 else 0}", "global.json")
    best: tuple[int, ...] = ()
    source = ""
    for project in sorted(ws.rglob("*.csproj"))[:50]:
        if any(
            part in {"bin", "obj", "node_modules", ".harness-venv"} for part in project.relative_to(ws).parts
        ):
            continue
        text = _read(project)
        for tfms in re.findall(r"<TargetFrameworks?>([^<]+)</TargetFrameworks?>", text):
            for match in _TFM.finditer(tfms):
                found = version_tuple(match.group(1))
                if found and found > best:
                    best, source = found, project.name
    if best:
        return ToolRequirement(
            "dotnet", f"{best[0]}.{best[1] if len(best) > 1 else 0}", f"{source} TargetFramework"
        )
    return None


def dotnet_spec(ws: Path) -> EnvSpec:
    spec = EnvSpec("dotnet", build_tool="dotnet")
    if req := dotnet_requirement(ws):
        spec.requirements.append(req)
    else:
        spec.requirements.append(ToolRequirement("dotnet", "8.0", "default (no TargetFramework found)"))
    return spec


# --------------------------------------------------------------------------------------------------- entry
def detect_spec(ws: Path, ecosystem: str) -> EnvSpec:
    """The toolchains ``ecosystem`` needs for the repository in ``ws``."""
    ws = Path(ws)
    if ecosystem in {"maven", "gradle", "java"}:
        return java_spec(ws, ecosystem)
    if ecosystem == "dotnet":
        return dotnet_spec(ws)
    if ecosystem == "python":
        spec = EnvSpec("python", build_tool="pip")
        if req := python_requirement(ws):
            spec.requirements.append(req)
        return spec
    return EnvSpec(ecosystem)

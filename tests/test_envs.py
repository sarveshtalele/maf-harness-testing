"""envs: reading toolchain requirements from a repository, and provisioning toolchains into a private cache."""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from maf_test_harness import envs
from maf_test_harness.envs import python_env, spec, toolchains
from maf_test_harness.envs.runners import Step


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


# -------------------------------------------------------------------------------------------------- specs
def test_version_helpers():
    assert spec.version_tuple("1.8.0_392") == (8, 0, 392) and spec.version_tuple("17.0.9") == (17, 0, 9)
    assert spec.version_tuple("v3.11") == (3, 11) and spec.version_tuple("junk") == ()
    assert spec.specifier_allows(">=3.10,<3.13", "3.12") and not spec.specifier_allows(">=3.10,<3.13", "3.13")
    assert spec.specifier_allows("~=3.10", "3.12") and not spec.specifier_allows("==3.11.*", "3.12")
    assert spec.minimum_from_specifier(">=3.10,<3.13") == "3.10" and spec.minimum_from_specifier("<4") is None


@pytest.mark.parametrize(
    ("files", "want", "reason"),
    [
        ({".python-version": "3.11.4\n"}, "3.11", ".python-version"),
        ({"runtime.txt": "python-3.9.18"}, "3.9", "runtime.txt"),
        ({"pyproject.toml": '[project]\nrequires-python = ">=3.10,<3.13"\n'}, "3.10", "pyproject.toml"),
        ({"setup.py": "setup(python_requires='>=3.8')"}, "3.8", "setup.py"),
    ],
)
def test_python_requirement_sources(tmp_path, files, want, reason):
    req = spec.python_requirement(write(tmp_path, files))
    assert req.version == want and req.reason.startswith(reason)
    assert spec.python_requirement(tmp_path / "nothing") is None


def test_python_env_knows_whether_the_running_interpreter_is_enough(tmp_path):
    assert python_env.current_interpreter_satisfies(python_env.spec(tmp_path))  # no requirement
    old = write(tmp_path / "old", {".python-version": "3.0"})
    new = write(tmp_path / "new", {".python-version": "99.0"})
    assert python_env.current_interpreter_satisfies(python_env.spec(old))
    assert not python_env.current_interpreter_satisfies(python_env.spec(new))
    banned = write(tmp_path / "banned", {"pyproject.toml": '[project]\nrequires-python = ">=3.0,<3.1"\n'})
    assert not python_env.current_interpreter_satisfies(python_env.spec(banned))


@pytest.mark.parametrize(
    ("files", "release", "source"),
    [
        (
            {"pom.xml": "<properties><maven.compiler.release>21</maven.compiler.release></properties>"},
            "21",
            "pom.xml",
        ),
        ({"pom.xml": "<properties><java.version>1.8</java.version></properties>"}, "8", "pom.xml"),
        ({"pom.xml": "<maven.compiler.source>11</maven.compiler.source>"}, "11", "pom.xml"),
        (
            {"build.gradle": "java { toolchain { languageVersion = JavaLanguageVersion.of(17) } }"},
            "17",
            "build.gradle",
        ),
        ({"build.gradle": "sourceCompatibility = JavaVersion.VERSION_1_8"}, "8", "build.gradle"),
        ({"build.gradle.kts": "kotlin { jvmToolchain(21) }"}, "21", "build.gradle.kts"),
        ({".sdkmanrc": "java=17.0.9-tem\n"}, "17", ".sdkmanrc"),
        ({".tool-versions": "java temurin-21.0.1\n"}, "21", ".tool-versions"),
    ],
)
def test_java_release_from_build_files(tmp_path, files, release, source):
    found = spec.java_release(write(tmp_path, files))
    assert found and found[0] == release and found[1].startswith(source)


def test_java_spec_maven_and_gradle_with_and_without_wrappers(tmp_path):
    maven = write(tmp_path / "m", {"pom.xml": "<java.version>17</java.version>"})
    result = spec.detect_spec(maven, "maven")
    assert [(r.kind, r.version) for r in result.requirements] == [("jdk", "17"), ("maven", "3.9.9")]
    write(maven, {"mvnw": "#!/bin/sh"})
    assert [r.kind for r in spec.detect_spec(maven, "maven").requirements] == ["jdk"]
    gradle = write(
        tmp_path / "g",
        {
            "build.gradle": "",
            "gradle/wrapper/gradle-wrapper.properties": "distributionUrl=https://x/gradle-8.5-bin.zip",
        },
    )
    result = spec.detect_spec(gradle, "gradle")
    assert result.build_tool == "gradle" and ("gradle", "8.5") in [
        (r.kind, r.version) for r in result.requirements
    ]
    assert (
        spec.detect_spec(write(tmp_path / "none", {"pom.xml": ""}), "maven").requirements[0].version == "17"
    )


def test_dotnet_requirement_from_global_json_and_target_frameworks(tmp_path):
    pinned = write(tmp_path / "a", {"global.json": '{"sdk": {"version": "8.0.100"}}'})
    assert spec.dotnet_requirement(pinned).version == "8.0"
    projects = write(
        tmp_path / "b",
        {
            "src/A.csproj": "<TargetFramework>net6.0</TargetFramework>",
            "src/B.csproj": "<TargetFrameworks>net6.0;net8.0</TargetFrameworks>",
            "obj/C.csproj": "<TargetFramework>net9.0</TargetFramework>",
            "old/D.csproj": "<TargetFramework>netcoreapp3.1</TargetFramework>",
            "legacy/E.csproj": "<TargetFramework>net48</TargetFramework>",
        },
    )
    assert spec.dotnet_requirement(projects).version == "8.0"
    assert spec.detect_spec(tmp_path / "empty", "dotnet").requirements[0].reason.startswith("default")
    assert spec.detect_spec(tmp_path, "node").requirements == []


# ------------------------------------------------------------------------------------------ detection
def test_parsing_and_satisfaction_rules():
    assert toolchains.parse_java_version('openjdk version "17.0.9" 2023-10-17') == "17.0.9"
    assert toolchains.parse_java_version('java version "1.8.0_392"') == "8.0.392"
    assert toolchains.parse_java_version("nothing") is None
    assert toolchains.satisfies("jdk", "21.0.1", "17") and not toolchains.satisfies("jdk", "11.0.2", "17")
    assert toolchains.satisfies("maven", "3.9.6", "3.9") and not toolchains.satisfies("maven", "3.8.8", "3.9")
    assert toolchains.satisfies("dotnet", "8.0.404", "8.0") and not toolchains.satisfies(
        "dotnet", "6.0.400", "8.0"
    )
    assert toolchains.satisfies("python", "3.12.1", "3.11") and not toolchains.satisfies(
        "python", "3.9.1", "3.11"
    )
    assert not toolchains.satisfies("jdk", "junk", "17")


def test_a_jre_without_javac_is_not_a_usable_jdk(tmp_path, monkeypatch):
    home = tmp_path / "jre"
    (home / "bin").mkdir(parents=True)
    java = home / "bin" / ("java.exe" if os.name == "nt" else "java")
    java.write_text("")
    monkeypatch.setenv("JAVA_HOME", str(home))
    monkeypatch.setattr(toolchains.shutil, "which", lambda name: None)
    monkeypatch.setattr(toolchains, "_run", lambda argv, timeout=20: 'openjdk version "17.0.1"')
    assert toolchains.detect_jdks() == []
    (home / "bin" / "javac").write_text("")
    assert [t.version for t in toolchains.detect_jdks()] == ["17.0.1"]


def test_toolchain_environment_variables(tmp_path):
    jdk = toolchains.Toolchain("jdk", "17", tmp_path / "jdk", "cache")
    assert jdk.env()["JAVA_HOME"] == str(tmp_path / "jdk") and jdk.env()["PATH"].startswith(
        str(tmp_path / "jdk" / "bin")
    )
    dotnet = toolchains.Toolchain("dotnet", "8.0.100", tmp_path / "dn", "cache").env(tmp_path / "cache")
    assert dotnet["DOTNET_ROOT"] == str(tmp_path / "dn") and dotnet["NUGET_PACKAGES"].startswith(
        str(tmp_path / "cache")
    )
    assert dotnet["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"
    assert toolchains.Toolchain("python", "3.11", tmp_path / "python.exe", "system").env() == {}


# ----------------------------------------------------------------------------------- offline downloads
def make_zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, text in entries.items():
            bundle.writestr(name, text)
    return buffer.getvalue()


class Mirror:
    """A local HTTP server standing in for Adoptium, Apache, Gradle and Microsoft (via HARNESS_TOOLCHAIN_MIRROR)."""

    def __init__(self, routes: dict[str, bytes]):
        self.routes, self.hits = routes, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?")[0]
                outer.hits.append(path)
                body = outer.routes.get(path)
                self.send_response(200 if body is not None else 404)
                self.end_headers()
                self.wfile.write(body or b"")

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_TOOLCHAIN_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(toolchains, "DETECTORS", dict.fromkeys(toolchains.DETECTORS, list))  # bare machine
    created = []

    def make(routes):
        server = Mirror(routes)
        created.append(server)
        monkeypatch.setenv("HARNESS_TOOLCHAIN_MIRROR", server.url)
        return server

    yield make
    for server in created:
        server.close()


def jdk_routes(archive: bytes, checksum: str | None = None):
    package = {
        "link": "https://api.adoptium.net/download/jdk17.zip",
        "checksum": checksum or hashlib.sha256(archive).hexdigest(),
        "name": "OpenJDK17.zip",
        "size": len(archive),
    }
    meta = json.dumps([{"binary": {"package": package}, "version": {"semver": "17.0.9+9"}}]).encode()
    return {"/v3/assets/latest/17/hotspot": meta, "/download/jdk17.zip": archive}


JDK_ZIP = make_zip({"jdk-17.0.9+9/bin/javac": "x", "jdk-17.0.9+9/bin/java": "x", "jdk-17.0.9+9/release": "y"})


def test_missing_jdk_is_downloaded_verified_extracted_and_then_reused_offline(tmp_path, mirror):
    server = mirror(jdk_routes(JDK_ZIP))
    req = spec.ToolRequirement("jdk", "17")
    result = toolchains.ensure([req], allow_download=True)
    jdk = result.toolchains[0]
    assert (jdk.kind, jdk.version, jdk.source) == ("jdk", "17.0.9", "cache")
    assert jdk.executable("javac") and (jdk.home.parent / "provenance.json").is_file()
    assert result.events[0]["action"] == "provisioned"
    hits = len(server.hits)
    again = toolchains.ensure([req], allow_download=False)  # cached: no approval and no network needed
    assert again.toolchains[0].home == jdk.home and len(server.hits) == hits
    assert again.env()["JAVA_HOME"] == str(jdk.home)


def test_a_download_needs_approval(tmp_path, mirror):
    mirror(jdk_routes(JDK_ZIP))
    with pytest.raises(toolchains.ToolchainError, match="downloading was not approved"):
        toolchains.ensure([spec.ToolRequirement("jdk", "17")], allow_download=False)
    assert not (tmp_path / "cache" / "jdk").exists()


def test_a_corrupted_download_is_discarded(tmp_path, mirror):
    mirror(jdk_routes(JDK_ZIP, checksum="0" * 64))
    with pytest.raises(toolchains.ToolchainError, match="Checksum mismatch"):
        toolchains.ensure([spec.ToolRequirement("jdk", "17")], allow_download=True)
    assert not any((tmp_path / "cache" / "jdk").glob("*")) if (tmp_path / "cache" / "jdk").exists() else True
    assert (
        not list((tmp_path / "cache" / ".staging").glob("*"))
        if (tmp_path / "cache" / ".staging").exists()
        else True
    )


def test_archives_that_escape_the_target_are_refused(tmp_path, mirror):
    evil = make_zip({"../evil.txt": "x", "jdk/bin/javac": "x"})
    mirror(jdk_routes(evil))
    with pytest.raises(toolchains.ToolchainError, match="escapes"):
        toolchains.ensure([spec.ToolRequirement("jdk", "17")], allow_download=True)
    assert not (tmp_path / "cache" / "jdk" / "evil.txt").exists()


def test_maven_gradle_and_dotnet_catalogues(tmp_path, mirror):
    maven_zip = make_zip({"apache-maven-3.9.9/bin/mvn": "x", "apache-maven-3.9.9/bin/mvn.cmd": "x"})
    gradle_zip = make_zip({"gradle-8.5/bin/gradle": "x", "gradle-8.5/bin/gradle.bat": "x"})
    dotnet_zip = make_zip({"dotnet": "x", "dotnet.exe": "x", "sdk/8.0.100/x.dll": "x"})
    system, arch = toolchains.platform_key()
    rid = {"windows": "win", "mac": "osx", "linux": "linux"}[
        system
    ] + f"-{'arm64' if arch == 'aarch64' else 'x64'}"
    releases = {
        "releases": [
            {
                "sdk": {
                    "version": "8.0.100",
                    "files": [
                        {
                            "name": "dotnet-sdk-x.pkg",
                            "rid": rid,
                            "url": "https://builds.dotnet.microsoft.com/a.pkg",
                            "hash": "0",
                        },
                        {
                            "name": "dotnet-sdk-x.zip",
                            "rid": rid,
                            "url": "https://builds.dotnet.microsoft.com/dotnet-sdk.zip",
                            "hash": hashlib.sha512(dotnet_zip).hexdigest(),
                        },
                    ],
                }
            }
        ]
    }
    mirror(
        {
            "/dist/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.zip": maven_zip,
            "/dist/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.zip.sha512": (
                hashlib.sha512(maven_zip).hexdigest() + "  apache-maven-3.9.9-bin.zip\n"
            ).encode(),
            "/distributions/gradle-8.5-bin.zip": gradle_zip,
            "/distributions/gradle-8.5-bin.zip.sha256": hashlib.sha256(gradle_zip).hexdigest().encode(),
            "/dotnet/release-metadata/8.0/releases.json": json.dumps(releases).encode(),
            "/dotnet-sdk.zip": dotnet_zip,
        }
    )
    result = toolchains.ensure(
        [
            spec.ToolRequirement("maven", "3.9"),
            spec.ToolRequirement("gradle", "8.5"),
            spec.ToolRequirement("dotnet", "8.0"),
        ],
        allow_download=True,
    )
    assert [t.kind for t in result.toolchains] == ["maven", "gradle", "dotnet"]
    assert result.executable("mvn") and result.executable("gradle") and result.executable("dotnet")
    merged = result.env()
    assert "MAVEN_HOME" in merged and "GRADLE_HOME" in merged and "DOTNET_ROOT" in merged
    assert result.tools() == frozenset({"mvn", "dotnet"})


def test_python_requirement_uses_uv_only_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchains, "DETECTORS", dict.fromkeys(toolchains.DETECTORS, list))
    monkeypatch.setattr(toolchains.shutil, "which", lambda name: None)
    with pytest.raises(toolchains.ToolchainError, match="uv"):
        toolchains.ensure([spec.ToolRequirement("python", "3.9")], allow_download=True)
    interpreter = tmp_path / "python.exe"
    interpreter.write_text("")
    calls = []
    monkeypatch.setattr(toolchains.shutil, "which", lambda name: "uv" if name == "uv" else None)

    def runner(argv):
        calls.append(argv)
        return str(interpreter) if "find" in argv else ""

    found = toolchains.python_via_uv("3.9", runner)
    assert found.home == interpreter and calls[0][1:3] == ["python", "install"]


def test_apply_toolchains_rewrites_the_plan_before_it_is_approved(tmp_path):
    home = tmp_path / "jdk"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "java").write_text("")
    mvn_home = tmp_path / "mvn"
    (mvn_home / "bin").mkdir(parents=True)
    (mvn_home / "bin" / "mvn").write_text("")
    provisioned = toolchains.Provisioned(
        [
            toolchains.Toolchain("jdk", "17", home, "cache"),
            toolchains.Toolchain("maven", "3.9.9", mvn_home, "cache"),
        ]
    )
    steps = [Step("a", ["mvn", "-B", "test"], "test", env={"X": "1"}), Step("b", ["echo", "hi"], "setup")]
    toolchains.apply_toolchains(steps, provisioned)
    assert steps[0].argv[0] == str(mvn_home / "bin" / "mvn") and steps[0].argv[1:] == ["-B", "test"]
    assert steps[0].env["JAVA_HOME"] == str(home) and steps[0].env["X"] == "1"
    assert steps[1].argv == ["echo", "hi"]
    venv = [Step("v", ["python", "-m", "venv", ".v"], "setup")]
    interpreter = tmp_path / "py311.exe"
    interpreter.write_text("")
    toolchains.apply_toolchains(
        venv, toolchains.Provisioned([toolchains.Toolchain("python", "3.11", interpreter, "system")])
    )
    assert venv[0].argv[0] == str(interpreter)
    round_trip = toolchains.Provisioned.from_dicts(provisioned.to_dicts())
    assert round_trip.executable("java") == provisioned.executable("java")
    untouched = [Step("c", ["mvn"], "test")]
    toolchains.apply_toolchains(untouched, toolchains.Provisioned())
    assert untouched[0].argv == ["mvn"] and untouched[0].env == {}


def test_mirror_rewrites_hosts_but_keeps_paths(monkeypatch):
    assert (
        toolchains.apply_mirror("https://api.adoptium.net/v3/x?a=1", "http://m:8/base")
        == "http://m:8/base/v3/x?a=1"
    )
    monkeypatch.delenv("HARNESS_TOOLCHAIN_MIRROR", raising=False)
    assert toolchains.apply_mirror("https://a/b") == "https://a/b"


# ------------------------------------------------------------------------------------------- planning
def test_plan_shows_found_versus_download_without_touching_the_network(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_TOOLCHAIN_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(toolchains, "DETECTORS", dict.fromkeys(toolchains.DETECTORS, list))
    repo = write(tmp_path / "r", {"pom.xml": "<java.version>17</java.version>"})
    rows = envs.plan_toolchains(repo, "maven")
    assert [(r["kind"], r["status"]) for r in rows] == [("jdk", "download"), ("maven", "download")]
    assert "adoptium" in rows[0]["source"].lower() and rows[0]["reason"].startswith("pom.xml")
    fake = toolchains.Toolchain("jdk", "21.0.1", tmp_path / "j", "system")
    monkeypatch.setattr(toolchains, "DETECTORS", {**toolchains.DETECTORS, "jdk": lambda: [fake]})
    assert envs.plan_toolchains(repo, "maven")[0]["status"] == "found"
    assert envs.plan_toolchains(tmp_path, "node") == []


def test_windows_prefers_real_launchers_over_extensionless_scripts(tmp_path, monkeypatch):
    home = tmp_path / "maven"
    (home / "bin").mkdir(parents=True)
    for name in ("mvn", "mvn.cmd"):
        (home / "bin" / name).write_text("")
    maven = toolchains.Toolchain("maven", "3.9.9", home, "cache")
    monkeypatch.setattr(toolchains.platform_compat, "IS_WINDOWS", True)
    assert maven.executable("mvn").name == "mvn.cmd"
    monkeypatch.setattr(toolchains.platform_compat, "IS_WINDOWS", False)
    assert maven.executable("mvn").name == "mvn"


def test_environment_plan_lists_downloads_instead_of_blocking_on_missing_tools(tmp_path, monkeypatch):
    from maf_test_harness.envs import environment
    from maf_test_harness.repo.stack import StackProfile

    monkeypatch.setenv("HARNESS_TOOLCHAIN_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(toolchains, "DETECTORS", dict.fromkeys(toolchains.DETECTORS, list))
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)  # no java, no mvn on this machine
    repo = write(tmp_path / "repo", {"pom.xml": "<java.version>17</java.version>"})
    plan = environment.build_environment_plan(StackProfile(primary="maven"), repo, tmp_path / "c")
    assert plan["needs_download"] is True and plan["missing_prerequisites"] == []
    assert {p["tool"]: p["provisionable"] for p in plan["prerequisites"]} == {"mvn": True, "java": True}
    assert [r["kind"] for r in plan["toolchains"]] == ["jdk", "maven"]
    assert any("Approving downloads" in note and "jdk 17" in note for note in plan["notes"])
    write(repo, {"mvnw": "#!/bin/sh"})  # the wrapper brings Maven: only the JDK is fetched
    plan = environment.build_environment_plan(StackProfile(primary="maven"), repo, tmp_path / "c")
    assert [r["kind"] for r in plan["toolchains"]] == ["jdk"]

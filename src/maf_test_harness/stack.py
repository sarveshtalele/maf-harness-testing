"""Deterministic technology-stack profile used to pick a runner and to steer generation.

Everything here is derived from repository files on the host; no model is involved. The profile answers
"which ecosystems are present, how are their tests conventionally run, and are the needed tools installed?".
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import platform_compat
from .repo_analyzer import _safe_read, _tracked_files

# ecosystem -> executables that must exist on the host for the runner to work
REQUIRED_TOOLS: dict[str, tuple[str, ...]] = {
    "python": ("python3",),
    "node": ("node", "npm"),
    "maven": ("mvn", "java"),
    "gradle": ("java",),
    "go": ("go",),
    "dotnet": ("dotnet",),
}


@dataclass
class StackProfile:
    ecosystems: list[str] = field(default_factory=list)  # ordered by evidence weight
    primary: str = "unknown"
    build_files: list[str] = field(default_factory=list)
    # pytest | jest | vitest | playwright | maven-surefire | gradle | go-test | dotnet-test
    test_runner: str = ""
    web_ui: bool = False
    http_service: bool = False
    openapi_files: list[str] = field(default_factory=list)
    ci_files: list[str] = field(default_factory=list)
    container_files: list[str] = field(default_factory=list)
    missing_tools: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_OPENAPI = re.compile(r"(^|/)(openapi|swagger)[^/]*\.(ya?ml|json)$", re.I)
_WEB_UI_HINT = re.compile(r"\.(xhtml|html|jsx|tsx|vue|svelte|jsp|erb|ejs|hbs)$", re.I)
_SERVICE_HINT = re.compile(
    r"(@RestController|@Path\(|@GetMapping|@RequestMapping|flask|fastapi|express\(|http\.HandleFunc|"
    r"\[HttpGet|\[ApiController|@app\.route|APIRouter|createServer\()",
    re.I,
)


def _node_runner(root: Path, package_files: list[str]) -> str:
    for rel in package_files:
        try:
            data = json.loads(_safe_read(root / rel) or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        if not isinstance(deps, dict):
            continue
        for key, runner in (("@playwright/test", "playwright"), ("vitest", "vitest"), ("jest", "jest")):
            if key in deps:
                return runner
    return "jest"


def detect_stack(root: Path, languages: dict[str, int] | None = None) -> StackProfile:
    root = Path(root).resolve()
    rel = [p.relative_to(root).as_posix() for p in _tracked_files(root)]
    names = {Path(r).name.lower() for r in rel}
    profile = StackProfile()
    weights: dict[str, int] = {}

    def add(eco: str, weight: int, build_file: str | None = None) -> None:
        weights[eco] = weights.get(eco, 0) + weight
        if build_file and build_file not in profile.build_files:
            profile.build_files.append(build_file)

    for r in rel:
        n = Path(r).name.lower()
        depth_bonus = 3 if "/" not in r else 1
        if n in {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile", "tox.ini"}:
            add("python", 5 * depth_bonus, r)
        elif n == "package.json":
            add("node", 5 * depth_bonus, r)
        elif n == "pom.xml":
            add("maven", 6 * depth_bonus, r)
        elif n in {"build.gradle", "build.gradle.kts"}:
            add("gradle", 6 * depth_bonus, r)
        elif n == "go.mod":
            add("go", 6 * depth_bonus, r)
        elif n.endswith((".csproj", ".sln")):
            add("dotnet", 4 * depth_bonus, r)
        elif n == "cargo.toml":
            add("rust", 5 * depth_bonus, r)
        elif n == "gemfile":
            add("ruby", 5 * depth_bonus, r)
        elif n == "composer.json":
            add("php", 5 * depth_bonus, r)
        if _OPENAPI.search(r):
            profile.openapi_files.append(r)
        if r.startswith(".github/workflows/") or n in {
            ".gitlab-ci.yml",
            "jenkinsfile",
            ".travis.yml",
            "azure-pipelines.yml",
        }:
            profile.ci_files.append(r)
        if n in {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml"}:
            profile.container_files.append(r)
    # Source-language votes break ties for repos with no manifest (single-file scripts).
    suffix_eco = {
        ".py": "python",
        ".js": "node",
        ".ts": "node",
        ".java": "maven",
        ".go": "go",
        ".cs": "dotnet",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
    }
    for r in rel:
        eco = suffix_eco.get(Path(r).suffix.lower())
        if eco:
            weights[eco] = weights.get(eco, 0) + 1
    profile.ecosystems = [e for e, _ in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))]
    profile.primary = profile.ecosystems[0] if profile.ecosystems else "unknown"
    profile.web_ui = any(_WEB_UI_HINT.search(r) for r in rel)
    sample = "\n".join(
        _safe_read(root / r)[:4000]
        for r in rel
        if Path(r).suffix.lower() in {".py", ".js", ".ts", ".java", ".go", ".cs"}
    )[:400_000]
    profile.http_service = bool(_SERVICE_HINT.search(sample))
    if profile.primary == "node":
        profile.test_runner = _node_runner(
            root, [b for b in profile.build_files if b.endswith("package.json")]
        )
    else:
        profile.test_runner = {
            "python": "pytest",
            "maven": "maven-surefire",
            "gradle": "gradle",
            "go": "go-test",
            "dotnet": "dotnet-test",
        }.get(profile.primary, "")
    # If a Java repo has a pom AND python scripts etc., languages counter (analysis) is authoritative for notes.
    if languages and profile.primary == "unknown":
        profile.notes.append(f"No build manifest found; languages: {', '.join(languages)}")
    for tool in REQUIRED_TOOLS.get(profile.primary, ()):
        if not platform_compat.tool_present(tool):
            profile.missing_tools.append(tool)
    if "pom.xml" in names and "mvnw" in names and "mvn" in profile.missing_tools:
        profile.notes.append("Maven wrapper present; system mvn not required.")
    return profile

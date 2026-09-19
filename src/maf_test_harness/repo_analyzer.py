from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from .models import RepoAnalysis

IGNORED_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "vendor",
    "obj",
    "dist",
    "build",
    "target",
    ".next",
    "coverage",
    ".coverage",
}

LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".cs": "C#",
    ".go": "Go",
    ".rb": "Ruby",
    ".php": "PHP",
    ".rs": "Rust",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".cpp": "C++",
    ".c": "C",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

SOURCE_SUFFIXES = set(LANGUAGES)
PACKAGE_FILES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
    "composer.json",
    "Directory.Packages.props",
}

TEST_PATTERNS = [
    re.compile(r"(^|/)tests?(/|$)", re.I),
    re.compile(r"(^|/)__tests__(/|$)", re.I),
    re.compile(r"(^|/)(spec|specs)(/|$)", re.I),
    re.compile(r"(^|/)(e2e|cypress|playwright)(/|$)", re.I),
    re.compile(r"(^|/)test_[^/]+\.py$", re.I),
    re.compile(r"(^|/)[^/]+_test\.py$", re.I),
    re.compile(r"(^|/)[^/]+\.(test|spec)\.(js|jsx|ts|tsx)$", re.I),
    re.compile(r"(^|/)[^/]+Tests?\.cs$", re.I),
    re.compile(r"(^|/)[^/]+Test\.java$", re.I),
    re.compile(r"(^|/)[^/]+_test\.go$", re.I),
]

SENSITIVE_PATH = re.compile(
    r"(^|/)(\.env(?:\..*)?|id_rsa|id_ed25519|.*\.(?:pem|p12|pfx|key)|"
    r"credentials?(?:\..*)?|secrets?(?:\..*)?)(/|$)",
    re.I,
)
SECRET_ASSIGNMENT = re.compile(
    r"(?im)\b(api[_-]?key|password|passwd|secret|access[_-]?token|auth[_-]?token)"
    r"\s*[:=]\s*(['\"])[^'\"\n]{4,}\2"
)


class RepoInputError(ValueError):
    pass


class RepoCloneError(RuntimeError):
    pass


_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


def validate_github_url(raw_url: str, with_ref: bool = False):
    """Accept a GitHub page URL or clone URL and normalize it to an HTTPS clone URL.

    Accepted: https://github.com/owner/repo, .../repo.git, a trailing slash, and .../repo/tree/<branch-or-tag>
    (pins the ref). Rejected: other hosts, http/ssh, credentials, query strings, fragments, and non-repository
    pages such as /issues or /blob/. Returns (clone_url, owner, repo) or, with ``with_ref``, plus the pinned ref.
    """
    raw_url = raw_url.strip()
    parsed = urlparse(raw_url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
        raise RepoInputError("Use a public HTTPS GitHub URL such as https://github.com/owner/repo.")
    if parsed.netloc not in {"github.com", "www.github.com"} or parsed.query or parsed.fragment:
        raise RepoInputError(
            "Credentials, query strings, and fragments are not accepted in the repository URL."
        )
    parts = [part for part in parsed.path.split("/") if part]
    ref: str | None = None
    if len(parts) > 2:
        if parts[2] != "tree" or len(parts) < 4:
            raise RepoInputError(
                "Repository URL must be https://github.com/owner/repo (optionally /tree/<ref>)."
            )
        ref = "/".join(parts[3:])
        if not _REF.fullmatch(ref) or ".." in ref or ref.endswith(".lock") or "//" in ref:
            raise RepoInputError("Repository ref contains unsupported characters.")
        parts = parts[:2]
    if len(parts) != 2:
        raise RepoInputError("Repository URL must be exactly https://github.com/owner/repo.")
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    valid = re.compile(r"^[A-Za-z0-9_.-]+$")
    if (
        owner in {".", ".."}
        or repo in {".", ".."}
        or not owner
        or not repo
        or not valid.fullmatch(owner)
        or not valid.fullmatch(repo)
    ):
        raise RepoInputError("GitHub owner or repository name contains unsupported characters.")
    clone_url = f"https://github.com/{owner}/{repo}.git"
    return (clone_url, owner, repo, ref) if with_ref else (clone_url, owner, repo)


def clone_repository(repo_url: str, destination: Path, timeout_seconds: int = 180) -> tuple[str, str]:
    normalized, _, repo, ref = validate_github_url(repo_url, with_ref=True)
    destination = destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "git",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "core.hooksPath=/dev/null",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
        *(["--branch", ref] if ref else []),
        normalized,
        str(destination),
    ]
    # Never block on a credential prompt (private/nonexistent repos fail fast instead of hanging).
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds, check=False, env=env
        )
    except FileNotFoundError as exc:
        raise RepoCloneError("git is not installed or is not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RepoCloneError(f"Repository clone exceeded {timeout_seconds} seconds.") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git clone failed").strip().splitlines()[-1]
        raise RepoCloneError(detail)
    return normalized, repo


def _safe_source(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
        return (
            not path.is_symlink()
            and path.resolve().is_relative_to(root.resolve())
            and not any(part in IGNORED_DIRS for part in relative.parts)
            and not SENSITIVE_PATH.search(relative.as_posix())
        )
    except (OSError, ValueError):
        return False


def _tracked_files(root: Path) -> list[Path]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            paths = [root / p.decode("utf-8", errors="replace") for p in proc.stdout.split(b"\x00") if p]
            return [path for path in paths if _safe_source(root, path)]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and _safe_source(root, path):
            files.append(path)
    return files


def _safe_read(path: Path, max_bytes: int = 512_000) -> str:
    try:
        if path.stat().st_size > max_bytes:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _detect_frameworks(root: Path, relative_files: list[str]) -> tuple[list[str], list[str]]:
    frameworks: set[str] = set()
    test_frameworks: set[str] = set()

    package_files = [p for p in relative_files if Path(p).name.lower() == "package.json"]
    for package in package_files[:20]:
        try:
            data = json.loads(_safe_read(root / package))
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        deps = {}
        for group in ("dependencies", "devDependencies"):
            if isinstance(data.get(group), dict):
                deps.update(data[group])
        mapping = {
            "react": "React",
            "next": "Next.js",
            "vue": "Vue",
            "@angular/core": "Angular",
            "express": "Express",
            "@nestjs/core": "NestJS",
        }
        tests = {"jest": "Jest", "vitest": "Vitest", "@playwright/test": "Playwright", "cypress": "Cypress"}
        for key, value in mapping.items():
            if key in deps:
                frameworks.add(value)
        for key, value in tests.items():
            if key in deps:
                test_frameworks.add(value)

    python_text = "\n".join(
        _safe_read(root / p).lower()
        for p in relative_files
        if Path(p).name.lower() in {"pyproject.toml", "requirements.txt"}
    )
    for needle, label in [
        ("django", "Django"),
        ("fastapi", "FastAPI"),
        ("flask", "Flask"),
        ("streamlit", "Streamlit"),
        ("typer", "Typer"),
        ("click", "Click"),
    ]:
        if needle in python_text:
            frameworks.add(label)
    if "pytest" in python_text:
        test_frameworks.add("pytest")

    joined = "\n".join(
        _safe_read(root / rel).lower()
        for rel in relative_files
        if Path(rel).suffix.lower() in {".csproj", ".props"} and len(rel.split("/")) <= 4
    )
    if "microsoft.aspnetcore" in joined:
        frameworks.add("ASP.NET Core")
    for needle, label in [("xunit", "xUnit"), ("nunit", "NUnit"), ("mstest", "MSTest")]:
        if needle in joined:
            test_frameworks.add(label)

    java_build = "\n".join(
        _safe_read(root / p).lower()
        for p in relative_files
        if Path(p).name.lower() in {"pom.xml", "build.gradle", "build.gradle.kts"}
    )
    if "javaee" in java_build or "java-ee" in java_build:
        frameworks.add("Java EE")
    if any(p.endswith(".xhtml") for p in relative_files):
        frameworks.add("JSF")
    if "arquillian" in java_build:
        test_frameworks.add("Arquillian")
    if "org.springframework" in java_build:
        frameworks.add("Spring")
    for needle, label in [("spring-boot", "Spring Boot"), ("quarkus", "Quarkus"), ("micronaut", "Micronaut")]:
        if needle in java_build:
            frameworks.add(label)
    if "junit-jupiter" in java_build or "org.junit.jupiter" in java_build:
        test_frameworks.add("JUnit 5")
    elif "junit" in java_build:
        test_frameworks.add("JUnit")
    if "testng" in java_build:
        test_frameworks.add("TestNG")

    if any(Path(p).name.lower() == "go.mod" for p in relative_files):
        frameworks.add("Go Modules")
    if any(Path(p).name.lower() == "cargo.toml" for p in relative_files):
        frameworks.add("Cargo")
    return sorted(frameworks), sorted(test_frameworks)


def _commit_sha(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def analyze_repository(root: Path, repo_url: str, repo_name: str | None = None) -> RepoAnalysis:
    root = root.resolve()
    if not root.is_dir():
        raise RepoInputError(f"Repository path does not exist: {root}")

    files = _tracked_files(root)
    relative = [p.relative_to(root).as_posix() for p in files if p.is_file()]
    language_counts: Counter[str] = Counter()
    source_count = 0
    for rel in relative:
        suffix = Path(rel).suffix.lower()
        if suffix in LANGUAGES:
            language_counts[LANGUAGES[suffix]] += 1
            source_count += 1

    test_files = sorted(
        {
            rel
            for rel in relative
            if Path(rel).suffix.lower() in SOURCE_SUFFIXES and any(p.search(rel) for p in TEST_PATTERNS)
        }
    )
    test_dirs = sorted(
        {
            part
            for rel in test_files
            for part in rel.split("/")[:-1]
            if part.lower() in {"test", "tests", "__tests__", "spec", "specs", "e2e", "cypress", "playwright"}
        }
    )
    package_files = sorted(
        rel
        for rel in relative
        if Path(rel).name in PACKAGE_FILES or Path(rel).suffix.lower() in {".csproj", ".sln"}
    )
    frameworks, test_frameworks = _detect_frameworks(root, relative)

    name = repo_name or root.name
    notes: list[str] = []
    if test_frameworks and not test_files:
        notes.append(
            "A test framework/configuration appears present, but no test source files were detected."
        )
    if not source_count:
        notes.append("No supported source-code files were detected.")

    return RepoAnalysis(
        repo_url=repo_url,
        repo_name=name,
        commit_sha=_commit_sha(root),
        file_count=len(relative),
        source_file_count=source_count,
        source_files=sorted(rel for rel in relative if Path(rel).suffix.lower() in SOURCE_SUFFIXES),
        languages=dict(language_counts.most_common()),
        frameworks=frameworks,
        package_files=package_files[:80],
        test_files=test_files[:200],
        test_frameworks=test_frameworks,
        likely_test_directories=test_dirs,
        has_tests=bool(test_files),
        notes=notes,
    )


def _priority(rel: str) -> tuple[int, int, str]:
    lower = rel.lower()
    score = 0
    if Path(rel).name.lower() in {
        "readme.md",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "pom.xml",
        "go.mod",
    }:
        score += 100
    for word in ("controller", "route", "api", "service", "handler", "model", "domain", "repository", "auth"):
        if word in lower:
            score += 10
    depth = rel.count("/")
    return (-score, depth, rel)


def _numbered_source(content: str, suffix: str) -> str:
    lines = content.splitlines()
    if suffix != ".py":
        return "\n".join(f"{i}: {line}" for i, line in enumerate(lines, 1))
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return "\n".join(f"{i}: {line}" for i, line in enumerate(lines, 1))
    omitted = set()
    outline = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                omitted.update(range(body[0].lineno, body[0].end_lineno + 1))
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            outline.append((node.lineno, lines[node.lineno - 1].strip()))
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id.isupper() for t in node.targets
        ):
            outline.append((node.lineno, ast.unparse(node)[:240]))
    summary = "SYMBOLS / CONSTANTS (original line numbers):\n" + "\n".join(
        f"{line}: {text}" for line, text in sorted(outline)
    )
    body = "\n".join(
        f"{i}: {line}"
        for i, line in enumerate(lines, 1)
        if i not in omitted and line.strip() and not line.lstrip().startswith("#")
    )
    # Large modules must still expose some implementation, not just an outline.
    return summary[:2000] + "\nSOURCE (docstrings/comments omitted):\n" + body


def build_repository_evidence(
    root: Path,
    max_files: int = 36,
    max_total_chars: int = 70_000,
    preferred_paths: list[str] | None = None,
) -> str:
    """Source-first, line-numbered evidence; documents cannot exhaust source budget.

    Every candidate receives a bounded share. Generation can prioritize files
    cited by approved requirements. Selection is stable for identical trees.
    """
    if max_files <= 0 or max_total_chars <= 0:
        return ""
    root = root.resolve()
    preferred = set(preferred_paths or [])
    candidates = []
    for path in _tracked_files(root):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if (
            path.suffix.lower() in SOURCE_SUFFIXES | {".xhtml", ".html", ".xml"}
            or path.name in PACKAGE_FILES
            or path.name.lower() == "readme.md"
        ):
            candidates.append(rel)

    def rank(rel):
        source = Path(rel).suffix.lower() in SOURCE_SUFFIXES
        return (0 if rel in preferred else 1 if source else 2, *_priority(rel))

    selected = sorted(candidates, key=rank)[:max_files]
    chunks = []
    used = 0
    for index, rel in enumerate(selected):
        header = f"\n--- FILE: {rel} ---\n"
        remaining_files = len(selected) - index
        allowance = min(12_000, (max_total_chars - used) // remaining_files)
        if allowance <= len(header) + 1:
            continue
        content = _safe_read(root / rel, max_bytes=512_000)
        if not content:
            continue
        content = SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}=<redacted>", content)
        numbered = _numbered_source(content, Path(rel).suffix.lower())
        excerpt = numbered[: allowance - len(header) - 1]
        chunk = header + excerpt + "\n"
        chunks.append(chunk)
        used += len(chunk)
    return "".join(chunks)


_DEFINITION = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:public\s+|private\s+|protected\s+|static\s+|final\s+)*"
    r"(?:def|function|class|interface|func|fn|void|int|String|boolean|[A-Z][\w<>\[\]]*)\s+(\w+)\s*[(<{:]"
)


def _compact_lines(source_lines: list[str], first: int) -> str:
    """Original line numbers, without blank lines and comment-only lines."""
    return "\n".join(
        f"{first + i}: {line}"
        for i, line in enumerate(source_lines)
        if line.strip() and not line.lstrip().startswith(("#", "//", "*", "/*"))
    )


def _python_target_chunks(src: str, wanted: set[str]) -> list[tuple[str, int, int, str]]:
    """Complete bodies of the wanted top-level functions/classes plus the same-module helpers they call."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    top = {
        n.name: n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    chosen = [name for name in top if name in wanted]
    for name in list(
        chosen
    ):  # one hop of callees inside the same module (e.g. the scorers a total is built from)
        for call in ast.walk(top[name]):
            callee = call.func.id if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) else None
            if callee in top and callee not in chosen:
                chosen.append(callee)
    lines = src.splitlines()
    return [
        (
            name,
            top[name].lineno,
            top[name].end_lineno or top[name].lineno,
            _compact_lines(lines[top[name].lineno - 1 : top[name].end_lineno], top[name].lineno),
        )
        for name in chosen
    ]


def extract_target_sources(
    root: Path, targets: list[str], source_files: list[str], max_chars: int = 24_000
) -> str:
    """Complete source of the code each case targets, so tests are written against what the code really does.

    Head-truncated file excerpts routinely cut off the functions that matter (a 35 KB evaluator lost all of its scoring
    rules). A case's ``target`` names a file and symbol; this returns those symbols in full, plus their same-module helpers.
    """
    root = Path(root).resolve()
    chunks: list[str] = []
    used = 0
    seen: set[tuple[str, str]] = set()
    for text in targets:
        rels = sorted((f for f in source_files if f in text or Path(f).name in text), key=len, reverse=True)[
            :2
        ]
        wanted = set(re.findall(r"[A-Za-z_]\w*", text))
        for rel in rels:
            src = _safe_read(root / rel)
            if not src:
                continue
            if rel.endswith(".py"):
                found = _python_target_chunks(src, wanted)
            else:  # other languages: the definition line and what follows it
                all_lines = src.splitlines()
                found = []
                for index, line in enumerate(all_lines):
                    match = _DEFINITION.match(line)
                    if match and match.group(1) in wanted:
                        end = min(len(all_lines), index + 70)
                        found.append(
                            (match.group(1), index + 1, end, _compact_lines(all_lines[index:end], index + 1))
                        )
            for name, start, end, body in found:
                if (rel, name) in seen:
                    continue
                block = f"\n--- SOURCE: {rel} :: {name} (lines {start}-{end}) ---\n{body}\n"
                if used + len(block) > max_chars:
                    continue
                seen.add((rel, name))
                chunks.append(block)
                used += len(block)
    return "".join(chunks)


_JAVA_SIGNATURE = re.compile(
    r"^\s*(?:public|protected)\s+(?:static\s+|final\s+|abstract\s+|synchronized\s+)*[\w<>\[\], ?.]+?\s*\([^)]*\)?"
)
_JAVA_TYPE = re.compile(r"^\s*(?:public\s+)?(?:abstract\s+|final\s+)?(?:class|interface|enum)\s+\w+")


def java_signatures(text: str, limit: int = 30) -> list[str]:
    """Type declaration and public/protected constructor and method signatures (wrapped ones joined)."""
    source_lines = text.splitlines()
    signatures: list[str] = []
    for index, line in enumerate(source_lines):
        if not (_JAVA_TYPE.match(line) or _JAVA_SIGNATURE.match(line)):
            continue
        joined = line.strip()
        for follow in source_lines[index + 1 : index + 6]:  # a signature may wrap over several lines
            if ")" in joined or _JAVA_TYPE.match(line):
                break
            joined += " " + follow.strip()
        signatures.append(joined.rstrip("{").strip())
    return signatures[:limit]


def collaborator_signatures(
    root: Path, target_source: str, source_files: list[str], max_chars: int = 7_000
) -> str:
    """Public constructors/method signatures of the Java classes the targeted code mentions.

    A generated test that instantiates a collaborator (``new ShoppingCartItem(...)``) guesses its constructor when only
    the target's own source is supplied; one wrong call makes the whole module fail to compile. Signatures of every
    same-repository class named in the target source remove the guess.
    """
    root = Path(root).resolve()
    by_name: dict[str, list[str]] = {}
    for rel in source_files:
        if rel.endswith(".java"):
            by_name.setdefault(Path(rel).stem, []).append(rel)
    already = set(re.findall(r"--- SOURCE: (\S+) ::", target_source))
    mentioned = [
        name
        for name in dict.fromkeys(re.findall(r"\b[A-Z][A-Za-z0-9_]+\b", target_source))
        if len(by_name.get(name, [])) == 1 and by_name[name][0] not in already
    ]
    chunks: list[str] = []
    used = 0
    for name in mentioned:
        rel = by_name[name][0]
        lines = java_signatures(_safe_read(root / rel))
        if not lines:
            continue
        block = f"\n--- SIGNATURES: {rel} ---\n" + "\n".join(lines) + "\n"
        if used + len(block) > max_chars:
            break
        chunks.append(block)
        used += len(block)
    return "".join(chunks)


_GRADLE_COORD = re.compile(r"""['"]([\w.\-]+):([\w.\-]+)(?::[^'"]*)?['"]""")


def declared_dependencies(root: Path, package_files: list[str], limit: int = 120) -> list[str]:
    """Library names declared in the build/manifest files (host-parsed, so generation cannot guess libraries)."""
    root = Path(root).resolve()
    found: dict[str, None] = {}
    for rel in package_files:
        name, text = Path(rel).name.lower(), _safe_read(root / rel)
        if not text:
            continue
        if name == "pom.xml":
            names = re.findall(r"<artifactId>([^<$]+)</artifactId>", text)
        elif name.startswith("build.gradle"):
            names = [artifact for _group, artifact in _GRADLE_COORD.findall(text)]
        elif name == "package.json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            names = [
                k
                for group in ("dependencies", "devDependencies")
                if isinstance(data.get(group), dict)
                for k in data[group]
            ]
        elif name == "requirements.txt":
            names = re.findall(r"(?m)^([A-Za-z0-9][A-Za-z0-9_.\-]*)", text)
        elif name == "pyproject.toml":
            names = re.findall(r"""["']([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*(?:[<>=!~\[;]|["'])""", text)
        elif name == "go.mod":
            names = re.findall(r"(?m)^\s*(?:require\s+)?([\w.\-]+/[\w./\-]+)\s+v", text)
        elif name.endswith(".csproj"):
            names = re.findall(r'PackageReference\s+Include="([^"]+)"', text)
        else:
            continue
        found.update(dict.fromkeys(n.strip() for n in names))
    return sorted(found)[:limit]

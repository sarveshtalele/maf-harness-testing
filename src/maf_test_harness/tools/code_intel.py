"""Central, deterministic code-intelligence tools shared by the host and by every agent.

Import resolution, API signatures, symbol lookup, route inventory, installed packages, tool probes and the static
generation gates are facts the host can compute exactly. Asking a model to guess them costs a full generation call per
mistake, so the same functions serve three callers: the host pre-check (``static_gate``), the auto-fixers
(``autofix``) and the MAF function tools an agent may call while it writes (``agent_tools``).

Everything here is read-only and bounded. Nothing executes repository code (``build_gate`` does that, after the
environment install was approved).
"""

from __future__ import annotations

import ast
import difflib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ..envs import platform_compat
from ..envs.environment import _probe
from ..repo.analyzer import declared_dependencies, java_signatures
from ..skills.contracts import (
    _PLACEHOLDER,
    _SKIP_MARKERS,
    _bound_names,
    _module_symbols,
    _native_syntax_check,
    _repo_module_files,
    check_java_imports,
    check_python_imports,
    check_python_test_hygiene,
    check_route_literals,
)
from .mock_pitfalls import check_mock_pitfalls

MAX_FILE_BYTES = 512_000
READ_LINE_LIMIT = 160
READ_CHAR_LIMIT = 9_000
_JDK_PREFIXES = ("java.", "javax.", "jakarta.", "jdk.", "sun.")


@dataclass
class RepoContext:
    """What every tool needs to know about the repository under test (all paths relative to ``root``)."""

    root: Path
    source_files: list[str]
    test_files: list[str] = field(default_factory=list)
    package_files: list[str] = field(default_factory=list)
    workspace: Path | None = None
    routes: list[str] = field(default_factory=list)
    app_url: str | None = None
    _dependency_text: str | None = field(default=None, repr=False)

    @property
    def dependency_text(self) -> str:
        """What the package files declare, as text to match library names against.

        Only declarations count. A comment or a description that merely *mentions* a word ("The engine is pure
        standard library") must not make that word a declared dependency, or an unresolvable ``import engine`` would
        pass every gate and fail only at collection.
        """
        if self._dependency_text is None:
            self._dependency_text = "\n".join(
                self._declared_text(rel) for rel in self.package_files if (self.root / rel).is_file()
            )
        return self._dependency_text

    def _declared_text(self, rel: str) -> str:
        text = self.read(rel) or ""
        name = Path(rel).name.lower()
        if name == "pyproject.toml":  # dependency names only: not the project description or classifiers
            return "\n".join(declared_dependencies(self.root, [rel]))
        if name.endswith((".txt", ".in", ".cfg")) or name in {"setup.py", "pipfile"}:
            return _COMMENT.sub("", text)
        return text

    @property
    def known_files(self) -> set[str]:
        return {*self.source_files, *self.test_files, *self.package_files}

    def read(self, rel: str) -> str | None:
        """Repository file text, or ``None`` when it is missing, outside the repository, or too large."""
        target = (self.root / rel).resolve()
        if not target.is_relative_to(self.root.resolve()) or not target.is_file():
            return None
        if target.stat().st_size > MAX_FILE_BYTES:
            return None
        return target.read_text(encoding="utf-8", errors="replace")


_COMMENT = re.compile(r"(?m)(?:^|\s)#.*$")  # a whole-line or trailing comment in requirements-style files


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# --------------------------------------------------------------------------------------------- packages


def installed_packages(ctx: RepoContext, limit: int = 150) -> dict[str, Any]:
    """Distributions actually present in the isolated environment (not merely declared)."""
    workspace = ctx.workspace
    if workspace is None:
        return {"ecosystem": "unknown", "installed": [], "note": "environment not created yet"}
    site = platform_compat.venv_site_packages_glob(workspace / ".harness-venv")
    if site:
        names: dict[str, list[str]] = {}
        for info in sorted(site[0].glob("*.dist-info")):
            top = info / "top_level.txt"
            modules = top.read_text(errors="replace").split() if top.is_file() else []
            names[info.name.removesuffix(".dist-info").rsplit("-", 1)[0]] = modules
        return {
            "ecosystem": "python",
            "installed": sorted(names)[:limit],
            "import_names": {d: m for d, m in names.items() if m and _normal(d) != _normal(m[0])},
        }
    modules_dir = workspace / "node_modules"
    if modules_dir.is_dir():
        return {"ecosystem": "node", "installed": sorted(p.name for p in modules_dir.iterdir())[:limit]}
    declared = declared_dependencies(ctx.root, ctx.package_files, limit)
    return {"ecosystem": "declared-only", "installed": declared, "note": "no isolated environment found"}


def _installed_python_modules(ctx: RepoContext) -> set[str]:
    info = installed_packages(ctx)
    if info["ecosystem"] != "python":
        return set()
    return {_normal(d) for d in info["installed"]} | {
        _normal(m) for modules in info.get("import_names", {}).values() for m in modules
    }


# ------------------------------------------------------------------------------------------ resolution


def resolve_import(ctx: RepoContext, module: str, language: str = "python") -> dict[str, Any]:
    """Where ``module`` (Python dotted name, or Java fully qualified class) comes from, or a precise suggestion."""
    module = module.strip()
    if language.lower() in {"java", "kotlin"}:
        return _resolve_java(ctx, module)
    top = module.split(".")[0]
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    if top in stdlib:
        return {"module": module, "status": "stdlib"}
    modules = _repo_module_files(ctx.source_files)
    if module in modules:
        return {"module": module, "status": "repo", "file": modules[module]}
    if any(m.startswith(module + ".") for m in modules):
        return {"module": module, "status": "repo_package"}
    if _normal(top) in _installed_python_modules(ctx):
        return {"module": module, "status": "installed"}
    if _normal(top) in _normal(ctx.dependency_text):
        return {
            "module": module,
            "status": "declared_dependency",
            "note": "declared but not confirmed installed",
        }
    leaf = module.split(".")[-1]
    close = [m for m in modules if m.split(".")[-1] == leaf] or difflib.get_close_matches(
        module, list(modules), n=3, cutoff=0.6
    )
    return {
        "module": module,
        "status": "unresolved",
        "suggestions": [{"module": m, "file": modules[m]} for m in close[:3]],
        "note": "not stdlib, not a repository module, not a declared or installed dependency",
    }


def _resolve_java(ctx: RepoContext, fqcn: str) -> dict[str, Any]:
    if fqcn.startswith(_JDK_PREFIXES):
        return {"module": fqcn, "status": "jdk"}
    suffix = fqcn.replace(".", "/") + ".java"
    hit = next((f for f in ctx.source_files if f.endswith(suffix)), None)
    if hit:
        return {"module": fqcn, "status": "repo", "file": hit}
    simple = fqcn.split(".")[-1]
    same = [f for f in ctx.source_files if PurePosixPath(f).stem == simple]
    if _normal(fqcn.split(".")[1] if "." in fqcn else fqcn) in _normal(ctx.dependency_text):
        return {"module": fqcn, "status": "declared_dependency"}
    return {
        "module": fqcn,
        "status": "unresolved",
        "suggestions": [{"file": f, "fqcn": _java_fqcn(ctx, f)} for f in same[:3]],
        "note": "not JDK, not repository code; verify the library is in declared_dependencies",
    }


def _java_fqcn(ctx: RepoContext, rel: str) -> str:
    text = ctx.read(rel) or ""
    package = re.search(r"(?m)^\s*package\s+([\w.]+)\s*;", text)
    stem = PurePosixPath(rel).stem
    return f"{package.group(1)}.{stem}" if package else stem


def java_class_index(ctx: RepoContext) -> dict[str, list[str]]:
    """Simple class name -> fully qualified names of every repository Java class."""
    index: dict[str, list[str]] = {}
    for rel in ctx.source_files:
        if rel.endswith(".java") and "/src/test/" not in "/" + rel:
            index.setdefault(PurePosixPath(rel).stem, []).append(_java_fqcn(ctx, rel))
    return index


# ------------------------------------------------------------------------------------------------ API


def _python_api(source: str, limit: int) -> dict[str, Any]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"error": f"syntax error at line {exc.lineno}"}

    def signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        return f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}({ast.unparse(node.args)})"

    functions, classes, lazy = [], [], set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.append({"line": node.lineno, "signature": signature(node)})
        elif isinstance(node, ast.ClassDef):
            methods = [
                signature(m) for m in node.body if isinstance(m, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            classes.append({"line": node.lineno, "name": node.name, "methods": methods[:limit]})
    module_level = _module_symbols(source)
    for func in ast.walk(tree):
        if isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in ast.walk(func):
                if isinstance(node, ast.Import):
                    lazy.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    lazy.add(node.module.split(".")[0])
    return {
        "language": "python",
        "module_level_names": sorted(module_level[0]) if module_level else "unknown (star import)",
        "functions": functions[:limit],
        "classes": classes[:limit],
        "imported_only_inside_functions": sorted(lazy - (module_level[0] if module_level else set())),
        "note": "Names imported only inside functions are not attributes of the module: patch sys.modules for them.",
    }


def module_api(ctx: RepoContext, path: str, limit: int = 40) -> dict[str, Any]:
    """Real names and signatures of one repository file (no guessing of constructors or methods)."""
    if path not in ctx.known_files:
        return {"error": f"'{path}' is not a repository file; use find_symbol to locate it"}
    text = ctx.read(path)
    if text is None:
        return {"error": f"'{path}' cannot be read (missing or too large)"}
    if path.endswith(".py"):
        return {"path": path, **_python_api(text, limit)}
    if path.endswith(".java"):
        return {
            "path": path,
            "language": "java",
            "fqcn": _java_fqcn(ctx, path),
            "signatures": java_signatures(text, 60),
        }
    return {"path": path, "note": "no structured API extractor for this language; use read_source"}


def find_symbol(ctx: RepoContext, name: str, limit: int = 15) -> list[dict[str, Any]]:
    """Definitions of ``name`` across repository source files."""
    if not re.fullmatch(r"[A-Za-z_][\w$]*", name or ""):
        return []
    pattern = re.compile(
        rf"^\s*(?:(?:export|public|protected|private|static|final|abstract|async)\s+)*"
        rf"(?:def|class|interface|enum|function|record|fun)\s+{re.escape(name)}\b"
        rf"|^\s*(?:(?:public|protected|private|static|final|synchronized)\s+)+[\w<>\[\], ?.]+?\s+{re.escape(name)}\s*\("
    )
    found: list[dict[str, Any]] = []
    for rel in ctx.source_files:
        text = ctx.read(rel)
        if text is None or name not in text:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.match(line):
                found.append({"path": rel, "line": number, "definition": line.strip()[:160]})
                break
        if len(found) >= limit:
            break
    return found


def read_source(ctx: RepoContext, path: str, start: int = 1, end: int = READ_LINE_LIMIT) -> dict[str, Any]:
    """Numbered lines of a repository file (bounded), for facts the API summary does not show."""
    if path not in ctx.known_files:
        return {"error": f"'{path}' is not a repository file"}
    text = ctx.read(path)
    if text is None:
        return {"error": f"'{path}' cannot be read (missing or too large)"}
    lines = text.splitlines()
    start = max(1, int(start))
    end = min(len(lines), max(start, int(end)), start + READ_LINE_LIMIT - 1)
    body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))[:READ_CHAR_LIMIT]
    return {"path": path, "start": start, "end": end, "total_lines": len(lines), "text": body}


def list_routes(ctx: RepoContext) -> dict[str, Any]:
    return {
        "base_url": ctx.app_url,
        "routes": ctx.routes[:80],
        "rule": "BASE_URL already contains the context path: append only the route.",
    }


def probe_tools() -> list[dict[str, Any]]:
    """Versions of the build tools present on this host (never installs anything)."""
    return [_probe(tool) for tool in ("python3", "java", "mvn", "node", "npm")]


# ------------------------------------------------------------------------------------------ gates


def source_contracts(ctx: RepoContext) -> dict[str, str]:
    """Bounded source evidence for state, schema and payload contracts; never execute it."""
    selected = sorted(
        ctx.source_files,
        key=lambda p: (
            not any(
                word in p.lower()
                for word in ("connection", "schema", "db.py", "common.py", "models", "routes")
            ),
            p,
        ),
    )
    facts, remaining = {}, 24000
    for path in selected:
        text = ctx.read(path)
        if not text or not any(
            word in text for word in ("CREATE TABLE", "os.environ", "extract_usage", "@router.")
        ):
            continue
        excerpt = text[: min(7000, remaining)]
        facts[path] = excerpt
        remaining -= len(excerpt)
        if remaining <= 0:
            break
    return facts


def check_stateful_fixtures(ctx: RepoContext, path: str, content: str) -> None:
    tree = ast.parse(content)
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(statement):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any("os.environ" in ast.unparse(target) for target in targets):
                    raise ValueError(
                        f"{path}: import-time environment mutation leaks between test modules; use a per-test monkeypatch fixture and restore captured target constants"
                    )
            if isinstance(node, ast.Call) and ast.unparse(node.func) in {
                "os.environ.setdefault",
                "os.environ.update",
            }:
                raise ValueError(
                    f"{path}: configure environment inside a per-test fixture, never at module import"
                )
    schemas = {}
    for source in source_contracts(ctx).values():
        for table, body in re.findall(
            r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)\s*\((.*?)\);", source, re.I | re.S
        ):
            columns = set(re.findall(r"(?:^|,)\s*(\w+)\s+(?:INTEGER|TEXT|REAL|BLOB|NUMERIC)\b", body, re.I))
            if columns:
                schemas.setdefault(table.lower(), set()).update(column.lower() for column in columns)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        for table, columns in re.findall(r"INSERT(?: OR \w+)? INTO\s+(\w+)\s*\(([^)]+)\)", node.value, re.I):
            declared = schemas.get(table.lower())
            supplied = {column.strip().lower() for column in columns.split(",")}
            if declared and supplied - declared:
                # ALTER TABLE additions are legitimate schema facts too.
                additions = set(
                    re.findall(
                        r"ALTER TABLE\s+" + re.escape(table) + r"\s+ADD(?: COLUMN)?\s+(\w+)",
                        "\n".join(ctx.read(p) or "" for p in ctx.source_files),
                        re.I,
                    )
                )
                unknown = supplied - declared - {name.lower() for name in additions}
                if unknown:
                    raise ValueError(
                        f"{path}: INSERT uses nonexistent {table} column(s): {', '.join(sorted(unknown))}; use the repository schema"
                    )


def static_gate(ctx: RepoContext, path: str, content: str) -> None:
    """Every static defect the host can find in one generated file; raises ``ValueError`` describing the first."""
    if path.endswith(".py"):
        try:
            compile(content, path, "exec")
        except SyntaxError as exc:
            raise ValueError(f"{path} line {exc.lineno}: SyntaxError: {exc.msg}") from exc
        if ctx.source_files:
            installed = frozenset(_installed_python_modules(ctx)) if ctx.workspace else None
            check_python_imports(
                content,
                ctx.source_files,
                ctx.dependency_text,
                path,
                read_source=ctx.read,
                installed=installed,
            )
        check_python_test_hygiene(content, path, ctx.source_files, read_source=ctx.read)
        check_mock_pitfalls(content, path, ctx)
        check_stateful_fixtures(ctx, path, content)
    elif path.endswith((".java", ".kt")):
        if problem := _native_syntax_check(path, content):
            raise ValueError(f"{path}: {problem}")
        if "org.jboss.arquillian" in content:
            raise ValueError(
                f"{path} uses Arquillian: `mvn test` has no container. Use plain JUnit, or call the live application "
                'with java.net.http.HttpClient and System.getenv("BASE_URL").'
            )
        if ctx.dependency_text:
            check_java_imports(content, [*ctx.source_files, *ctx.test_files], ctx.dependency_text, path)
    elif problem := _native_syntax_check(path, content):
        raise ValueError(f"{path}: {problem}")
    if marker := _SKIP_MARKERS.search(content) or _PLACEHOLDER.search(content):
        raise ValueError(f"{path} contains a skip/placeholder marker ('{marker.group(0)}'); tests must run")
    check_route_literals(content, path, ctx.routes, ctx.app_url)


def undefined_python_names(content: str) -> list[str]:
    """Names loaded but never bound in a Python file (empty when unknowable)."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    bound = _bound_names(tree)
    if bound is None:
        return []
    builtins = set(dir(__import__("builtins")))
    return sorted(
        {
            n.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound | builtins
        }
    )

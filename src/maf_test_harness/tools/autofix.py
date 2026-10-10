"""Mechanical repairs the host applies to generated tests before any model retry.

Only fixes that cannot change what a test means are made: adding an import for a name that is undefined but has one
unambiguous source (a standard-library module, a JDK class, a repository class with a unique simple name), and pointing
an unresolvable import at the one repository module with the same leaf name. Everything else (a wrong assertion, a
patched name that does not exist, a guessed method) stays a model problem and is fed back with a concrete remedy.

Every applied fix is returned so the workflow can write it to the audit log.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from ..repo.import_recipes import ROOT_LINE
from .code_intel import RepoContext, java_class_index, resolve_import, undefined_python_names

Fix = dict[str, str]

# name -> import statement. Only names with a single sensible meaning; ``datetime`` (module or class) is left out.
_PYTHON_NAMES: dict[str, str] = {
    **{
        m: f"import {m}"
        for m in [
            "os",
            "sys",
            "re",
            "json",
            "time",
            "subprocess",
            "tempfile",
            "io",
            "shutil",
            "textwrap",
            "importlib",
            "argparse",
            "logging",
            "collections",
            "itertools",
            "functools",
            "contextlib",
            "unittest",
            "types",
            "typing",
            "pathlib",
            "inspect",
            "traceback",
            "hashlib",
            "base64",
            "csv",
            "sqlite3",
            "threading",
            "asyncio",
            "copy",
            "random",
            "string",
            "math",
            "pytest",
        ]
    },
    "Path": "from pathlib import Path",
    "mock": "from unittest import mock",
    "MagicMock": "from unittest.mock import MagicMock",
    "Mock": "from unittest.mock import Mock",
    "AsyncMock": "from unittest.mock import AsyncMock",
    "patch": "from unittest.mock import patch",
    "mock_open": "from unittest.mock import mock_open",
    "call": "from unittest.mock import call",
    "ANY": "from unittest.mock import ANY",
    "StringIO": "from io import StringIO",
    "ExitStack": "from contextlib import ExitStack",
    "contextmanager": "from contextlib import contextmanager",
    "redirect_stdout": "from contextlib import redirect_stdout",
    "defaultdict": "from collections import defaultdict",
    "Counter": "from collections import Counter",
    "namedtuple": "from collections import namedtuple",
    "dataclass": "from dataclasses import dataclass",
    "partial": "from functools import partial",
    "wraps": "from functools import wraps",
}


def _insert_python_imports(content: str, statements: list[str]) -> str:
    lines = content.splitlines()
    tree = ast.parse(content)
    imports = []  # the leading import block only: an import after code (sys.path.insert(...)) is too late
    for n in tree.body:
        if isinstance(n, ast.Import | ast.ImportFrom):
            imports.append(n)
        elif not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)):
            break
    if imports:
        at = imports[-1].end_lineno or imports[-1].lineno  # after the last import
    else:
        body = [
            n
            for n in tree.body
            if not (
                isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            )
        ]
        at = 0
        if body:
            first = body[0]
            start = min([first.lineno, *(d.lineno for d in getattr(first, "decorator_list", []))])
            while start > 1 and lines[start - 2].lstrip().startswith(
                "#"
            ):  # keep comments attached to their code
                start -= 1
            at = start - 1
        else:
            at = len(lines)
        statements = [*statements, ""]
    return "\n".join([*lines[:at], *statements, *lines[at:]]) + ("\n" if content.endswith("\n") else "")


def fix_python(ctx: RepoContext, path: str, content: str) -> tuple[str, list[Fix]]:
    fixes: list[Fix] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content, fixes
    # 1. an import of a module that does not resolve, when exactly one repository module has that leaf name
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            old = node.module
        elif isinstance(node, ast.Import) and len(node.names) == 1 and not node.names[0].asname:
            old = node.names[0].name
        else:
            continue
        info = resolve_import(ctx, old)
        suggestions = info.get("suggestions") or []
        if info["status"] == "unresolved" and len(suggestions) == 1:
            new = suggestions[0]["module"]
            if all(part.isidentifier() for part in new.split(".")) and new != old:
                content = re.sub(
                    rf"(?m)^(\s*(?:from|import)\s+){re.escape(old)}(?=\s|$)", rf"\g<1>{new}", content, count=1
                )
                fixes.append({"path": path, "kind": "import_path", "detail": f"{old} -> {new}"})
    # 2. names used but never bound, with one unambiguous import
    wanted = [n for n in undefined_python_names(content) if n in _PYTHON_NAMES]
    if wanted:
        statements = sorted({_PYTHON_NAMES[n] for n in wanted}, key=lambda s: (s.startswith("from"), s))
        content = _insert_python_imports(content, statements)
        fixes += [{"path": path, "kind": "missing_import", "detail": stmt} for stmt in statements]
    return content, fixes


def _shared_parts(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a.split("/"), b.split("/"), strict=False):
        if x != y:
            break
        n += 1
    return n


def _unique_recipe(
    module: str, recipes: dict[str, dict[str, str]], preferred: list[str], test_path: str = ""
) -> dict[str, str] | None:
    """The one recipe an import of ``module`` can mean: its own module, or a suffix of the normalized path."""
    hits = {}
    for target, r in recipes.items():
        full = re.sub(r"[-\s]", "_", target.removesuffix(".py").replace("/", "."))
        if module in (r["module"], full) or full.endswith("." + module) or r["module"].endswith("." + module):
            hits[target] = r
    if (
        len(hits) > 1
    ):  # the case's own linked source files decide among look-alikes (many skills/*/scripts/main.py)
        hits = {t: r for t, r in hits.items() if t in preferred} or hits
    if len(hits) > 1 and test_path:  # then the test's own location: skills/x/y/tests/.. -> skills/x/y/scripts
        score = {t: _shared_parts(r["sys_path_root"], test_path) for t, r in hits.items()}
        best = [t for t, v in score.items() if v == max(score.values())]
        hits = {t: hits[t] for t in best}
    return next(iter(hits.values())) if len(hits) == 1 else None


def _bootstrap_line(tree: ast.Module) -> int:
    """Index (0-based line) after the docstring and ``from __future__`` imports."""
    at = 0
    for n in tree.body:
        doc = isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
        if doc or (isinstance(n, ast.ImportFrom) and n.module == "__future__"):
            at = n.end_lineno or n.lineno
        else:
            break
    return at


def fix_import_recipes(
    path: str, content: str, recipes: dict[str, dict[str, str]], preferred: list[str] | None = None
) -> tuple[str, list[Fix]]:
    """Make imports of scripts the repo cannot dotted-import use the recipe's literal bootstrap (see import_recipes)."""
    fixes: list[Fix] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content, fixes
    lines = content.splitlines()
    need_bootstrap = False
    used: list[dict[str, str]] = []
    for node in tree.body:  # module-level imports only
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            recipe = _unique_recipe(node.module, recipes, preferred or [], path)
        elif isinstance(node, ast.Import) and len(node.names) == 1 and not node.names[0].asname:
            recipe = _unique_recipe(node.names[0].name, recipes, preferred or [], path)
        else:
            continue
        if recipe is None:
            continue
        used.append(recipe)
        old = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
        if old != recipe["module"]:
            names = ", ".join(a.name + (f" as {a.asname}" if a.asname else "") for a in node.names)
            new = f"from {recipe['module']} import {names}" if isinstance(node, ast.ImportFrom) else None
            if new is None:
                continue
            lines[node.lineno - 1 : node.end_lineno] = [new] + [None] * (
                (node.end_lineno or node.lineno) - node.lineno
            )  # type: ignore[list-item]
            fixes.append(
                {"path": path, "kind": "import_recipe_applied", "detail": f"{old} -> {recipe['module']}"}
            )
            need_bootstrap = True
        elif "sys.path" not in content:
            fixes.append({"path": path, "kind": "import_recipe_applied", "detail": f"bootstrap for {old}"})
            need_bootstrap = True
    lines = [x for x in lines if x is not None]
    content = "\n".join(lines) + ("\n" if content.endswith("\n") else "")
    if need_bootstrap and not re.search(r"(?m)^_ROOT\s*=", content):
        r = used[0]
        block = r["code"].split("\n")
        block = block[: block.index(r["import"])]  # everything but the import itself
        at = _bootstrap_line(ast.parse(content))
        rows = content.splitlines()
        content = "\n".join([*rows[:at], *block, *rows[at:]]) + ("\n" if content.endswith("\n") else "")
    return content, fixes


_PARENTS_DIV = re.compile(
    r"(?:Path\(__file__\)(?:\.resolve\(\))?|Path\(__file__\)\.absolute\(\))\.parents(?=\s*/)"
)


def fix_path_parents_division(path: str, content: str) -> tuple[str, list[Fix]]:
    """``Path(__file__).resolve().parents / "x"`` raises TypeError; the intent is the repository root."""
    if not _PARENTS_DIV.search(content):
        return content, []
    content = _PARENTS_DIV.sub("_ROOT", content)
    if not re.search(r"(?m)^_ROOT\s*=", content):
        try:
            at = _bootstrap_line(ast.parse(content))
        except SyntaxError:
            return content, []
        rows = content.splitlines()
        content = "\n".join([*rows[:at], "from pathlib import Path", ROOT_LINE, *rows[at:]]) + "\n"
    return content, [{"path": path, "kind": "path_parents_division", "detail": "parents / x -> _ROOT / x"}]


def fix_missing_purge(path: str, content: str, recipes: dict[str, dict[str, str]]) -> tuple[str, list[Fix]]:
    """A test that puts a colliding root on sys.path must first drop the other package cached in sys.modules."""
    if "sys.modules" in content:
        return content, []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content, []
    for r in recipes.values():
        if not r.get("purge"):
            continue
        root_parts = r["sys_path_root"].split("/")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func) == "sys.path.insert"
                and any(p in ast.unparse(node.value) for p in root_parts[-2:])
            ):
                rows = content.splitlines()
                indent = " " * node.col_offset
                rows.insert(node.lineno - 1, indent + r["purge"])
                return "\n".join(rows) + "\n", [
                    {"path": path, "kind": "sys_modules_purge", "detail": r["purge"]}
                ]
    return content, []


_JDK: dict[str, str] = {
    **{
        n: f"java.util.{n}"
        for n in [
            "List",
            "ArrayList",
            "Map",
            "HashMap",
            "LinkedHashMap",
            "TreeMap",
            "Set",
            "HashSet",
            "LinkedHashSet",
            "Collection",
            "Collections",
            "Arrays",
            "Optional",
            "Objects",
            "UUID",
            "Date",
            "Calendar",
            "Iterator",
            "Locale",
            "Random",
            "Scanner",
            "Deque",
            "ArrayDeque",
            "LinkedList",
            "Queue",
            "Properties",
            "StringJoiner",
        ]
    },
    **{
        n: f"java.io.{n}"
        for n in [
            "IOException",
            "UncheckedIOException",
            "File",
            "InputStream",
            "OutputStream",
            "ByteArrayOutputStream",
            "ByteArrayInputStream",
            "PrintStream",
            "StringWriter",
            "StringReader",
            "BufferedReader",
            "InputStreamReader",
            "Serializable",
        ]
    },
    **{
        n: f"java.time.{n}"
        for n in ["Duration", "Instant", "LocalDate", "LocalDateTime", "LocalTime", "ZonedDateTime", "ZoneId"]
    },
    **{n: f"java.net.http.{n}" for n in ["HttpClient", "HttpRequest", "HttpResponse"]},
    "URI": "java.net.URI",
    "URL": "java.net.URL",
    "BigDecimal": "java.math.BigDecimal",
    "BigInteger": "java.math.BigInteger",
    "StandardCharsets": "java.nio.charset.StandardCharsets",
    "Files": "java.nio.file.Files",
    "Path": "java.nio.file.Path",
    "Paths": "java.nio.file.Paths",
    "TimeUnit": "java.util.concurrent.TimeUnit",
    "CompletableFuture": "java.util.concurrent.CompletableFuture",
    "AtomicInteger": "java.util.concurrent.atomic.AtomicInteger",
    "AtomicLong": "java.util.concurrent.atomic.AtomicLong",
    "Pattern": "java.util.regex.Pattern",
    "Matcher": "java.util.regex.Matcher",
    "Collectors": "java.util.stream.Collectors",
    "Stream": "java.util.stream.Stream",
    "IntStream": "java.util.stream.IntStream",
    "Function": "java.util.function.Function",
    "Supplier": "java.util.function.Supplier",
    "Consumer": "java.util.function.Consumer",
    "Predicate": "java.util.function.Predicate",
}
_JUPITER: dict[str, str] = {
    n: f"org.junit.jupiter.api.{n}"
    for n in [
        "Test",
        "BeforeEach",
        "AfterEach",
        "BeforeAll",
        "AfterAll",
        "Assertions",
        "DisplayName",
        "Disabled",
        "Nested",
    ]
}
_JUNIT4: dict[str, str] = {
    "Test": "org.junit.Test",
    "Before": "org.junit.Before",
    "After": "org.junit.After",
    "BeforeClass": "org.junit.BeforeClass",
    "AfterClass": "org.junit.AfterClass",
    "Assert": "org.junit.Assert",
}
_ASSERT_CALL = re.compile(
    r"(?<![\w.])assert(?:Equals|True|False|NotNull|Null|Throws|Same|NotSame|ArrayEquals|That)\s*\("
)


def _java_code(content: str) -> str:
    """Source with comments and string/char literals blanked so identifiers inside them are not read as types."""
    content = re.sub(r"/\*.*?\*/", " ", content, flags=re.S)
    content = re.sub(r"//[^\n]*", " ", content)
    return re.sub(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])\'', '""', content)


def fix_java(ctx: RepoContext, path: str, content: str) -> tuple[str, list[Fix]]:
    fixes: list[Fix] = []
    code = _java_code(content)
    package = re.search(r"(?m)^\s*package\s+([\w.]+)\s*;", content)
    own_package = package.group(1) if package else ""
    imported = set(re.findall(r"(?m)^\s*import\s+(?:static\s+)?[\w.]*?\.?(\w+)\s*;", content))
    declared = set(re.findall(r"\b(?:class|interface|enum|record)\s+(\w+)", code))
    used = {m.group(1) for m in re.finditer(r"(?<![\w.@])([A-Z][A-Za-z0-9_]*)\b(?!\s*\()", code)} | {
        m.group(1) for m in re.finditer(r"\bnew\s+([A-Z]\w*)", code)
    }
    annotations = set(re.findall(r"@([A-Z]\w*)", code))
    deps = ctx.dependency_text.lower()
    jupiter = "junit-jupiter" in deps or "junit5" in deps
    junit4 = not jupiter and "junit" in deps
    index = java_class_index(ctx)
    additions: dict[str, str] = {}
    for name in sorted((used | annotations) - imported - declared):
        repo = sorted(set(index.get(name, [])))
        annotation_only = name in annotations and name not in used
        if len(repo) == 1:
            fqcn = repo[0]
            if fqcn.rsplit(".", 1)[0] != own_package and "." in fqcn:
                additions[name] = fqcn
        elif not repo:
            if name in _JDK and not annotation_only:
                additions[name] = _JDK[name]
            elif jupiter and name in _JUPITER:
                additions[name] = _JUPITER[name]
            elif junit4 and name in _JUNIT4:
                additions[name] = _JUNIT4[name]
    static = ""
    if _ASSERT_CALL.search(code) and not re.search(r"import\s+static\s+org\.junit\.[\w.]*Assert", content):
        static = (
            "import static org.junit.jupiter.api.Assertions.*;"
            if jupiter
            else "import static org.junit.Assert.*;"
            if junit4
            else ""
        )
    statements = sorted(f"import {fqcn};" for fqcn in additions.values()) + ([static] if static else [])
    if not statements:
        return content, fixes
    lines = content.splitlines()
    import_lines = [i for i, line in enumerate(lines) if re.match(r"\s*import\s", line)]
    if import_lines:
        at = import_lines[-1] + 1
    elif package:
        at = next(i for i, line in enumerate(lines) if re.match(r"\s*package\s", line)) + 1
        statements = ["", *statements]
    else:
        at = 0
        statements = [*statements, ""]
    content = "\n".join([*lines[:at], *statements, *lines[at:]]) + ("\n" if content.endswith("\n") else "")
    fixes += [{"path": path, "kind": "missing_import", "detail": s} for s in statements if s]
    return content, fixes


def drop_extra_tests(item: dict[str, Any]) -> list[Fix]:
    """One positive case -> one test: drop top-level `test_*` functions after the first when nothing else uses them."""
    ids = item.get("case_ids") or []
    if len(ids) != 1 or not str(ids[0]).startswith("TC-POS"):
        return []
    try:
        tree = ast.parse(item["content"])
    except SyntaxError:
        return []
    tests = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name.startswith("test_")
    ]
    lines = item["content"].splitlines()
    fixes: list[Fix] = []
    for node in tests[1:][::-1]:
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)]) - 1
        rest = "\n".join(lines[:start] + lines[node.end_lineno :])
        if re.search(rf"\b{re.escape(node.name)}\b", rest):
            continue
        lines[start : node.end_lineno] = []
        fixes.append({"path": item["path"], "kind": "extra_test_dropped", "detail": node.name})
    if fixes:
        item["content"] = "\n".join(lines).rstrip() + "\n"
    return fixes


def autofix_files(
    ctx: RepoContext,
    payload: dict[str, Any],
    recipes: dict[str, dict[str, str]] | None = None,
    preferred: list[str] | None = None,
) -> tuple[dict[str, Any], list[Fix]]:
    """Apply the language fixers to every generated file; returns the updated payload and the applied fixes."""
    applied: list[Fix] = []
    for item in payload.get("files", []):
        path, content = item["path"], item["content"]
        if path.endswith(".py"):
            content, fixes = fix_path_parents_division(path, content)
            if recipes:
                content, more = fix_import_recipes(path, content, recipes, preferred)
                fixes += more
                content, more = fix_missing_purge(path, content, recipes)
                fixes += more
            fixed, more = fix_python(ctx, path, content)
            fixes += more
            item["content"] = fixed
            fixes += drop_extra_tests(item)
            fixed = item["content"]
        elif path.endswith(".java"):
            fixed, fixes = fix_java(ctx, path, content)
        else:
            continue
        if fixes:
            item["content"] = fixed
            applied += fixes
    return payload, applied

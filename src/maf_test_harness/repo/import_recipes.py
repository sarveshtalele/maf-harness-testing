"""Correct import recipes for Python targets that a plain dotted import cannot reach.

A file under a directory such as ``skills/change-impact-analysis/`` has no dotted module path, so a model that is only
told "do not translate '-' to '_'" has no alternative. The application itself usually has one: a launcher that puts a
directory on ``sys.path`` and imports by bare name. This module finds that directory (or derives one) and states the
exact ``sys.path`` root and import line, so generation can copy it and the validator can quote it.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath

_TEST_NAMES = ("test_", "conftest")


def _is_test(rel: str) -> bool:
    p = PurePosixPath(rel)
    return p.name.startswith(_TEST_NAMES) or p.name.endswith("_test.py") or "tests" in p.parts


def _eval_dir(
    node: ast.AST, fdir: PurePosixPath, rel: str, env: dict[str, PurePosixPath]
) -> PurePosixPath | None:
    """Evaluate a path expression built from ``__file__`` to a repo-relative path (a file is the file path itself)."""
    if isinstance(node, ast.Name):
        return PurePosixPath(rel) if node.id == "__file__" else env.get(node.id)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        base = _eval_dir(node.value, fdir, rel, env)
        return base.parent if base is not None else None
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "parents"
    ):
        base = _eval_dir(node.value.value, fdir, rel, env)
        idx = node.slice
        if base is None or not (isinstance(idx, ast.Constant) and isinstance(idx.value, int)):
            return None
        out = base
        for _ in range(idx.value + 1):
            out = out.parent
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _eval_dir(node.left, fdir, rel, env)
        if left is not None and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return left / node.right.value
        return None
    if isinstance(node, ast.Call):
        name = ast.unparse(node.func)
        if (
            name in {"Path", "str", "os.path.abspath", "os.path.realpath", "Path.resolve", "os.fspath"}
            and node.args
        ):
            return _eval_dir(node.args[0], fdir, rel, env)
        if name == "os.path.dirname" and node.args:
            base = _eval_dir(node.args[0], fdir, rel, env)
            return base.parent if base is not None else None
        if name == "os.path.join" and node.args:
            base = _eval_dir(node.args[0], fdir, rel, env)
            parts = [
                a.value for a in node.args[1:] if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
            return base.joinpath(*parts) if base is not None and len(parts) == len(node.args) - 1 else None
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"resolve", "absolute"}:
            return _eval_dir(node.func.value, fdir, rel, env)
    return None


def inserted_sys_path_dirs(rel: str, source: str) -> list[str]:
    """Repo-relative directories this file puts on ``sys.path`` (insert/append of a ``__file__``-relative path)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    fdir = PurePosixPath(rel).parent
    env: dict[str, PurePosixPath] = {}
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if (val := _eval_dir(node.value, fdir, rel, env)) is not None:
                env[node.targets[0].id] = val
        elif (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) in {"sys.path.insert", "sys.path.append"}
            and node.args
            and (val := _eval_dir(node.args[-1], fdir, rel, env)) is not None
        ):
            found.append(val.as_posix())
    return found


def _public_name(source: str, hint: str = "") -> str:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ""
    kinds = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    names = [n.name for n in tree.body if isinstance(n, kinds) and not n.name.startswith("_")]
    if named := next((n for n in names if re.search(rf"\b{n}\b", hint)), ""):  # the symbol the case targets
        return named
    for kind in (ast.ClassDef, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for node in tree.body:
            if isinstance(node, kind) and not node.name.startswith("_"):
                return node.name
    return ""


def _ok(parts: tuple[str, ...]) -> bool:
    return all(p.isidentifier() for p in parts)


def recipes_for_files(
    source_files: list[str], read: Callable[[str], str | None], target_files: list[str], hint: str = ""
) -> dict[str, dict[str, str]]:
    py = [f for f in source_files if f.endswith(".py")]
    out: dict[str, dict[str, str]] = {}
    for target in target_files:
        parts = PurePosixPath(target).with_suffix("").parts
        bad = [i for i, p in enumerate(parts) if not p.isidentifier()]
        if not bad or bad[-1] == len(parts) - 1 or not target.endswith(".py"):
            continue  # importable already, or the file name itself is unimportable (needs importlib)
        base = PurePosixPath(*parts[: bad[-1] + 1])  # the last non-identifier directory
        roots: list[str] = []
        # The repo's own tests first, then the application's launchers: both show a root that is known to work.
        scan = sorted((f for f in py if PurePosixPath(f).is_relative_to(base)), key=lambda f: not _is_test(f))
        for f in scan:
            for d in inserted_sys_path_dirs(f, read(f) or ""):
                if PurePosixPath(target).is_relative_to(d) and _ok(
                    PurePosixPath(target).with_suffix("").relative_to(d).parts
                ):
                    roots.append(d)
        root = roots[0] if roots else base.as_posix()
        seen = "as the application/its tests already do" if roots else "derived from the path"
        module = PurePosixPath(target).with_suffix("").relative_to(root).parts
        dotted = ".".join(module)
        name = _public_name(read(target) or "", hint)
        line = f"from {dotted} import {name}" if name else f"import {dotted}"
        out[target] = {
            "sys_path_root": root,
            "import": line,
            "how": (
                f"In the test, find the repository root with pathlib (walk Path(__file__).resolve().parents until one "
                f"holds pyproject.toml, setup.cfg or .git), `sys.path.insert(0, str(repo_root / '{root}'))`, then "
                f"`{line}` ({seen}). Never import via skills.<dir_with_hyphens_renamed>."
            ),
        }
    return out


def python_import_recipes(
    root: Path, source_files: list[str], targets: list[str]
) -> dict[str, dict[str, str]]:
    """``{target_file: {sys_path_root, import, how}}`` for target .py files not reachable by a plain dotted import."""
    root = Path(root)

    def read(rel: str) -> str | None:
        try:
            return (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    files = sorted(
        {
            f
            for f in source_files
            if f.endswith(".py")
            # whole path, or the file name not glued to a longer name (analyzer.py must not match impact_analyzer.py)
            and any(f in t or re.search(r"(?<![\w-])" + re.escape(Path(f).name), t) for t in targets)
        }
    )
    return recipes_for_files(source_files, read, files, " ".join(targets))

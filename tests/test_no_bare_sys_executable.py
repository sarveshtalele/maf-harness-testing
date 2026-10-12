"""Regression guard: nothing in src/maf_test_harness runs ``sys.executable`` or a bare ``python``/``python3`` name.

In the PyInstaller-frozen desktop app ``sys.executable`` is ``maf-backend``, not Python, and a bare ``python``/``python3``
can be the Windows Store alias that silently downloads an interpreter. Interpreters must come from
``paths.python_argv()`` or from a detected real Python (``envs.toolchains``).

Each use is attributed to its innermost function, or to the module-level name it sits in (tables, templates). Every
remaining use must be listed below as ``(file, owner)`` with a one-line reason. An entry that no longer matches a real
use fails too, so the lists cannot rot silently. A new use needs a deliberate review here.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "maf_test_harness"
BARE_NAMES = {"python", "python3", "python.exe", "python3.exe"}

ALLOWED_SYS_EXECUTABLE = {
    "paths.py:python_argv": "the one frozen-aware interpreter argv: [sys.executable] or [backend, --run-python]",
    "paths.py:resource_root": "the frozen backend's own folder (resource root), not an interpreter",
    "envs/toolchains.py:_scan_interpreters": "drops the frozen backend from detected Pythons; adds the running interpreter only when not frozen",
    "envs/toolchains.py:_PYTHON_PROBE": "probe code a detected real Python runs to print its own executable path",
    "envs/runners.py:_create_venv_step": "non-frozen branch only: the running interpreter creates the venv; frozen uses a detected Python",
    "qa/packaging.py:RERUN": "text of the rerun script, which later runs under the user's real Python, not the backend",
}

ALLOWED_BARE_NAMES = {
    "agent/personas.py:_STACK_KNOWLEDGE": "keyword table for stack detection; never executed",
    "envs/environment.py:TOOLS": "version-command table; _probe replaces argv[0] with a detected interpreter path",
    "envs/runners.py:resolve_ecosystem": "file-suffix to ecosystem name mapping; never executed",
    "envs/runners.py:_ecosystem_of": "classifies an argv by name for error tables; never executed",
    "envs/runners.py:repository_components": "component ecosystem name for planning; never executed",
    "envs/toolchains.py:_scan_interpreters": "names resolved by shutil.which then probed; only Windows-excluded names are bare; resolved paths are alias-checked",
    "envs/toolchains.py:tools": "maps a toolchain kind to the executable it covers; never executed",
    "repo/stack.py:REQUIRED_TOOLS": "tool names checked with platform_compat.tool_present (which); never executed",
    "tools/code_intel.py:probe_tools": "names passed to environment._probe, which runs the detected interpreter path",
    "tools/env_tools.py:KINDS": "enumeration of toolchain kinds in a tool schema; never executed",
    "tools/env_tools.py:ECOSYSTEMS": "enumeration of ecosystems in a tool schema; never executed",
}


def _is_sys_executable(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "executable"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _is_sys_executable_import(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.ImportFrom)
        and node.module == "sys"
        and any(a.name == "executable" for a in node.names)
    )


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        ):
            ids.add(id(body[0].value))
    return ids


def _owned_nodes(tree: ast.Module):
    """Every node with its owner: the innermost function, else the module-level name (or class) it belongs to."""

    def walk(node: ast.AST, owner: str):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        yield node, owner
        for child in ast.iter_child_nodes(node):
            yield from walk(child, owner)

    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
            top = stmt.targets[0].id
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            top = stmt.target.id
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            top = stmt.name
        else:
            top = "<module>"
        yield from walk(stmt, top)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _sys_executable_uses(path: Path) -> list[tuple[str, str, int]]:
    rel = path.relative_to(SRC).as_posix()
    tree = _parse(path)
    docstrings = _docstrings(tree)
    uses = []
    for node, owner in _owned_nodes(tree):
        if (
            _is_sys_executable(node)
            or _is_sys_executable_import(node)
            or (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and "sys.executable" in node.value  # template text that embeds the executable (e.g. RERUN)
            )
        ):
            uses.append((rel, owner, node.lineno))
    return uses


def _bare_name_uses(path: Path) -> list[tuple[str, str, int]]:
    rel = path.relative_to(SRC).as_posix()
    uses = []
    for node, owner in _owned_nodes(_parse(path)):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value in BARE_NAMES:
                uses.append((rel, owner, node.lineno))
        elif (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value in BARE_NAMES
            and any(
                isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == 0
                for t in node.targets
            )
        ):
            uses.append((rel, owner, node.lineno))
    return uses


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _check(uses, allowed):
    seen = {f"{file}:{owner}" for file, owner, _ in uses}
    unexpected = sorted((f, o, line) for f, o, line in uses if f"{f}:{o}" not in allowed)
    stale = sorted(set(allowed) - seen)
    return unexpected, stale


def test_sys_executable_is_used_only_where_reviewed():
    uses = [use for path in _sources() for use in _sys_executable_uses(path)]
    unexpected, stale = _check(uses, ALLOWED_SYS_EXECUTABLE)
    assert not unexpected, (
        "sys.executable used outside the reviewed allow-list (use paths.python_argv() or a detected Python): "
        + "; ".join(f"{f}:{line} in {o}" for f, o, line in unexpected)
    )
    assert not stale, f"sys.executable allow-list entries with no matching use; remove them: {stale}"


def test_no_bare_python_names_as_argv_zero():
    uses = [use for path in _sources() for use in _bare_name_uses(path)]
    unexpected, stale = _check(uses, ALLOWED_BARE_NAMES)
    assert not unexpected, (
        "bare python/python3 name outside the reviewed list (it may run the Windows Store alias): "
        + "; ".join(f"{f}:{line} in {o}" for f, o, line in unexpected)
    )
    assert not stale, f"bare-name allow-list entries with no matching use; remove them: {stale}"


def test_allow_lists_have_a_reason_for_every_entry():
    for allowed in (ALLOWED_SYS_EXECUTABLE, ALLOWED_BARE_NAMES):
        for key, reason in allowed.items():
            file, _, owner = key.partition(":")
            assert file.endswith(".py") and owner.isidentifier(), key
            assert isinstance(reason, str) and 20 <= len(reason) <= 200 and "\n" not in reason, key


def test_detectors_flag_the_forbidden_patterns(tmp_path, monkeypatch):
    """The guard must fire on each forbidden form, so a passing run means something."""
    monkeypatch.setattr(__import__(__name__), "SRC", tmp_path)
    module = tmp_path / "bad.py"
    module.write_text(
        '"""Docstring may mention sys.executable."""\n'
        "import subprocess, sys\n"
        "def run_it():\n"
        "    subprocess.run([sys.executable, '-c', 'pass'])\n"
        "CMD = ['python3', '--version']\n"
        "def other():\n"
        "    argv = []\n"
        "    argv[0] = 'python'\n",
        encoding="utf-8",
    )
    assert {owner for _, owner, _ in _sys_executable_uses(module)} == {"run_it"}
    assert {owner for _, owner, _ in _bare_name_uses(module)} == {"CMD", "other"}

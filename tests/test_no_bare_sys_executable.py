"""Regression guard: nothing in src/maf_test_harness runs ``sys.executable`` or a bare Python name.

In the PyInstaller-frozen desktop app ``sys.executable`` is ``maf-backend``, not Python, and a bare ``python``/``python3``
can be the Windows Store alias that silently downloads an interpreter. Interpreters must come from
``paths.python_argv()`` or from a detected real Python (``envs.toolchains``).

Enforced, by AST, over every .py under src/maf_test_harness:
- ``sys.executable`` in any form: ``sys.executable``, through an alias (``import sys as s``), as ``os.sys.executable``,
  as ``getattr(x, "executable")`` with a literal name, through ``from sys import executable`` or ``from sys import *``,
  and template text that embeds ``sys.executable`` (e.g. the RERUN script).
- A bare Python name (py, pythonw, python, python3, python3.N, optionally .exe) as the first element of a list or tuple,
  as a comprehension element built from an f-string such as ``f"python3.{n}"``, as ``argv[0] = ...``, or as the first
  word of a command string passed to run, Popen, run_command, run_cancellable, check_output, check_call, call or Step.

Not enforced: names assembled at run time (importlib, eval, concatenation across statements). Review those by hand.

Each use is attributed to its owner: the innermost qualified def (``Class.method``, ``function``) or the module-level
name it sits in. Every owner that has a use must be listed with its exact expected count and a one-line reason. A new
use, an extra use in a listed owner, or an entry that no longer matches all fail the test, so a new use needs review.
"""

import ast
import re
import sys
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "maf_test_harness"
MODULE = "<module>"
BARE_PYTHON = re.compile(r"^(?:py|pythonw|python|python3|python3\.\d+)(?:\.exe)?$", re.IGNORECASE)
COMMAND_RUNNERS = {
    "run",
    "Popen",
    "run_command",
    "run_cancellable",
    "check_output",
    "check_call",
    "call",
    "Step",
}

# "file.py:Owner" -> (exact number of uses, one-line reason)
ALLOWED_SYS_EXECUTABLE: dict[str, tuple[int, str]] = {
    "paths.py:python_argv": (
        2,
        "the one frozen-aware interpreter argv: [sys.executable] or [backend, --run-python]",
    ),
    "paths.py:resource_root": (1, "the frozen backend's own folder (resource root), not an interpreter"),
    "envs/toolchains.py:_scan_interpreters": (
        2,
        "drops the frozen backend from detected Pythons; adds the running one only when not frozen",
    ),
    "envs/toolchains.py:_PYTHON_PROBE": (
        1,
        "probe code a detected real Python runs to print its own executable path",
    ),
    "envs/runners.py:_create_venv_step": (
        1,
        "non-frozen branch only: the running interpreter creates the venv; frozen uses a detected Python",
    ),
    "qa/packaging.py:RERUN": (
        1,
        "text of the rerun script, which later runs under the user's real Python, not the backend",
    ),
}

# "file.py:Owner" -> (exact number of uses, one-line reason)
ALLOWED_BARE_NAMES: dict[str, tuple[int, str]] = {
    "agent/personas.py:_STACK_KNOWLEDGE": (2, "keyword table for stack detection; never executed"),
    "envs/environment.py:TOOLS": (
        1,
        "version-command table; _probe replaces argv[0] with a detected interpreter path",
    ),
    "envs/runners.py:resolve_ecosystem": (1, "file-suffix to ecosystem name mapping; never executed"),
    "envs/runners.py:_ecosystem_of": (1, "classifies an argv by name for error tables; never executed"),
    "envs/runners.py:repository_components": (1, "component ecosystem name for planning; never executed"),
    "envs/toolchains.py:_scan_interpreters": (
        2,
        "bare names searched with WindowsApps removed; each hit is alias-checked and probed by absolute path",
    ),
    "envs/toolchains.py:Provisioned.tools": (
        1,
        "maps a toolchain kind to the executable it covers; never executed",
    ),
    "repo/stack.py:REQUIRED_TOOLS": (
        1,
        "tool names checked with platform_compat.tool_present (real detection); never executed",
    ),
    "tools/code_intel.py:probe_tools": (
        1,
        "names passed to environment._probe, which runs the detected interpreter path",
    ),
    "tools/env_tools.py:KINDS": (1, "enumeration of toolchain kinds in a tool schema; never executed"),
    "tools/env_tools.py:ECOSYSTEMS": (1, "enumeration of ecosystems in a tool schema; never executed"),
}


def _sys_aliases(tree: ast.AST) -> set[str]:
    names = {"sys"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.asname or a.name for a in node.names if a.name == "sys")
    return names


def _is_sys_executable(node: ast.AST, aliases: set[str]) -> bool:
    if not (isinstance(node, ast.Attribute) and node.attr == "executable"):
        return False
    value = node.value
    return (isinstance(value, ast.Name) and value.id in aliases) or (
        isinstance(value, ast.Attribute) and value.attr == "sys"  # os.sys.executable
    )


def _is_getattr_executable(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "executable"
    )


def _is_sys_import(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.ImportFrom)
        and node.module == "sys"
        and any(a.name in {"executable", "*"} for a in node.names)
    )


def _docstring_ids(tree: ast.AST) -> set[int]:
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
    """Every node with its owner: the qualified def it sits in, else the module-level name, else ``<module>``."""

    def walk(node: ast.AST, owner: str):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            owner = node.name if owner == MODULE else f"{owner}.{node.name}"
        yield node, owner
        for child in ast.iter_child_nodes(node):
            yield from walk(child, owner)

    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
            top = stmt.targets[0].id
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            top = stmt.target.id
        else:
            top = MODULE
        yield from walk(stmt, top)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _sys_executable_uses(path: Path) -> list[tuple[str, int]]:
    """(``file:owner``, line) for every sys.executable use in ``path``."""
    rel = path.relative_to(SRC).as_posix()
    tree = _parse(path)
    aliases, docstrings = _sys_aliases(tree), _docstring_ids(tree)
    uses = []
    for node, owner in _owned_nodes(tree):
        line = getattr(node, "lineno", 0)
        template = (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and "sys.executable" in node.value  # template text that embeds the executable (e.g. RERUN)
        )
        if (
            template
            or _is_sys_executable(node, aliases)
            or _is_getattr_executable(node)
            or _is_sys_import(node)
        ):
            uses.append((f"{rel}:{owner}", line))
    return uses


def _fstring_starts_with_python(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.JoinedStr)
        and bool(node.values)
        and isinstance(node.values[0], ast.Constant)
        and str(node.values[0].value).lower().startswith("python")
    )


def _is_bare_name(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(BARE_PYTHON.match(node.value))
    )


def _command_starts_with_bare_python(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        words = node.value.strip().split(maxsplit=1)
        return bool(words) and bool(BARE_PYTHON.match(words[0]))
    return _fstring_starts_with_python(node)


def _bare_name_uses(path: Path) -> list[tuple[str, int]]:
    rel = path.relative_to(SRC).as_posix()
    uses = []
    for node, owner in _owned_nodes(_parse(path)):
        line = getattr(node, "lineno", 0)
        hit = False
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            hit = _is_bare_name(node.elts[0]) or _fstring_starts_with_python(node.elts[0])
        elif isinstance(node, ast.ListComp):
            hit = _fstring_starts_with_python(node.elt) or _is_bare_name(node.elt)
        elif isinstance(node, ast.Assign):
            hit = (
                isinstance(node.value, ast.Constant)
                and _is_bare_name(node.value)
                and any(
                    isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == 0
                    for t in node.targets
                )
            )
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            hit = (
                name in COMMAND_RUNNERS and bool(node.args) and _command_starts_with_bare_python(node.args[0])
            )
        if hit:
            uses.append((f"{rel}:{owner}", line))
    return uses


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _audit(found: list[tuple[str, int]], allowed: dict[str, tuple[int, str]]) -> list[str]:
    """Problems: owners with uses that are not listed, listed owners with a different count, stale entries."""
    counts = Counter(key for key, _ in found)
    problems = []
    for key in sorted(set(counts) | set(allowed)):
        expected = allowed.get(key, (0, ""))[0]
        actual = counts.get(key, 0)
        if actual != expected:
            lines = sorted(line for k, line in found if k == key)
            problems.append(f"{key}: {actual} use(s) (lines {lines}), allow-list expects {expected}")
    return problems


def test_sys_executable_is_used_only_where_reviewed():
    found = [use for path in _sources() for use in _sys_executable_uses(path)]
    problems = _audit(found, ALLOWED_SYS_EXECUTABLE)
    assert not problems, (
        "sys.executable outside the reviewed allow-list (use paths.python_argv() or a detected Python):\n"
        + "\n".join(problems)
    )


def test_no_bare_python_names_as_argv_zero():
    found = [use for path in _sources() for use in _bare_name_uses(path)]
    problems = _audit(found, ALLOWED_BARE_NAMES)
    assert not problems, (
        "bare Python name outside the reviewed list (it may run the Windows Store alias):\n"
        + "\n".join(problems)
    )


def test_allow_lists_have_a_count_and_a_reason_for_every_entry():
    for allowed in (ALLOWED_SYS_EXECUTABLE, ALLOWED_BARE_NAMES):
        for key, (count, reason) in allowed.items():
            file, _, owner = key.partition(":")
            assert file.endswith(".py") and owner, key
            assert isinstance(count, int) and count >= 1, key
            assert isinstance(reason, str) and 20 <= len(reason) <= 200 and "\n" not in reason, key


def test_detectors_flag_every_forbidden_form(tmp_path, monkeypatch):
    """The guard must fire on each form it claims to enforce, so a passing run means something."""
    monkeypatch.setattr(sys.modules[__name__], "SRC", tmp_path)
    module = tmp_path / "bad.py"
    module.write_text(
        '"""Docstring may mention sys.executable."""\n'
        "import os\n"
        "import subprocess\n"
        "import sys as s\n"
        "from sys import *\n"
        "class Runner:\n"
        "    def go(self):\n"
        "        subprocess.run([s.executable, '-c', 'pass'])\n"
        "        return getattr(os.sys, 'executable')\n"
        "def also():\n"
        "    return os.sys.executable\n"
        "CMD = ['python3', '--version']\n"
        "LITERAL = ['py', 'x']\n"
        "NAMES = [f'python3.{m}' for m in range(8, 9)]\n"
        "def other():\n"
        "    argv = []\n"
        "    argv[0] = 'pythonw'\n"
        "    subprocess.run('python -m pytest')\n",
        encoding="utf-8",
    )
    assert Counter(key for key, _ in _sys_executable_uses(module)) == Counter(
        {"bad.py:<module>": 1, "bad.py:Runner.go": 2, "bad.py:also": 1}
    )
    assert Counter(key for key, _ in _bare_name_uses(module)) == Counter(
        {"bad.py:CMD": 1, "bad.py:LITERAL": 1, "bad.py:NAMES": 1, "bad.py:other": 2}
    )


def test_audit_reports_an_extra_use_in_a_listed_owner():
    allowed = {"x.py:f": (1, "one reviewed use")}
    assert _audit([("x.py:f", 3)], allowed) == []
    assert _audit([("x.py:f", 3), ("x.py:f", 9)], allowed) == [
        "x.py:f: 2 use(s) (lines [3, 9]), allow-list expects 1"
    ]
    assert _audit([], allowed) == ["x.py:f: 0 use(s) (lines []), allow-list expects 1"]

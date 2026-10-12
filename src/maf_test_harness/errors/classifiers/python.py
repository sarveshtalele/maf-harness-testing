"""Python: pip/venv failures and pytest execution failures."""

from __future__ import annotations

from ._compile import compile_rules

RULES = compile_rules(
    [
        (
            "ENV-PY-001",
            r"Microsoft Visual C\+\+ 14\.0|error: command '(?:gcc|cc|cl\.exe|c\+\+)' failed|unable to execute "
            r"'(?:gcc|cc)'|gcc: (?:command )?not found|Failed building wheel for|Failed to build installable wheels|"
            r"fatal error: Python\.h: No such file|Could not build wheels for",
        ),
        ("ENV-PY-003", r"requires a different Python|Requires-Python|python_requires"),
        (
            "ENV-PY-002",
            r"No matching distribution found for|Could not find a version that satisfies the requirement",
        ),
        ("ENV-PY-004", r"externally-managed-environment"),
        # the frozen desktop app found no real Python to build the venv (runners.py: python-missing)
        ("ENV-PY-011", r"Python [^\n]* is required to run Python tests"),
        (
            "ENV-PY-005",
            r"ResolutionImpossible|conflicting dependencies|Cannot install .* because these package versions",
        ),
        (
            "ENV-PY-006",
            r"SRE module mismatch|Fatal Python error: init_fs_encoding|No module named 'encodings'",
        ),
        # a test client / helper module (not application code) names the package it cannot import
        (
            "ENV-PY-010",
            r"^[^\n]*test[^\n]*requires the (?P<package>[A-Za-z0-9][\w.-]*) package to be installed",
        ),
        (
            "ENV-PY-010",
            r"site-packages[^\n]*(?:testclient|test_client)[^\n]*\n(?:[^\n]*\n){0,40}?[^\n]*"
            r"No module named '(?P<package>[A-Za-z0-9_]+)",
        ),
        ("ENV-PY-010", r"test[_.]?client[\s\S]{0,600}?\$ pip install (?P<package>[A-Za-z0-9][\w.-]*)"),
        ("ENV-PY-008", r"\[WinError 2\] The system cannot find the file specified"),
        (
            "ENV-PY-009",
            r"has requirement .+, but you have|which is incompatible|"
            r"got an unexpected keyword argument 'on_(?:startup|shutdown)'",
        ),
    ]
)

EXEC_RULES = compile_rules(
    [
        (
            "EXEC-101",
            r"(?:\w*Error: )?No module named '(?P<module>[\w.]+)'|ImportError: cannot import name|ModuleNotFoundError",
        ),
        ("EXEC-101", r"Could not import (?P<module>[\w-]+) python package"),
        ("EXEC-104", r"\bSyntaxError\b|\bIndentationError\b|\bTabError\b"),
        ("EXEC-105", r"fixture '[^']+' not found|ScopeMismatch"),
        ("EXEC-107", r"reading from stdin|EOF when reading a line"),
        ("EXEC-102", r"\bNameError\b|\bAttributeError\b|TypeError: .*(?:argument|positional)"),
        ("EXEC-106", r"collected 0 items|no tests ran|no tests collected|Ran 0 tests"),
        ("EXEC-103", r"\bAssertionError\b|^E\s+assert |^assert "),
    ]
)

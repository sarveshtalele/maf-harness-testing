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
        (
            "ENV-PY-005",
            r"ResolutionImpossible|conflicting dependencies|Cannot install .* because these package versions",
        ),
        (
            "ENV-PY-006",
            r"SRE module mismatch|Fatal Python error: init_fs_encoding|No module named 'encodings'",
        ),
        ("ENV-PY-008", r"\[WinError 2\] The system cannot find the file specified"),
    ]
)

EXEC_RULES = compile_rules(
    [
        (
            "EXEC-101",
            r"(?:\w*Error: )?No module named '(?P<module>[\w.]+)'|ImportError: cannot import name|ModuleNotFoundError",
        ),
        ("EXEC-104", r"\bSyntaxError\b|\bIndentationError\b|\bTabError\b"),
        ("EXEC-105", r"fixture '[^']+' not found|ScopeMismatch"),
        ("EXEC-107", r"reading from stdin|EOF when reading a line"),
        ("EXEC-102", r"\bNameError\b|\bAttributeError\b|TypeError: .*(?:argument|positional)"),
        ("EXEC-106", r"collected 0 items|no tests ran|no tests collected|Ran 0 tests"),
        ("EXEC-103", r"\bAssertionError\b|^E\s+assert |^assert "),
    ]
)

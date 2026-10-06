"""Deterministic, regex-based testability analysis (no model calls, bounded reads).

Flags classes whose constructors acquire collaborators through statics/singletons (mocks are never used), UI-toolkit
classes (need toolkit init) and locals created inside methods (not injectable). Also retargets design cases whose
expected arithmetic lives in a same-class callee rather than in the targeted method.
"""

from __future__ import annotations

import re
from pathlib import Path

from .analyzer import _BLANK, _DEFINITION, _brace_body_end, _safe_read

MAX_FILES = 300
MAX_CLASSES_PER_PROMPT = 8

_CLASS = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|final|abstract|sealed|partial|export|default)\s+)*class\s+(\w+)"
)
_ACQUIRE = {
    ".java": r"\b\w+\.getInstance\s*\(|\b[A-Z]\w*\.get\w*(?:Connection|Instance)\w*\s*\(|DriverManager\.getConnection|new\s+(?:Socket|FileInputStream|FileReader)\s*\(|Class\.forName\s*\(",
    ".cs": r"\b\w+\.(?:GetInstance|Instance)\b|new\s+(?:SqlConnection|HttpClient|TcpClient|FileStream)\s*\(",
    ".py": r"\brequests\.|sqlite3\.connect|\bpsycopg\w*|\bopen\s*\(",
    ".ts": r"new\s+PrismaClient\s*\(|\bfetch\s*\(|\b\w+\.getInstance\s*\(",
}
_ACQUIRE[".tsx"] = _ACQUIRE[".js"] = _ACQUIRE[".jsx"] = _ACQUIRE[".ts"]
_UI_TYPES = (
    r"TableColumn|TableView|Button|Label|TextField|TextArea|ComboBox|ChoiceBox|ListView|VBox|HBox|GridPane"
    r"|BorderPane|Scene|Stage|JButton|JLabel|JTextField|JTable|JPanel|JFrame"
)
_LOCAL_UI = re.compile(rf"^\s*(?:final\s+)?(?:{_UI_TYPES})\b(?:<[^>]*>)?\s+\w+\s*=\s*new\s+", re.M)
_ARITH = re.compile(r"\w\s*[*/]\s*[\w(]")
# Arithmetic only: "/" in paths/URLs and "*" in globs are not cues (they caused false retargets on CLI/API cases).
_ARITH_CUE = re.compile(
    r"\d\s*[*x×/]\s*\d|multipl|calculat|product of|\btimes\b|\btotal\b|\bamount\b|\bsum\b", re.I
)


def _mockito_static_ok(root: Path) -> bool:
    for name in ("pom.xml", "build.gradle", "build.gradle.kts"):
        for path in [root / name, *root.glob(f"*/{name}")]:
            text = _safe_read(path)
            if "mockito-inline" in text or re.search(
                r"mockito-core(?:</artifactId>\s*<version>|:)\s*[\"']?(?:[5-9]|\d{2})\.", text
            ):
                return True
    return False


def _classes(rel: str, text: str) -> list[tuple[str, list[str]]]:
    """(class name, class body lines) per class declaration; one pseudo-class for JSX component files."""
    lines = text.splitlines()
    found = []
    for i, line in enumerate(lines):
        m = _CLASS.match(line)
        if m:
            found.append((m.group(1), lines[i : _brace_body_end(lines, i)]))
    if not found and rel.endswith((".tsx", ".jsx")) and re.search(r"return\s*\(?\s*<[A-Za-z]", text):
        found.append((Path(rel).stem, lines))
    return found


def _methods(body: list[str]) -> dict[str, str]:
    out = {}
    for i, line in enumerate(body):
        m = _DEFINITION.match(line)
        name = next((g for g in m.groups() if g), None) if m and i else None
        if name and name not in out:
            out[name] = _BLANK.sub(" ", "\n".join(body[i : _brace_body_end(body, i)]))
    return out


def _python_ctor(body: list[str]) -> str:
    for i, line in enumerate(body):
        if re.match(r"\s+def\s+__init__\b", line):
            indent = len(line) - len(line.lstrip())
            end = next(
                (
                    j
                    for j in range(i + 1, len(body))
                    if body[j].strip() and len(body[j]) - len(body[j].lstrip()) <= indent
                ),
                len(body),
            )
            return "\n".join(body[i:end])
    return ""


def _analyze_class(rel: str, name: str, body: list[str], text: str, mockito_static: bool, root: Path) -> dict:
    suffix, reasons, strategies = Path(rel).suffix, [], []
    methods = _methods(body)
    ctor = _python_ctor(body) if suffix == ".py" else methods.get(name) or methods.get("constructor", "")
    match = re.search(_ACQUIRE.get(suffix, "(?!)"), ctor)
    if match:
        reasons.append(
            f"constructor acquires collaborator statically ({match.group().strip()}); injected mocks are never used"
        )
        strategies += ["integration_test", "test_pure_methods", "testability_gap"]
        if suffix == ".java" and mockito_static:
            strategies.append("mockStatic (mockito-inline/mockito>=5 declared)")
    ui = False
    if "import javafx." in text:
        ui = True
        reasons.append("JavaFX controls need the toolkit initialised and the FX thread")
        strategies.append(
            "initialise JavaFX toolkit once (Platform.startup) and run UI code on the FX thread"
        )
    if "javax.swing" in text:
        ui = True
        reasons.append("Swing components need EDT/headless handling")
        strategies.append("construct on the EDT (SwingUtilities.invokeAndWait) and guard headless")
    if "System.Windows" in text:
        ui = True
        reasons.append("WinForms/WPF controls need an STA thread")
        strategies.append("run on an STA thread")
    if suffix in (".tsx", ".jsx"):
        ui = True
        reasons.append("React component needs a DOM renderer")
        if "@testing-library/react" in _safe_read(root / "package.json"):
            strategies.append("use @testing-library/react")
    if ui and suffix in (".java", ".cs"):
        for method, mbody in methods.items():
            if _LOCAL_UI.search(mbody):
                reasons.append(
                    f"objects created locally in {method} are not injectable; assert on observable container state"
                )
    if not reasons:
        strategies = ["unit_test", "mock_collaborators"]
    level = "low" if match else "medium" if reasons else "high"
    return {"level": level, "reasons": reasons, "allowed_strategies": strategies}


def analyze_testability(root: Path, source_files: list[str]) -> dict:
    root = Path(root).resolve()
    mockito_static, classes = _mockito_static_ok(root), {}
    for rel in sorted(set(source_files))[:MAX_FILES]:
        if Path(rel).suffix not in _ACQUIRE or any(p in {"test", "tests"} for p in Path(rel).parts):
            continue
        text = _safe_read(root / rel)
        for name, body in _classes(rel, text) if text else []:
            classes[f"{rel}::{name}"] = _analyze_class(rel, name, body, text, mockito_static, root)
    levels = [c["level"] for c in classes.values()]
    return {
        "classes": classes,
        "summary": {level: levels.count(level) for level in ("high", "medium", "low")}
        | {"classes": len(classes), "mockito_static": mockito_static},
    }


def testability_for_targets(
    testability: dict | None,
    targets: list[str],
    root: Path | None = None,
    source_files: list[str] | None = None,
) -> dict:
    """Compact entries for the classes named by the targets only (keeps prompts small).

    With ``root`` and ``source_files``, also lists targets a test cannot import (``non_public_targets``).
    """
    text = " ".join(targets)
    picked = {}
    for key, info in ((testability or {}).get("classes") or {}).items():
        path, name = key.split("::", 1)
        if path in text or re.search(rf"\b{re.escape(name)}\b", text):
            picked[key] = {
                "level": info["level"],
                "reasons": info["reasons"][:4],
                "allowed_strategies": info["allowed_strategies"],
            }
    picked = dict(list(picked.items())[:MAX_CLASSES_PER_PROMPT])
    if root is not None and source_files and (hidden := non_public_targets(root, source_files, targets)):
        picked["non_public_targets"] = hidden
    return picked


_SCRIPT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_JVM = {".java", ".kt", ".cs"}


def non_public_targets(root: Path, source_files: list[str], targets: list[str]) -> dict[str, str]:
    """Targets that a test cannot import: unexported JS/TS module symbols, Python `_private`, Java/C#/Kotlin `private`.

    A name counts only when every definition found for it is non-public. Bounded to MAX_FILES reads.
    """
    names = {n for t in targets for n in re.findall(r"[A-Za-z_]\w*", t.split("::")[-1])[-1:]}
    names = {n for n in names if len(n) > 2}
    if not names:
        return {}
    found: dict[str, list[tuple[str, bool]]] = {}
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, sorted(names))) + r")\b")
    for rel in sorted(set(source_files))[:MAX_FILES]:
        suffix = Path(rel).suffix
        if suffix not in _SCRIPT | _JVM | {".py"}:
            continue
        text = _safe_read(Path(root) / rel)
        if not text or not pattern.search(text):
            continue
        exported = set()
        if suffix in _SCRIPT:
            for group in re.findall(r"export\s*\{([^}]*)\}", text):
                exported |= {part.split(" as ")[0].strip() for part in group.split(",")}
        for line in text.splitlines():
            for name in names:
                if suffix in _SCRIPT and re.match(
                    rf"\s*(export\s+(?:default\s+)?)?(?:async\s+)?(?:function\*?|const|let|var|class)\s+{name}\b",
                    line,
                ):
                    public = bool(re.match(r"\s*export\b", line)) or name in exported
                    found.setdefault(name, []).append((rel, public))
                elif suffix == ".py" and re.match(rf"\s*(?:async\s+)?(?:def|class)\s+{name}\b", line):
                    found.setdefault(name, []).append(
                        (rel, not (name.startswith("_") and not name.endswith("__")))
                    )
                elif suffix in _JVM and re.search(rf"\b{name}\s*\(", line) and _DEFINITION.match(line):
                    found.setdefault(name, []).append((rel, not re.match(r"\s*private\b", line)))
    return {
        name: (
            f"{name} in {defs[0][0]} is not public (not exported/private): test it through the public "
            "function, component or class that uses it, or record a testability gap. Never copy its code into the test."
        )
        for name, defs in found.items()
        if defs and not any(public for _, public in defs)
    }


def retarget_cases(cases: list[dict], root: Path, source_files: list[str]) -> list[str]:
    """Move a case to the same-class callee that holds the arithmetic its expectation names; returns audit notes.

    Conservative: only when the targeted body has no arithmetic and exactly one same-file callee does.
    """
    notes = []
    for case in cases:
        target = str(case.get("target") or "")
        expected = f"{case.get('title', '')} {case.get('expected_result', '')}"
        if not _ARITH_CUE.search(expected):
            continue
        for rel in (f for f in source_files if f in target or Path(f).stem in target):
            lines = _safe_read(Path(root) / rel).splitlines()
            defs: dict[str, str] = {}
            for i, line in enumerate(lines):
                m = _DEFINITION.match(line)
                name = next((g for g in m.groups() if g), None) if m else None
                if name and name not in defs and not _CLASS.match(line):
                    defs[name] = _BLANK.sub(" ", "\n".join(lines[i : _brace_body_end(lines, i)]))
            method = next((n for n in reversed(re.findall(r"\w+", target)) if n in defs), None)
            if not method or _ARITH.search(defs[method]):
                continue
            callees = [
                n
                for n, b in defs.items()
                if n != method and re.search(rf"\b{n}\s*\(", defs[method]) and _ARITH.search(b)
            ]
            if len(callees) == 1:
                case["target"] = re.sub(rf"\b{method}\b", callees[0], target)
                notes.append(
                    f"retargeted {case.get('id', case.get('title'))} from {method} to {callees[0]}: "
                    "behaviour implemented in callee"
                )
                break
    return notes

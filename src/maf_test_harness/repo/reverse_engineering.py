"""Bounded deterministic source inventory adapted to the host's approved clone boundary.

Python declarations use AST. Other language declarations are labeled heuristic candidates,
not proof of behavior. No imports, installer commands, repository scripts or models run.
"""

import ast
import hashlib
import re
from pathlib import Path

SKIP = {".git", "node_modules", "vendor", ".harness-venv", ".venv", "__pycache__", "dist", "build", "target"}
MAX_FILES = 300
MAX_FILE_BYTES = 256_000
MAX_TOTAL_BYTES = 4_000_000


def reverse_engineer(root: Path, source_files: list[str]) -> dict:
    root = root.resolve()
    facts, gaps, files = [], [], []
    total = 0
    parsed = 0
    candidates = sorted(set(source_files))
    for rel in candidates[:MAX_FILES]:
        path = root / rel
        if Path(rel).is_absolute() or ".." in Path(rel).parts or set(Path(rel).parts) & SKIP:
            gaps.append(f"Excluded unsafe/generated path: {rel}")
            continue
        if any(part in {"test", "tests"} for part in Path(rel).parts) or path.name.startswith("test_"):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            gaps.append(f"Source unavailable or outside clone: {rel}")
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                gaps.append(f"File size limit reached: {rel}")
                continue
            with path.open("rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                gaps.append(f"File size limit reached while reading: {rel}")
                continue
        except OSError as exc:
            gaps.append(f"Source read failed: {rel} ({type(exc).__name__})")
            continue
        if total + len(data) > MAX_TOTAL_BYTES:
            gaps.append("Total source byte limit reached; remaining files were not analyzed.")
            break
        total += len(data)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            gaps.append(f"Non-UTF-8 source not parsed: {rel}")
            continue
        files.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        declarations = []
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError as exc:
                gaps.append(f"Python syntax not parsed: {rel}:{exc.lineno}")
                continue
            parsed += 1
            for item in tree.body:
                members = (
                    [
                        (item.name + "." + n.name, n)
                        for n in item.body
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ]
                    if isinstance(item, ast.ClassDef)
                    else [(getattr(item, "name", ""), item)]
                )
                for name, node in members:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(
                        "_"
                    ):
                        declarations.append(
                            (name, node.lineno, "ast", ast.get_source_segment(text, node) or "")
                        )
        elif path.suffix in {".js", ".jsx", ".ts", ".tsx", ".java", ".cs", ".go", ".kt"}:
            parsed += 1
            # Candidates are not promoted to implemented business behavior or security claims.
            pattern = r"(?m)^[ \t]*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(|^[ \t]*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\([^\n]*\)\s*=>|^[ \t]*public\s+(?:static\s+)?[\w<>\[\]?]+\s+(\w+)\s*\(|^[ \t]*fun\s+(\w+)\s*\(|^[ \t]*func\s+(\w+)\s*\("
            for match in re.finditer(pattern, text):
                declarations.append(
                    (
                        next(g for g in match.groups() if g),
                        text.count("\n", 0, match.start()) + 1,
                        "heuristic",
                        match.group(0).strip(),
                    )
                )
        else:
            gaps.append(f"Unsupported declaration parser: {rel}")
        for name, line, confidence, evidence in declarations:
            facts.append(
                {
                    "kind": "callable_declaration",
                    "symbol": name,
                    "source_file": rel,
                    "line": line,
                    "confidence": confidence,
                    "evidence": evidence[:850],
                }
            )
    if len(candidates) > MAX_FILES:
        gaps.append(f"Source file limit reached: {MAX_FILES}/{len(candidates)} candidates considered.")
    gaps.append(
        "Static declarations do not prove full business semantics, runtime behavior, authorization or compliance. Dynamic routes and unsupported syntax require review."
    )
    return {
        "engine": "host-static-v1",
        "model_calls": 0,
        "files": files,
        "facts": facts,
        "gaps": gaps,
        "coverage": {
            "candidate_files": len(candidates),
            "read_files": len(files),
            "parsed_files": parsed,
            "bytes": total,
        },
    }

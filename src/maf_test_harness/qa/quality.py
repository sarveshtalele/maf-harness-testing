"""Conservative source witnesses; these checks never claim measured behavior coverage."""

import json
import re
from pathlib import Path

from ..skills.contracts import _strip_strings_and_comments


def _without_comments(text):
    return re.sub(
        r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/',
        lambda m: " " * len(m[0]) if m[0].startswith(("//", "/*")) else m[0],
        text,
        flags=re.S,
    )


def _java_test_bodies(text):
    # ponytail: exact method-body duplicates only; semantic equivalence needs a Java AST.
    clean = _without_comments(text)
    masked = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', lambda m: " " * len(m[0]), clean)
    bodies = set()
    for method in re.finditer(
        r"@Test\b(?:\([^)]*\))?\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:public\s+)?void\s+\w+\s*\([^)]*\)[^{]*\{",
        masked,
    ):
        depth = 1
        for end in range(method.end(), len(masked)):
            depth += (masked[end] == "{") - (masked[end] == "}")
            if depth == 0:
                tokens = re.findall(
                    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\w+|[^\s]', clean[method.end() : end]
                )
                bodies.add(tuple(tokens))
                break
    return bodies


def _arguments(text, start):
    """Split a Java call's arguments without treating nested calls/strings as separators."""
    args, current, depth, quote, escaped = [], [], 0, "", False
    for char in text[start:]:
        if quote:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return args + ["".join(current).strip()]
            depth -= 1
        elif char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    return []


def _check_positive_literals(code, source, typename, path):
    constraints, annotations = {}, []
    for line in source.splitlines():
        clean = line.strip()
        if clean.startswith("@"):
            annotations.append(clean)
            continue
        field = re.search(r"\bprivate\s+String\s+(\w+)\s*;", clean)
        size = re.search(r"@Size\s*\((.*?)\)", " ".join(annotations))
        if field and size:
            bounds = dict(re.findall(r"\b(min|max)\s*=\s*(\d+)", size.group(1)))
            constraints[field.group(1)] = (int(bounds.get("min", 0)), int(bounds.get("max", 2147483647)))
        if clean and not clean.startswith("//"):
            annotations = []
    assignments = []
    for name in constraints:
        assignments.extend(
            (name, m.group(1))
            for m in re.finditer(
                r"\.set" + re.escape(name[:1].upper() + name[1:]) + r'\s*\(\s*("(?:[^"\\]|\\.)*")\s*\)', code
            )
        )
    for signature in re.finditer(r"\bpublic\s+" + re.escape(typename) + r"\s*\(([^)]*)\)", source):
        parameters = [p.strip().split()[-1] for p in signature.group(1).split(",") if p.strip()]
        for call in re.finditer(r"\bnew\s+" + re.escape(typename) + r"\s*\(", code):
            arguments = _arguments(code, call.end())
            if len(arguments) == len(parameters):
                assignments.extend(zip(parameters, arguments, strict=True))
    for name, literal in assignments:
        if name not in constraints:
            continue
        try:
            value = json.loads(literal)
        except (ValueError, TypeError):
            continue
        if (
            isinstance(value, str)
            and not constraints[name][0] <= len(value.encode("utf-16-le")) // 2 <= constraints[name][1]
        ):
            raise ValueError(
                f"{path}: positive fixture {typename}.{name} violates source @Size{constraints[name]}. Use valid fixtures; invalid values require an explicit negative/validation case."
            )


def require_complete_intake(state):
    requirements = state["artifacts"].get("requirements", {})
    facts = state["artifacts"]["repo_analysis"].get("reverse_engineering", {}).get("facts", [])
    if requirements.get("analysis_mode") != "deterministic" or not facts:
        return
    represented = {
        (r.get("source_files", [""])[0], r.get("source_line"), r.get("source_symbol"))
        for r in requirements["requirement_model"]["requirements"]
    }
    missing = {(f["source_file"], f["line"], f["symbol"]) for f in facts} - represented
    if missing:
        raise ValueError(
            f"Incomplete deterministic intake: {len(facts) - len(missing)}/{len(facts)} declarations represented. Rerun Requirements and Test design before generation; a truncated inventory cannot qualify the repository."
        )


def existing_test_evidence(root, paths, requirements, limit=18000):
    names = {Path(path).stem for requirement in requirements for path in requirement.get("source_files", [])}
    snippets = []
    for path in paths:
        target = (root / path).resolve()
        if not target.is_relative_to(root.resolve()) or target.is_symlink() or not target.is_file():
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        if any(re.search(r"\b" + re.escape(name) + r"\b", text) for name in names):
            snippet = f"--- EXISTING TEST: {path} ---\n{text[:6000]}"
            snippets.append(snippet[:limit])
            limit -= len(snippets[-1])
            if limit <= 0:
                break
    return "\n".join(snippets)


def validate_java_traceability(payload, cases, requirements, root):
    """Require calls on the source type for unit-method claims, not names/comments/imports."""
    by_case = {str(case["id"]): case for case in cases}
    by_requirement = {r["id"]: r for r in requirements.get("requirement_model", {}).get("requirements", [])}
    witnesses = []
    existing_bodies = {}
    for path in sorted((root / "src/test/java").rglob("*.java")):
        if not path.is_symlink() and path.resolve().is_relative_to(root.resolve()):
            for body in _java_test_bodies(path.read_text(encoding="utf-8", errors="replace")):
                existing_bodies[body] = path.relative_to(root).as_posix()
    for file in payload["files"]:
        if not file["path"].endswith(".java"):
            continue
        code = _strip_strings_and_comments(file["content"])
        if re.search(r"\bassertTrue\s*\(\s*true\s*\)|\bassertFalse\s*\(\s*false\s*\)", code):
            raise ValueError(f"{file['path']}: constant assertion is not an application behavior check.")
        duplicate = next(
            (existing_bodies[b] for b in _java_test_bodies(file["content"]) if b in existing_bodies), None
        )
        if duplicate:
            raise ValueError(
                f"{file['path']}: test body duplicates {duplicate}. Add a distinct source-backed behavior check instead of repeating existing coverage."
            )
        case = by_case[str(file["case_ids"][0])]
        if case.get("level") != "unit":
            continue
        for link in case.get("traceability_links", []):
            requirement = by_requirement.get(link["id"])
            if not requirement or not requirement.get("source_symbol"):
                continue
            for path in requirement["source_files"]:
                if not path.endswith(".java"):
                    continue
                typename = Path(path).stem
                symbol = requirement["source_symbol"]
                if case.get("type") == "positive":
                    _check_positive_literals(
                        _without_comments(file["content"]),
                        (root / path).read_text(encoding="utf-8"),
                        typename,
                        file["path"],
                    )
                receivers = set(re.findall(r"\b" + re.escape(typename) + r"\s+(\w+)\s*(?:=|;)", code)) | {
                    typename
                }
                invoked = any(
                    re.search(r"\b" + re.escape(receiver) + r"\s*\.\s*" + re.escape(symbol) + r"\s*\(", code)
                    for receiver in receivers
                )
                if symbol in {"equals", "hashCode"} and re.search(
                    r"\bEqualsVerifier\s*\.\s*forClass\s*\(\s*" + re.escape(typename) + r"\s*\.\s*class\s*\)",
                    code,
                ):
                    invoked = True
                if not invoked:
                    raise ValueError(
                        f"{file['path']}: case {case['id']} claims {requirement['id']} ({typename}.{symbol}) but never invokes that method on its source type. Call and assert the actual method or correct the design's traceability; constructors and comments do not cover setters."
                    )
                witnesses.append(
                    {
                        "case_id": case["id"],
                        "requirement_id": requirement["id"],
                        "source_file": path,
                        "symbol": symbol,
                        "check": "static typed invocation; not runtime coverage",
                    }
                )
    return {
        "java_invocation_witnesses": witnesses,
        "scope": "Direct Java unit invocation witnesses only; behavior, line, branch and mutation coverage are not measured.",
    }

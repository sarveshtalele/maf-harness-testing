"""Deterministic expectation checks for generated Python tests (static only: nothing is executed or evaluated).

D1 `count_assumes_no_filter`: `len(<target call>) == N` where the test's own literal fixture has N items and the target
body filters (so the expectation assumes nothing was dropped). Cap 60.

D2 `expectation_contradicts_source`: an asserted attribute or key is assigned in the target by a pure expression over
one string input, and the test binds exactly one string literal to that input. A tiny AST interpreter evaluates the
expression with whitelisted operations only. A mismatch caps the case at 0. Anything the checks cannot bind or evaluate
is reported as `derivation_required` and caps nothing, so correct tests are not rejected by guesswork.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from .confidence import resolve_target

RULE_COUNT_NO_FILTER = "count_assumes_no_filter"
RULE_CONTRADICTS = "expectation_contradicts_source"
RULE_REQUIRED = "derivation_required"
_ASSERT_EQUAL = {"assertEqual", "assertEquals"}
_PURE_METHODS = {
    "count": lambda s, *a: s.count(*a),
    "split": lambda s, *a: s.split(*a),
    "splitlines": lambda s, *a: s.splitlines(*a),
    "strip": lambda s, *a: s.strip(*a),
}
_SOURCE_HEADER = re.compile(r"^--- SOURCE: .* ---$")
_LINE_PREFIX = re.compile(r"^\d+: ")


class _Unsupported(Exception):
    """The expression uses something the whitelisted interpreter does not evaluate."""


def strip_target_text(target_source: str) -> str:
    """Plain Python from extracted target_source (line-number prefixes and SOURCE headers removed)."""
    lines = []
    for line in target_source.splitlines():
        if _SOURCE_HEADER.match(line.strip()):
            continue
        lines.append(_LINE_PREFIX.sub("", line, count=1))
    return "\n".join(lines)


def _parse(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _target_function(tree: ast.Module | None, symbol: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    if tree is None or not symbol:
        return None
    found = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == symbol
    ]
    return found[0] if len(found) == 1 else None


def _invokes(node: ast.AST, symbol: str) -> bool:
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            func = call.func
            if (isinstance(func, ast.Attribute) and func.attr == symbol) or (
                isinstance(func, ast.Name) and func.id == symbol
            ):
                return True
    return False


def _len_comparisons(tree: ast.AST) -> list[tuple[ast.AST, int]]:
    """(`<expr>` in len(<expr>) == N, N) for every `len(...) == N` comparison and assertEqual(len(...), N)."""
    pairs: list[tuple[ast.AST, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            left, right = node.left, node.comparators[0]
            if (
                isinstance(left, ast.Call)
                and isinstance(left.func, ast.Name)
                and left.func.id == "len"
                and len(left.args) == 1
                and isinstance(right, ast.Constant)
                and type(right.value) is int
            ):
                pairs.append((left.args[0], right.value))
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in _ASSERT_EQUAL and len(node.args) == 2:
                first, second = node.args
                if (
                    isinstance(first, ast.Call)
                    and isinstance(first.func, ast.Name)
                    and first.func.id == "len"
                    and len(first.args) == 1
                    and isinstance(second, ast.Constant)
                    and type(second.value) is int
                ):
                    pairs.append((first.args[0], second.value))
    return pairs


def _filters(function: ast.AST) -> bool:
    """A comprehension `if`, filter(...), a continue in a loop, or a test comparing against 0 or None."""
    for node in ast.walk(function):
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp) and any(
            gen.ifs for gen in node.generators
        ):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "filter":
            return True
        if isinstance(node, ast.For | ast.While) and any(isinstance(n, ast.Continue) for n in ast.walk(node)):
            return True
        if isinstance(node, ast.If):
            for cmp_ in ast.walk(node.test):
                if isinstance(cmp_, ast.Compare) and any(
                    isinstance(c, ast.Constant)
                    and (c.value is None or (type(c.value) is int and c.value == 0))
                    for c in cmp_.comparators
                ):
                    return True
    return False


def count_assumes_no_filter(test_content: str, target_source: str, target: str) -> dict[str, Any] | None:
    """D1: a cap-60 finding when `len(<target call>) == N` matches an N-item literal fixture and the target filters."""
    resolved = resolve_target(target)
    if resolved is None or resolved.kind == "route":
        return None
    test_tree = _parse(test_content)
    function = _target_function(_parse(strip_target_text(target_source)), resolved.symbol)
    if test_tree is None or function is None or not _filters(function):
        return None
    literal_sizes = {len(node.elts) for node in ast.walk(test_tree) if isinstance(node, ast.List | ast.Tuple)}
    for subject, count in _len_comparisons(test_tree):
        if count in literal_sizes and _invokes(subject, resolved.symbol):
            return {
                "rule": RULE_COUNT_NO_FILTER,
                "cap": 60,
                "reason": (
                    f"The expectation len(...) == {count} equals the size of a literal fixture, but "
                    f"`{resolved.symbol}` filters its candidates; name which items survive the filter."
                ),
            }
    return None


# --- D2 -------------------------------------------------------------------------------------------------------


def _asserted_keys(test_tree: ast.AST) -> list[tuple[str, int]]:
    """(attribute or key, expected int) for `<x>["key"] == N`, `<x>.attr == N` and assertEqual(<x>["key"], N)."""
    pairs: list[tuple[str, int]] = []
    candidates: list[tuple[ast.AST, ast.AST]] = []
    for node in ast.walk(test_tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            candidates.append((node.left, node.comparators[0]))
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in _ASSERT_EQUAL and len(node.args) == 2:
                candidates.append((node.args[0], node.args[1]))
    for left, right in candidates:
        if not (isinstance(right, ast.Constant) and type(right.value) is int):
            continue
        if isinstance(left, ast.Subscript) and isinstance(left.slice, ast.Constant):
            if isinstance(left.slice.value, str):
                pairs.append((left.slice.value, right.value))
        elif isinstance(left, ast.Attribute):
            pairs.append((left.attr, right.value))
    return pairs


def _assigned_values(function: ast.AST, key: str) -> list[ast.expr]:
    values: list[ast.expr] = []
    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            values += [v for k, v in zip(node.keys, node.values, strict=True) if _is_key(k, key)]
        if isinstance(node, ast.Assign):
            values += [node.value for t in node.targets if _is_store(t, key)]
        if isinstance(node, ast.Call):
            values += [kw.value for kw in node.keywords if kw.arg == key]
    return values


def _is_key(node: ast.AST | None, key: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == key


def _is_store(target: ast.AST, key: str) -> bool:
    if isinstance(target, ast.Subscript):
        return _is_key(target.slice, key)
    return isinstance(target, ast.Attribute) and target.attr == key


def _evaluate(node: ast.AST, names: dict[str, Any]) -> Any:
    """Pure, whitelisted interpreter: literals, one named input, + - * //, len(), and str.count/split/splitlines/strip."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and type(_evaluate(node.operand, names)) is int
    ):
        return -_evaluate(node.operand, names)
    if isinstance(node, ast.BinOp):
        left, right = _evaluate(node.left, names), _evaluate(node.right, names)
        kinds = (type(left), type(right))
        if isinstance(node.op, ast.Add) and kinds in ((int, int), (str, str)):
            return left + right
        if isinstance(node.op, ast.Sub) and kinds == (int, int):
            return left - right
        if isinstance(node.op, ast.Mult) and kinds in ((int, int), (str, int), (int, str)):
            return left * right
        if isinstance(node.op, ast.FloorDiv) and kinds == (int, int) and right != 0:
            return left // right
        raise _Unsupported
    if isinstance(node, ast.Call) and not node.keywords:
        args = [_evaluate(a, names) for a in node.args]
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "len"
            and len(args) == 1
            and type(args[0]) is str
        ):
            return len(args[0])
        if isinstance(node.func, ast.Attribute) and node.func.attr in _PURE_METHODS:
            receiver = _evaluate(node.func.value, names)
            if type(receiver) is str and all(type(a) in (int, str) for a in args) and len(args) <= 2:
                return _PURE_METHODS[node.func.attr](receiver, *args)
    raise _Unsupported


def _string_input_bindings(test_tree: ast.AST, symbol: str) -> set[str]:
    """String literals the test passes in as file content (write_text), StringIO input, or a direct target argument."""
    bound: set[str] = set()
    for node in ast.walk(test_tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name in {"write_text", "StringIO"}:
            positional = node.args[:1]
        elif name == symbol:
            positional = node.args
        else:
            continue
        for arg in positional:
            if isinstance(arg, ast.Constant) and type(arg.value) is str:
                bound.add(arg.value)
    return bound


def _input_names(function: ast.AST) -> set[str]:
    """Variables that hold the string input: parameters, or values read from a file (read_text / read)."""
    names = {a.arg for a in function.args.args if a.arg not in ("self", "cls")}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr in {"read_text", "read"}:
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def expectation_contradicts_source(
    test_content: str, target_source: str, target: str
) -> dict[str, Any] | None:
    """D2: the first contradicted expectation, or None. Returns {"rule", "cap", "reason"} with cap 0 on contradiction.

    Ambiguous cases return {"rule": RULE_REQUIRED, "cap": None, ...} so the caller can record them without capping.
    """
    resolved = resolve_target(target)
    if resolved is None or resolved.kind == "route":
        return None
    test_tree = _parse(test_content)
    function = _target_function(_parse(strip_target_text(target_source)), resolved.symbol)
    if test_tree is None or function is None:
        return None
    pairs = _asserted_keys(test_tree)
    if not pairs:
        return None
    ambiguous = None
    bound = _string_input_bindings(test_tree, resolved.symbol)
    for key, expected in pairs:
        values = _assigned_values(function, key)
        if len(values) != 1:
            ambiguous = ambiguous or f"`{key}` is assigned {len(values)} times in `{resolved.symbol}`"
            continue
        expression = values[0]
        free = {n.id for n in ast.walk(expression) if isinstance(n, ast.Name)} - {"len"}
        if free and (len(free) > 1 or not free <= _input_names(function) or len(bound) != 1):
            ambiguous = ambiguous or f"`{key}` cannot be bound to one string input"
            continue
        names = {name: next(iter(bound)) for name in free}
        try:
            value = _evaluate(expression, names)
        except (_Unsupported, ZeroDivisionError, TypeError, ValueError):
            ambiguous = ambiguous or f"`{key}` uses an expression outside the pure whitelist"
            continue
        if type(value) is not int:
            ambiguous = ambiguous or f"`{key}` does not evaluate to an integer"
            continue
        if value != expected:
            return {
                "rule": RULE_CONTRADICTS,
                "cap": 0,
                "reason": (
                    f"The source computes `{key}` = {value} for the test input, but the test expects {expected}. "
                    "Re-derive the expectation from the implementation before asserting it."
                ),
            }
    if ambiguous:
        return {"rule": RULE_REQUIRED, "cap": None, "reason": ambiguous}
    return None


def deterministic_findings(test_content: str, target_source: str, target: str) -> list[dict[str, Any]]:
    """All D1/D2 findings for one Python test file; caps are those with a numeric cap, the rest are notes."""
    findings = []
    for check in (count_assumes_no_filter, expectation_contradicts_source):
        finding = check(test_content, target_source, target)
        if finding:
            findings.append(finding)
    return findings


# --- Blind derivation: masking for the reviewer ---------------------------------------------------------------

SLOT_PATTERN = re.compile("«(E\\d+)»")


def literal_value_text(value: Any) -> str:
    """The normalised form the reviewer's `value` is compared with (no quotes around strings)."""
    return value if isinstance(value, str) else str(value)


def _char_offset(line: str, byte_offset: int) -> int:
    return len(line.encode("utf-8")[:byte_offset].decode("utf-8", errors="ignore"))


def _assertion_literals(tree: ast.AST) -> list[ast.Constant]:
    """Right-hand literals of `==` comparisons and of assertEqual(…, literal) calls (Python)."""
    found: list[ast.Constant] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            right = node.comparators[0]
            if isinstance(right, ast.Constant) and type(right.value) in (int, str, float):
                found.append(right)
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in _ASSERT_EQUAL and len(node.args) == 2:
                right = node.args[1]
                if isinstance(right, ast.Constant) and type(right.value) in (int, str, float):
                    found.append(right)
    return sorted((n for n in found if n.lineno == n.end_lineno), key=lambda n: (n.lineno, n.col_offset))


def mask_expected_literals(files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Blind view of generated Python files: each assertion's right-hand literal becomes «E<n>».

    Returns (masked copies of the files, slot records). A slot record holds the real normalised value (never sent to
    the reviewer), its file index and the case IDs of that file. Files that do not parse are passed through unmasked.
    """
    masked: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    for index, item in enumerate(files):
        content = item.get("content", "")
        tree = _parse(content) if item.get("path", "").endswith(".py") else None
        if tree is None:
            masked.append(dict(item))
            continue
        lines = content.splitlines(keepends=True)
        spans = []
        for literal in _assertion_literals(tree):
            row = literal.lineno - 1
            number = len(slots) + 1
            slots.append(
                {
                    "slot": f"E{number}",
                    "file": index,
                    "case_ids": [str(c) for c in item.get("case_ids", [])],
                    "value": literal_value_text(literal.value),
                }
            )
            spans.append(
                (
                    row,
                    _char_offset(lines[row], literal.col_offset),
                    _char_offset(lines[row], literal.end_col_offset),
                    number,
                )
            )
        # Right to left within each line, so earlier character offsets stay valid.
        for row, first, last, number in sorted(spans, key=lambda span: (span[0], span[1]), reverse=True):
            lines[row] = lines[row][:first] + f"«E{number}»" + lines[row][last:]
        masked.append({**item, "content": "".join(lines)})
    return masked, slots

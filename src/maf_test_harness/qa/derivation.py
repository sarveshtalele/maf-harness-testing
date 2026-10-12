"""Deterministic expectation checks for generated Python tests. Static only: nothing is executed or evaluated.

Policy: a deterministic finding must never block a correct test on its own. A finding either proves the expectation
wrong (D2 with its full preconditions), or asks for evidence that the reviewer has to supply (D1, see
qa/confidence.py `settle_deterministic`). Anything the checks cannot bind or evaluate is `derivation_required`, which
caps nothing. Every check is total: a hostile or huge input returns no finding instead of raising.

D1 `count_assumes_no_filter`: `len(<target call>) == N`, where the list or tuple literal passed to the target call (or
to the constructor it is called on) has N items and the target body really filters (cap 60; lifted by evidence).

D2 `expectation_contradicts_source`: the asserted key of the target's direct return value is assigned once,
unconditionally, by a pure expression over the single string input, and the test binds one string literal to that
input. A whitelisted AST interpreter evaluates it. Cap 0 on disagreement (the host lowers it to 60 when the blind
derivation agrees with the literal).
"""

from __future__ import annotations

import ast
import re
from typing import Any

from .confidence import resolve_target

RULE_COUNT_NO_FILTER = "count_assumes_no_filter"
RULE_CONTRADICTS = "expectation_contradicts_source"
RULE_REQUIRED = "derivation_required"
MAX_SOURCE_CHARS = 200_000
MAX_STEPS = 2_000
MAX_DEPTH = 50
MAX_STR = 10_000
MAX_INT = 10**12
_ASSERT_EQUAL = {"assertEqual", "assertEquals"}
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")
_SOURCE_HEADER = re.compile(r"^--- SOURCE: .* ---$")
_LINE_PREFIX = re.compile(r"^\d+: ")
_CASE_MARKER = re.compile(r"CASE_ID:\s*([^\s|]+)")
SLOT_PATTERN = re.compile("«(E\\d+)»")
_PURE_METHODS = {
    "count": lambda s, *a: s.count(*a),
    "split": lambda s, *a: s.split(*a),
    "splitlines": lambda s, *a: s.splitlines(*a),
    "strip": lambda s, *a: s.strip(*a),
}


class _Unsupported(Exception):
    """The expression uses something the whitelisted interpreter does not evaluate, or exceeds a limit."""


def split_lines(text: str) -> list[str]:
    """Lines split exactly as the Python parser numbers them (\\r\\n, \\r, \\n only; not str.splitlines)."""
    return _LINE.findall(text or "")


def strip_target_text(target_source: str) -> str:
    """Plain Python from extracted target_source (line-number prefixes and SOURCE headers removed)."""
    out = []
    for line in split_lines(target_source):
        body = line.rstrip("\r\n")
        if _SOURCE_HEADER.match(body.strip()):
            continue
        out.append(_LINE_PREFIX.sub("", body, count=1))
    return "\n".join(out)


def _parse(text: str) -> ast.Module | None:
    """The module, or None for anything that does not parse or is too large (including RecursionError)."""
    if not isinstance(text, str) or len(text) > MAX_SOURCE_CHARS:
        return None
    try:
        return ast.parse(text)
    except (
        Exception
    ):  # SyntaxError, RecursionError, MemoryError, ValueError: the repository cannot crash the stage
        return None


def _target_function(tree: ast.Module | None, symbol: str):
    if tree is None or not symbol:
        return None
    found = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == symbol
    ]
    return found[0] if len(found) == 1 else None


def _invokes(node: ast.AST, symbol: str) -> ast.Call | None:
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            func = call.func
            if (isinstance(func, ast.Attribute) and func.attr == symbol) or (
                isinstance(func, ast.Name) and func.id == symbol
            ):
                return call
    return None


def _call_name(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def _single_bindings(tree: ast.AST) -> dict[str, ast.expr]:
    """Names bound exactly once in the module, by a plain single-target assignment (the value expression)."""
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            counts[node.id] = counts.get(node.id, 0) + 1
    values: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            values[node.targets[0].id] = node.value
    return {name: value for name, value in values.items() if counts.get(name) == 1}


def _len_comparisons(tree: ast.AST) -> list[tuple[ast.AST, int]]:
    """(subject, N) for `len(subject) == N` and assertEqual(len(subject), N)."""
    pairs: list[tuple[ast.AST, int]] = []

    def is_len(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "len"
            and len(node.args) == 1
        )

    def is_int(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and type(node.value) is int

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            left, right = node.left, node.comparators[0]
            if is_len(left) and is_int(right):
                pairs.append((left.args[0], right.value))
        if isinstance(node, ast.Call) and _call_name(node) in _ASSERT_EQUAL and len(node.args) == 2:
            first, second = node.args
            if is_len(first) and is_int(second):
                pairs.append((first.args[0], second.value))
    return pairs


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
    }


def _real_filters(function: ast.AST) -> list[dict[str, Any]]:
    """Filters that drop input items: comprehension `if`, filter(...), and a `continue` guarded by an `if` in a loop."""
    filters: list[dict[str, Any]] = []
    for node in ast.walk(function):
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
            for gen in node.generators:
                for cond in gen.ifs:
                    filters.append({"tokens": _names_in(cond), "text": ast.unparse(cond)})
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "filter":
            tokens = set().union(*(_names_in(a) for a in node.args)) if node.args else set()
            filters.append({"tokens": tokens, "text": ast.unparse(node)})
        elif isinstance(node, ast.For):
            for guard in ast.walk(node):
                if isinstance(guard, ast.If) and any(isinstance(n, ast.Continue) for n in ast.walk(guard)):
                    filters.append({"tokens": _names_in(guard.test), "text": ast.unparse(guard.test)})
    return filters


def _resolved_inputs(call: ast.Call, bindings: dict[str, ast.expr]) -> list[ast.expr]:
    """Argument expressions that flow into the target call: its own arguments, and those of the constructor it is
    called on (directly, or through one name bound once)."""
    args = [*call.args, *(kw.value for kw in call.keywords)]
    receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
    if isinstance(receiver, ast.Name):
        receiver = bindings.get(receiver.id)
    if isinstance(receiver, ast.Call):
        args += [*receiver.args, *(kw.value for kw in receiver.keywords)]
    resolved = []
    for arg in args:
        if isinstance(arg, ast.Name):
            arg = bindings.get(arg.id, arg)
        if isinstance(arg, ast.List | ast.Tuple):
            resolved.append(arg)
    return resolved


def count_assumes_no_filter(test_content: str, target_source: str, target: str) -> dict[str, Any] | None:
    """D1: `len(<target call>) == N` where a literal list or tuple flowing into that call has N items and the target
    filters. The finding carries the filter tokens so the host can lift it when the reviewer shows the filter kept
    everything."""
    resolved = resolve_target(target)
    if resolved is None or resolved.kind == "route":
        return None
    test_tree = _parse(test_content)
    function = _target_function(_parse(strip_target_text(target_source)), resolved.symbol)
    if test_tree is None or function is None:
        return None
    filters = _real_filters(function)
    if not filters:
        return None
    bindings = _single_bindings(test_tree)
    for subject, count in _len_comparisons(test_tree):
        call = _invokes(subject, resolved.symbol)
        if call is None:
            continue
        if count in {len(node.elts) for node in _resolved_inputs(call, bindings)}:
            return {
                "rule": RULE_COUNT_NO_FILTER,
                "cap": 60,
                "expected": count,
                "tokens": sorted({t for f in filters for t in f["tokens"]}),
                "filter_lines": [f["text"] for f in filters],
                "reason": (
                    f"The expectation len(...) == {count} equals the size of a literal fixture passed to "
                    f"`{resolved.symbol}`, and `{resolved.symbol}` filters its candidates. The reviewer must show which "
                    "items survive the filter."
                ),
            }
    return None


# --- D2 -------------------------------------------------------------------------------------------------------


def _binding_counts(function: ast.AST) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in ast.walk(function):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign | ast.For | ast.AsyncFor):
            targets = [node.target]
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            targets = [node.optional_vars]
        elif isinstance(node, ast.NamedExpr):
            targets = [node.target]
        for target in targets:
            for name in ast.walk(target):
                if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store):
                    counts[name.id] = counts.get(name.id, 0) + 1
    for arg in [*function.args.args, *function.args.kwonlyargs]:
        counts[arg.arg] = counts.get(arg.arg, 0) + 1  # a parameter is one binding
    return counts


def _input_name(function: ast.AST, counts: dict[str, int]) -> str | None:
    """The single string input: the only parameter (never rebound), or the one name read from a file, bound once."""
    params = [a.arg for a in function.args.args if a.arg not in ("self", "cls")]
    if len(params) == 1 and counts.get(params[0]) == 1:
        return params[0]
    if params:
        return None
    reads = [
        node.targets[0].id
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr in {"read_text", "read"}
    ]
    return reads[0] if len(reads) == 1 and counts.get(reads[0]) == 1 else None


def _asserted_pairs(test_tree: ast.AST) -> list[tuple[ast.AST, str | None, int]]:
    """(subject, key or attribute, expected int) for `<subject>[...]["key"] == N` and `<subject>.attr == N`."""
    pairs: list[tuple[ast.AST, str | None, int]] = []
    candidates: list[tuple[ast.AST, ast.AST]] = []
    for node in ast.walk(test_tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            candidates.append((node.left, node.comparators[0]))
        if isinstance(node, ast.Call) and _call_name(node) in _ASSERT_EQUAL and len(node.args) == 2:
            candidates.append((node.args[0], node.args[1]))
    for left, right in candidates:
        if not (isinstance(right, ast.Constant) and type(right.value) is int):
            continue
        if isinstance(left, ast.Subscript) and isinstance(left.slice, ast.Constant):
            if isinstance(left.slice.value, str):
                base = left.value
                while isinstance(
                    base, ast.Subscript
                ):  # index["file"]["key"]: the subject is the innermost base
                    base = base.value
                pairs.append((base, left.slice.value, right.value))
        elif isinstance(left, ast.Attribute):
            pairs.append((left.value, left.attr, right.value))
    return pairs


def _is_subject(base: ast.AST, symbol: str, bindings: dict[str, ast.expr]) -> ast.Call | None:
    """The target call that produces the asserted value: the call itself, or a name bound once to it."""
    if isinstance(base, ast.Name) and base.id in bindings:
        base = bindings[base.id]
    if isinstance(base, ast.Call) and _invokes(base, symbol) is base:
        return base
    return None


def _string_bindings(test_tree: ast.AST, call: ast.Call) -> list[tuple[str, bool, set[str]]]:
    """(literal, flows into the target, names of the binding): write_text literals and direct arguments of the call."""
    found: list[tuple[str, bool, set[str]]] = []
    direct = {
        a.value
        for a in [*call.args, *(kw.value for kw in call.keywords)]
        if isinstance(a, ast.Constant) and type(a.value) is str
    }
    for value in direct:
        found.append((value, True, set()))
    chain_names = _names_in(call)
    for node in ast.walk(test_tree):
        if isinstance(node, ast.Call) and _call_name(node) == "write_text" and node.args:
            literal = node.args[0]
            if isinstance(literal, ast.Constant) and type(literal.value) is str:
                receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
                names = _names_in(receiver) if receiver is not None else set()
                found.append((literal.value, bool(names & chain_names), names))
    unique = {(value, flows): (value, flows, names) for value, flows, names in found}
    return list(unique.values())


def _key_occurrences(function: ast.AST, key: str):
    """(value expression, unconditional?) for every place the key is assigned; AugAssign on it is returned as None."""
    enclosing: dict[int, ast.AST] = {}
    for node in ast.walk(function):
        for child in ast.iter_child_nodes(node):
            enclosing[id(child)] = node
    occurrences: list[tuple[ast.expr, bool]] = []
    for node in ast.walk(function):
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Subscript)
            and _is_key(node.target.slice, key)
        ):
            return None
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=True):
                if _is_key(k, key):
                    occurrences.append((v, _unconditional(node, enclosing, function)))
        elif isinstance(node, ast.Assign) and any(_is_store(t, key) for t in node.targets):
            occurrences.append((node.value, _unconditional(node, enclosing, function)))
    return occurrences


def _is_key(node: ast.AST | None, key: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == key


def _is_store(target: ast.AST, key: str) -> bool:
    if isinstance(target, ast.Subscript):
        return _is_key(target.slice, key)
    return isinstance(target, ast.Attribute) and target.attr == key


_CONDITIONAL = (ast.If, ast.While, ast.Try, ast.With, ast.IfExp, ast.Lambda, ast.comprehension, ast.Match)


def _unconditional(node: ast.AST, enclosing: dict[int, ast.AST], function: ast.AST) -> bool:
    """No branch, handler or comprehension between the assignment and the function. A for loop over the single bound
    input is allowed (the test binds exactly one file)."""
    current = enclosing.get(id(node))
    while current is not None and current is not function:
        if isinstance(current, _CONDITIONAL):
            return False
        current = enclosing.get(id(current))
    return True


class _Evaluator:
    """Whitelisted interpreter over literals, one input name, + - * //, len(), and str.count/split/splitlines/strip."""

    def __init__(self) -> None:
        self.steps = 0

    def run(self, node: ast.AST, names: dict[str, Any], depth: int = 0) -> Any:
        self.steps += 1
        if self.steps > MAX_STEPS or depth > MAX_DEPTH:
            raise _Unsupported
        return self._eval(node, names, depth + 1)

    def _eval(self, node: ast.AST, names: dict[str, Any], depth: int) -> Any:
        if isinstance(node, ast.Constant) and type(node.value) in (int, str):
            return self._checked(node.value)
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            value = self.run(node.operand, names, depth)
            if type(value) is int:
                return self._checked(-value)
            raise _Unsupported
        if isinstance(node, ast.BinOp):
            return self._binop(node, names, depth)
        if isinstance(node, ast.Call) and not node.keywords:
            args = [self.run(a, names, depth) for a in node.args]
            if isinstance(node.func, ast.Name) and node.func.id == "len" and len(args) == 1:
                if type(args[0]) in (str, list):
                    return len(args[0])
                raise _Unsupported
            if isinstance(node.func, ast.Attribute) and node.func.attr in _PURE_METHODS and len(args) <= 2:
                receiver = self.run(node.func.value, names, depth)
                if type(receiver) is str and all(type(a) in (int, str) for a in args):
                    result = _PURE_METHODS[node.func.attr](receiver, *args)
                    if isinstance(result, list) and len(result) > MAX_STR:
                        raise _Unsupported
                    return self._checked(result) if not isinstance(result, list) else result
        raise _Unsupported

    def _binop(self, node: ast.BinOp, names: dict[str, Any], depth: int) -> Any:
        left, right = self.run(node.left, names, depth), self.run(node.right, names, depth)
        kinds = (type(left), type(right))
        if isinstance(node.op, ast.Add) and kinds in ((int, int), (str, str)):
            return self._checked(left + right)
        if isinstance(node.op, ast.Sub) and kinds == (int, int):
            return self._checked(left - right)
        if isinstance(node.op, ast.Mult):
            if kinds == (int, int):
                return self._checked(left * right)
            if kinds in ((str, int), (int, str)):
                text, count = (left, right) if kinds == (str, int) else (right, left)
                if len(text) * max(count, 0) > MAX_STR:
                    raise _Unsupported
                return text * count
        if isinstance(node.op, ast.FloorDiv) and kinds == (int, int) and right != 0:
            return self._checked(left // right)
        raise _Unsupported

    @staticmethod
    def _checked(value: Any) -> Any:
        if type(value) is int and abs(value) > MAX_INT:
            raise _Unsupported
        if type(value) is str and len(value) > MAX_STR:
            raise _Unsupported
        return value


def expectation_contradicts_source(
    test_content: str, target_source: str, target: str
) -> dict[str, Any] | None:
    """D2: a contradiction (cap 0, the host may lower it), a derivation_required note (cap None), or None."""
    resolved = resolve_target(target)
    if resolved is None or resolved.kind == "route":
        return None
    test_tree = _parse(test_content)
    function = _target_function(_parse(strip_target_text(target_source)), resolved.symbol)
    if test_tree is None or function is None:
        return None
    bindings = _single_bindings(test_tree)
    counts = _binding_counts(function)
    ambiguous = None
    for subject, key, expected in _asserted_pairs(test_tree):
        call = _is_subject(subject, resolved.symbol, bindings)
        if call is None or key is None:
            continue  # not the direct return value of the target call (for example a test-local dict): not applicable
        reason = _d2_reason(test_tree, function, call, key, expected, counts, resolved.symbol)
        if isinstance(reason, dict):
            return reason
        ambiguous = ambiguous or reason
    if ambiguous:
        return {"rule": RULE_REQUIRED, "cap": None, "reason": ambiguous}
    return None


def _d2_reason(test_tree, function, call, key, expected, counts, symbol):
    """A contradiction dict, None (agrees), or a string explaining why the check does not apply."""
    input_name = _input_name(function, counts)
    if input_name is None:
        return f"`{symbol}` does not have exactly one string input that the test can bind"
    literals = _string_bindings(test_tree, call)
    if len(literals) != 1 or not literals[0][1]:
        return f"`{key}` cannot be bound to one string input that flows into `{symbol}`"
    occurrences = _key_occurrences(function, key)
    if occurrences is None:
        return f"`{key}` is changed with an augmented assignment"
    if len(occurrences) != 1:
        return f"`{key}` is assigned {len(occurrences)} times in `{symbol}`"
    expression, unconditional = occurrences[0]
    if not unconditional:
        return f"`{key}` is assigned conditionally in `{symbol}`"
    free = {n.id for n in ast.walk(expression) if isinstance(n, ast.Name)} - {"len"}
    if free != {input_name}:
        return f"`{key}` depends on names other than the single input"
    try:
        value = _Evaluator().run(expression, {input_name: literals[0][0]})
    except (_Unsupported, ZeroDivisionError, TypeError, ValueError, RecursionError, MemoryError):
        return f"`{key}` uses an expression outside the pure whitelist"
    if type(value) is not int:
        return f"`{key}` does not evaluate to an integer"
    if value == expected:
        return None
    return {
        "rule": RULE_CONTRADICTS,
        "cap": 0,
        "expected": expected,
        "value": value,
        "key": key,
        "reason": (
            f"The source computes `{key}` = {value} for the test input, but the test expects {expected}. "
            "Re-derive the expectation from the implementation before asserting it."
        ),
    }


# --- Blind derivation: masking for the reviewer ---------------------------------------------------------------


def literal_value_text(value: Any) -> str:
    """The reviewer-facing text of a literal (no quotes around strings)."""
    return value if isinstance(value, str) else str(value)


def _char_offset(line: str, byte_offset: int) -> int:
    return len(line.encode("utf-8")[:byte_offset].decode("utf-8", errors="ignore"))


def _assertion_literals(tree: ast.AST) -> list[ast.Constant]:
    """Right-hand literals of `==` comparisons and of assertEqual(…, literal) calls (single-line, Python)."""
    found: list[ast.Constant] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            right = node.comparators[0]
            if isinstance(right, ast.Constant) and type(right.value) in (int, str, float):
                found.append(right)
        if isinstance(node, ast.Call) and _call_name(node) in _ASSERT_EQUAL and len(node.args) == 2:
            right = node.args[1]
            if isinstance(right, ast.Constant) and type(right.value) in (int, str, float):
                found.append(right)
    return sorted((n for n in found if n.lineno == n.end_lineno), key=lambda n: (n.lineno, n.col_offset))


def _case_for_line(
    lineno: int, tree: ast.AST, markers: list[tuple[int, str]], file_cases: list[str]
) -> list[str]:
    """The case of the test function that encloses the line: its CASE_ID marker, else the file's own case IDs."""
    if not markers:
        return file_cases
    functions = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        and n.lineno <= lineno <= (n.end_lineno or n.lineno)
    ]
    if not functions:
        return file_cases
    innermost = max(functions, key=lambda n: n.lineno)
    start = min([innermost.lineno, *(d.lineno for d in innermost.decorator_list)])
    above = [case for line, case in markers if line < start]
    marker = above[-1] if above else None
    return [marker] if marker in file_cases or not file_cases else file_cases


def mask_expected_literals(files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Blind view of generated Python files: each assertion's right-hand literal becomes «E<n>».

    Returns (masked copies, slot records). A slot record holds the real literal (never sent to the reviewer), its
    normalised value text, its file index and its case: the CASE_ID marker of the enclosing test function, else the
    file's own case IDs. Files that do not parse pass through unmasked.
    """
    masked: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    for index, item in enumerate(files):
        content = item.get("content", "")
        tree = _parse(content) if str(item.get("path", "")).endswith(".py") else None
        if tree is None:
            masked.append(dict(item))
            continue
        lines = split_lines(content)
        file_cases = [str(c) for c in item.get("case_ids", [])]
        markers = [
            (number, match.group(1))
            for number, line in enumerate(lines, start=1)
            if (match := _CASE_MARKER.search(line))
        ]
        spans = []
        for literal in _assertion_literals(tree):
            row = literal.lineno - 1
            if row >= len(lines):
                continue
            number = len(slots) + 1
            slots.append(
                {
                    "slot": f"E{number}",
                    "file": index,
                    "case_ids": _case_for_line(literal.lineno, tree, markers, file_cases),
                    "value": literal_value_text(literal.value),
                    "literal": literal.value,
                    "line": literal.lineno,
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


def deterministic_findings(test_content: str, target_source: str, target: str) -> list[dict[str, Any]]:
    """All D1/D2 findings for one Python test text. Never raises: a failing check contributes no finding."""
    findings: list[dict[str, Any]] = []
    for check in (count_assumes_no_filter, expectation_contradicts_source):
        try:
            finding = check(test_content, target_source, target)
        except Exception:  # defensive: the repository under test must never crash the review stage
            finding = None
        if finding:
            findings.append(finding)
    return findings

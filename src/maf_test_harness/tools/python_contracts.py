"""Conservative source-only checks for generated Python fixture contracts."""

import ast

from ..skills.contracts import _repo_module_files


def check_fixture_contracts(content, path, ctx):
    tree = ast.parse(content)
    modules = _repo_module_files(ctx.source_files)
    contracts = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.level or node.module not in modules:
            continue
        source = ctx.read(modules[node.module])
        if not source:
            continue
        try:
            definitions = ast.parse(source).body
        except SyntaxError:
            continue
        for alias in node.names:
            cls = next((d for d in definitions if isinstance(d, ast.ClassDef) and d.name == alias.name), None)
            if (
                not cls
                or cls.bases
                or any(
                    isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and d.name == "__init__"
                    for d in cls.body
                )
            ):
                continue
            decorators = [d.func if isinstance(d, ast.Call) else d for d in cls.decorator_list]
            if not any(
                (isinstance(d, ast.Name) and d.id == "dataclass")
                or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
                for d in decorators
            ):
                continue
            if any(
                isinstance(d, ast.Call) and any(k.arg in {"init", "kw_only"} for k in d.keywords)
                for d in cls.decorator_list
            ):
                continue
            fields = []
            for field in cls.body:
                if not isinstance(field, ast.AnnAssign) or not isinstance(field.target, ast.Name):
                    continue
                annotation = ast.unparse(field.annotation)
                if "ClassVar" in annotation or "KW_ONLY" in annotation:
                    continue
                default = field.value
                if isinstance(default, ast.Call) and (
                    ast.unparse(default.func) in {"field", "dataclasses.field"}
                ):
                    if any(
                        k.arg == "init" and isinstance(k.value, ast.Constant) and k.value.value is False
                        for k in default.keywords
                    ):
                        continue
                    if any(k.arg == "kw_only" for k in default.keywords):
                        fields = []
                        break
                    required = not any(k.arg in {"default", "default_factory"} for k in default.keywords)
                else:
                    required = default is None
                fields.append((field.target.id, required))
            if fields:
                contracts[alias.asname or alias.name] = fields
    for call in ast.walk(tree):
        if (
            not isinstance(call, ast.Call)
            or not isinstance(call.func, ast.Name)
            or call.func.id not in contracts
        ):
            continue
        if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
            continue
        fields = contracts[call.func.id]
        names = [name for name, _ in fields]
        supplied = {k.arg for k in call.keywords} | set(names[: len(call.args)])
        unknown = supplied - set(names)
        missing = [name for name, required in fields if required and name not in supplied]
        if unknown or missing or len(call.args) > len(fields):
            raise ValueError(
                f"{path}:{call.lineno}: {call.func.id} fixture differs from source constructor; "
                f"unknown arguments={sorted(unknown)}, missing required={missing}; fields={names}."
            )
    unittest_aliases = {
        a.asname or a.name
        for n in tree.body
        if isinstance(n, ast.Import)
        for a in n.names
        if a.name == "unittest"
    }
    explicit_mock = any(
        (isinstance(n, ast.Import) and any(a.name == "unittest.mock" for a in n.names))
        or (
            isinstance(n, ast.ImportFrom)
            and n.module == "unittest"
            and any(a.name == "mock" for a in n.names)
        )
        for n in tree.body
    )
    if not explicit_mock and any(
        isinstance(n, ast.Attribute)
        and n.attr == "mock"
        and isinstance(n.value, ast.Name)
        and n.value.id in unittest_aliases
        for n in ast.walk(tree)
    ):
        raise ValueError(
            f"{path}: explicitly import unittest.mock; importing unittest alone does not load mock."
        )

"""Static checks for generated-test mistakes that only show up at run time, found on real runs.

Each check needs facts from the repository's own source (a decorator, an existence guard, a chain builder), so a model that
never opens the file gets them wrong and the failure surfaces one execution later. The host can see them before anything runs:

* ``@asynccontextmanager`` / ``@contextmanager`` functions driven like generators (``.__anext__()``), which fails with
  ``'_AsyncGeneratorContextManager' object has no attribute '__anext__'``;
* ``patch.object(app.state, "x", autospec=True)``: ``app.state`` attributes are plain values (often a mock from another test);
* a ``MagicMock`` standing in for a LangChain LLM inside a real chain: chains need a ``Runnable``;
* a hand-written stand-in class patched in for ``PromptTemplate``/``ChatPromptTemplate`` inside a real chain: LangChain
  validates it as a pydantic ``BasePromptTemplate`` and rejects a plain duck-typed object;
* a "UI test" that starts Streamlit and reads the server's HTML: it is an empty JavaScript shell, the widgets are not in it;
* a function called with a literal path that its first statement checks with ``os.path.exists``, while the test mocks
  everything else.

All are ``ValueError`` messages that say what to do instead, in the same style as the other static gates. They are heuristics
with deliberately narrow triggers: a false positive costs one repair round, a miss costs an execution.
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .code_intel import RepoContext

CONTEXT_MANAGER_DECORATORS = {"asynccontextmanager", "contextmanager"}
LLM_CLASSES = {
    "Ollama",
    "ChatOllama",
    "OpenAI",
    "ChatOpenAI",
    "AzureChatOpenAI",
    "ChatAnthropic",
    "HuggingFacePipeline",
    "LlamaCpp",
    "GPT4All",
    "ChatGoogleGenerativeAI",
}
CHAIN_MARKERS = re.compile(
    r"RetrievalQA|LLMChain|from_chain_type|create_retrieval_chain|load_qa_chain|ConversationalRetrievalChain"
)
CHAIN_BUILDERS = {
    "RetrievalQA",
    "LLMChain",
    "load_qa_chain",
    "create_retrieval_chain",
    "ConversationalRetrievalChain",
}
PROMPT_CLASSES = {"PromptTemplate", "ChatPromptTemplate", "FewShotPromptTemplate", "PipelinePromptTemplate"}
_MAX_SOURCE_BYTES = 200_000


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _decorator_names(node: ast.AST) -> set[str]:
    out = set()
    for dec in getattr(node, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        out.add(_name(target))
    return out


def _repo_functions(ctx: RepoContext) -> dict[str, ast.AST]:
    """Top-level functions of the repository's Python files by name (first definition wins), parsed once per context."""
    cache = getattr(ctx, "_pitfall_functions", None)
    if cache is not None:
        return cache
    found: dict[str, ast.AST] = {}
    for rel in ctx.source_files:
        if not rel.endswith(".py"):
            continue
        text = ctx.read(rel)
        if not text or len(text) > _MAX_SOURCE_BYTES:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found.setdefault(node.name, node)
    try:
        ctx._pitfall_functions = found  # type: ignore[attr-defined]
    except AttributeError:
        pass
    return found


def _imported_repo_names(tree: ast.AST, functions: dict[str, ast.AST]) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.update((a.asname or a.name) for a in node.names if a.name in functions)
    return names


def _check_context_manager_misuse(tree: ast.AST, path: str, functions: dict[str, ast.AST]) -> None:
    managed = {
        name: sorted(_decorator_names(fn) & CONTEXT_MANAGER_DECORATORS)[0]
        for name, fn in functions.items()
        if _decorator_names(fn) & CONTEXT_MANAGER_DECORATORS
    }
    if not managed:
        return
    variables: dict[str, str] = {}  # variable -> managed function it was assigned from
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and _name(node.value.func) in managed
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    variables[target.id] = _name(node.value.func)
    for node in ast.walk(tree):
        method, target = "", None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr
            in {
                "__anext__",
                "asend",
                "athrow",
                "__next__",
                "send",
            }
        ):
            method, target = node.func.attr, node.func.value
        elif isinstance(node, ast.Call) and _name(node.func) in {"anext", "next"} and node.args:
            method, target = _name(node.func), node.args[0]
        if target is None:
            continue
        source = _name(target.func) if isinstance(target, ast.Call) else variables.get(_name(target), "")
        if source in managed:
            raise ValueError(
                f"{path}: `{source}` is decorated with @{managed[source]}, so `{source}(...)` is a context manager, not a "
                f"generator: `{method}` does not exist on it. Use `async with {source}(...):` (or `with` for @contextmanager) "
                "and assert inside the block; startup runs on entry, shutdown on exit."
            )


def _check_autospec_on_state(tree: ast.AST, path: str) -> None:
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "object"
        ):
            continue
        if not any(
            kw.arg == "autospec" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in node.keywords
        ):
            continue
        if node.args and _name(node.args[0]) == "state":
            attr = (
                node.args[1].value
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                else "attribute"
            )
            raise ValueError(
                f"{path}: `patch.object(<app>.state, {attr!r}, autospec=True)` fails when the attribute is already a mock "
                "(InvalidSpecError) and there may be nothing to spec: `app.state` holds plain values that earlier tests or "
                f"the startup code set. Assign a MagicMock instead (`app.state.{attr} = MagicMock()`), inside a fixture that "
                "restores the previous value, or patch the factory the startup code calls."
            )


def _string_targets(tree: ast.AST) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _name(node.func) in {"patch", "object"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            out.append(node.args[0].value)
    return out


def _local_class_names(tree: ast.AST) -> set[str]:
    """Classes defined in this file (a stand-in the model wrote itself, not something imported for real)."""
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def _patch_replacement_targets(tree: ast.AST, replacements: set[str]) -> list[str]:
    """String target of every ``patch(target, replacement)`` whose second argument is one of ``replacements``."""
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _name(node.func) in {"patch", "object"}
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and isinstance(node.args[1], ast.Name)
            and node.args[1].id in replacements
        ):
            out.append(node.args[0].value)
    return out


def _check_mock_prompt_template_in_chain(tree: ast.AST, path: str, ctx: RepoContext) -> None:
    local_classes = _local_class_names(tree)
    if not local_classes:
        return
    for target in _patch_replacement_targets(tree, local_classes):
        module, _, attr = target.rpartition(".")
        if attr not in PROMPT_CLASSES or not module:
            continue
        rel = module.replace(".", "/") + ".py"
        source = ctx.read(rel) or ctx.read(f"src/{rel}") or ""
        if CHAIN_MARKERS.search(source):
            raise ValueError(
                f"{path}: patching `{target}` with a hand-written stand-in class puts a plain object where a real chain "
                f"expects a `{attr}` (LangChain validates it as a pydantic `BasePromptTemplate` and raises `value is not a "
                f"valid dict`). Capture the call without breaking validation: import the real `{attr}` (the same class "
                f"`{module}` imports it from) and use `patch('{target}', MagicMock(wraps=real_{attr.lower()}_class))` — the "
                "mock calls through to the real constructor and returns a genuine instance, while still recording the call "
                "args to assert on."
            )


def _check_mock_llm_in_chain(tree: ast.AST, path: str, content: str, ctx: RepoContext) -> None:
    if re.search(r"Fake\w*(?:LLM|ChatModel)|RunnableLambda|GenericFakeChatModel", content):
        return
    targets = _string_targets(tree)
    if any(t.rpartition(".")[2] in CHAIN_BUILDERS for t in targets):
        return  # the chain itself is patched too, so the mock LLM is never validated by a real chain
    for target in targets:
        module, _, attr = target.rpartition(".")
        if attr not in LLM_CLASSES or not module:
            continue
        rel = module.replace(".", "/") + ".py"
        source = ctx.read(rel) or ctx.read(f"src/{rel}") or ""
        if CHAIN_MARKERS.search(source):
            raise ValueError(
                f"{path}: patching `{target}` puts a MagicMock where a real LangChain chain expects an LLM; chains validate "
                "their `llm` as a Runnable and raise `instance of Runnable expected`. Use "
                "`langchain_core.language_models.fake.FakeListLLM(responses=[...])` (or `FakeListChatModel`), or patch the "
                "chain builder itself (`patch('%s.RetrievalQA')`) and assert on how it was called." % module
            )


def _check_streamlit_html(content: str, path: str) -> None:
    starts_streamlit = re.search(
        r"(?:Popen|run|check_call)\s*\([^)]*['\"]streamlit['\"]", content, re.S
    ) or re.search(r"streamlit\s+run", content)
    reads_http = re.search(r"urlopen\(|requests\.get\(|httpx\.get\(|urllib\.request", content)
    if starts_streamlit and reads_http and "playwright" not in content.lower() and "AppTest" not in content:
        raise ValueError(
            f'{path}: Streamlit\'s server returns an empty JavaScript shell (`<div id="root">`); widgets, radio options and '
            "text are drawn in the browser, so asserting on the fetched HTML can never find them. Test the UI in-process with "
            "`from streamlit.testing.v1 import AppTest` (`at = AppTest.from_file('streamlit_app.py').run()`; read "
            "`at.sidebar.radio`, `at.button`, `at.markdown`), or drive a real browser with Playwright when it is installed."
        )


def _first_param_guard(fn: ast.AST) -> str | None:
    """The first parameter's name when the function starts by checking that path exists (and raising)."""
    params = [a.arg for a in getattr(fn, "args", ast.arguments()).args]
    if not params:
        return None
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.If)
            and re.search(r"exists|isfile", ast.unparse(node.test))
            and params[0] in ast.unparse(node.test)
            and any(isinstance(n, ast.Raise) for n in ast.walk(node))
        ):
            return params[0]
    return None


def _check_existence_guard(tree: ast.AST, path: str, content: str, functions: dict[str, ast.AST]) -> None:
    if re.search(
        r"FileNotFoundError|pytest\.raises|tmp_path|mkstemp|NamedTemporaryFile|path\.exists|isfile", content
    ):
        return  # the test either expects the error or deals with the file
    imported = _imported_repo_names(tree, functions)
    literals = {  # `pdf_path = "/tmp/x.pdf"` ... `load_pdf(pdf_path)`
        target.id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _name(node.func) in imported and node.args:
            first = node.args[0]
            if isinstance(first, ast.Name):
                first = literals.get(first.id, first)
            fn = functions[_name(node.func)]
            guard = _first_param_guard(fn)
            if guard and isinstance(first, ast.Constant) and isinstance(first.value, str):
                raise ValueError(
                    f"{path}: `{_name(node.func)}` checks that `{guard}` exists before doing anything else and raises "
                    f"FileNotFoundError, but the test passes the literal {first.value!r} and never makes it exist. Create the "
                    "file with `tmp_path` (`p = tmp_path / 'x.pdf'; p.write_bytes(b'%PDF-1.4')`) or patch "
                    "`os.path.exists` where the function looks it up (`patch('<module>.os.path.exists', return_value=True)`)."
                )


def check_mock_pitfalls(content: str, path: str, ctx: RepoContext) -> None:
    """Raise ``ValueError`` for the first known run-time pitfall in a generated Python test (see the module docstring)."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return
    functions = _repo_functions(ctx)
    _check_context_manager_misuse(tree, path, functions)
    _check_autospec_on_state(tree, path)
    _check_mock_llm_in_chain(tree, path, content, ctx)
    _check_mock_prompt_template_in_chain(tree, path, ctx)
    _check_streamlit_html(content, path)
    _check_existence_guard(tree, path, content, functions)


__all__ = ["check_mock_pitfalls"]

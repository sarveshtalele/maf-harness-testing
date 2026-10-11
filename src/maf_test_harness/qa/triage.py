"""Failure triage: which files (and lines) to update, derived deterministically from execution evidence.

Tracebacks and compiler output name files and lines. Each location is classified as a *generated test* location
or a *repository source* location and every failing case is classified with simple, inspectable rules. The output
is a work list, not a verdict about who is at fault: a failing assertion can mean an application defect or a wrong
expectation, so the report says which of the two to check first.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

# path:line patterns emitted by pytest, javac/maven, jest/node, go, dotnet
_LOCATION = re.compile(
    r"""(?:File\s+"(?P<py>[^"]+)",\s+line\s+(?P<pyl>\d+))
      |(?P<colon>[\w./\\$@-]+\.(?:py|java|kt|js|jsx|ts|tsx|mjs|go|cs|rb|php|rs)):\[?(?P<line>\d+)
      |\((?P<jv>[\w$-]+\.(?:java|kt|cs)):(?P<jvl>\d+)\)""",
    re.X,
)

_TEST_DEFECT = re.compile(
    r"ImportError|ModuleNotFoundError|NameError|SyntaxError|IndentationError|fixture '.*' not found|collection failure|"
    r"cannot find symbol|does not exist|has private access|cannot be applied|incompatible types|reached end of file|"
    r"reading from stdin|No module named|package .* does not exist|is not abstract|unreported exception",
    re.I,
)
_APP_SIGNAL = re.compile(
    r"AssertionError|expected:|Expected|assert |toBe|toEqual|status (?:code )?(?:was|is) [45]\d\d", re.I
)


def locations(text: str) -> list[tuple[str, int]]:
    found = []
    for m in _LOCATION.finditer(text or ""):
        path = m.group("py") or m.group("colon") or m.group("jv")
        line = m.group("pyl") or m.group("line") or m.group("jvl")
        if path and line:
            found.append((path.replace("\\", "/"), int(line)))
    return found


def _match(path: str, candidates: list[str]) -> str | None:
    """Map a reported path (absolute, workspace-relative or bare file name) to a known repository path."""
    normalized = path.replace("\\", "/")
    if any(
        part in normalized
        for part in ("site-packages", "node_modules", "/usr/lib", ".harness-venv", "jdk", "python3")
    ):
        return None
    clean = normalized.split(".harness_runs/", 1)[-1]
    for candidate in sorted(candidates, key=len, reverse=True):
        if clean.endswith(candidate) or normalized.endswith(candidate):
            return candidate
    name = PurePosixPath(normalized).name
    hits = [c for c in candidates if PurePosixPath(c).name == name]
    return hits[0] if len(hits) == 1 else None


def classify(message: str) -> str:
    if _TEST_DEFECT.search(message):
        return "test_defect"
    return "check_application_then_expectation" if _APP_SIGNAL.search(message) else "needs_triage"


ACTION = {
    "test_defect": "Fix the generated test (wrong import/API/setup); use 'Regenerate failing tests' or edit the file.",
    "check_application_then_expectation": (
        "Check the application behavior at the source location first; if it is "
        "intended, correct the expectation in the test, otherwise fix the source."
    ),
    "needs_triage": "Read the failure output and decide whether the test or the application is wrong.",
}


REMEDIES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"^Accuracy gate:"),
        "Some cases are not verified at the required confidence. Retry generation (completed cases are kept), or revise "
        "the design for the listed cases. HARNESS_REQUIRE_FULL_ACCURACY=0 allows partial execution instead.",
    ),
    (
        re.compile(r"Independent test review requires repair"),
        "Fix the lowest-scoring rubric dimension or the named cap: call the real target from target_source, assert its "
        "actual result or effect, and use source-valid fixtures. A cap is applied when the target is not invoked, is mocked, "
        "has no assertion, or has no source.",
    ),
    (
        re.compile(
            r"\[gateway\]|gateway was unreachable|service failed to complete the prompt: "
            r"(?:Connection error|.*Error code: (?:429|50[234]))",
            re.I,
        ),
        "The model gateway was unreachable or overloaded; check the gateway URL/network, then Retry. Completed work is kept.",
    ),
    (
        re.compile(r"patch(?:\.object)?\(\s*(?:sys\s*,\s*)?['\"](?:sys\.)?exit['\"]"),
        "Do not mock sys.exit: execution then continues past the exit and fails later. Use "
        "`with pytest.raises(SystemExit) as exc:` and assert `exc.value.code`. To also assert which code sys.exit "
        "received per parametrized case, keep the patch but give it `side_effect=SystemExit` so it actually raises "
        "(`patch('sys.exit', side_effect=SystemExit) as mock_exit`), still inside `pytest.raises(SystemExit)`.",
    ),
    (
        re.compile(r"module '([\w.]+)' has no attribute '(\w+)'"),
        "You patched a name that does not exist at module level (it may be imported lazily inside a function). Patch only "
        "names shown in target_source; for a lazily imported package patch `sys.modules` with a stub instead.",
    ),
    (
        re.compile(r"reading from stdin|EOF when reading a line", re.I),
        "The code under test calls input(). Patch it (monkeypatch.setattr('builtins.input', lambda *_: '1')) or pass "
        "the CLI flag that skips the prompt; a test must never read real stdin.",
    ),
    (
        re.compile(r"NameError: name '(capsys|capfd|monkeypatch|tmp_path|caplog|mocker)' is not defined"),
        "A pytest fixture must be a parameter of the test function: add it to the signature, e.g. "
        "`def test_x(monkeypatch, capsys):`.",
    ),
    (
        re.compile(
            r"expected:?\s*<?\d+>?\s*but\s*was:?\s*<?(?:404|405)>?|\b(?:404|405)\b.*(?:Not Found|Not Allowed)",
            re.I,
        ),
        "The URL or HTTP method is not one the application serves: build the URL as BASE_URL + a route from "
        "generation_policy.live_app.routes (BASE_URL already contains the context path) and use a method listed for it.",
    ),
    (
        re.compile(r"no Bean Validation provider", re.I),
        "There is no Bean Validation provider on the test classpath: do not call Validation.buildDefaultValidatorFactory(); "
        "assert with plain Java (constructors, setters, getters) instead.",
    ),
    (
        re.compile(r"DeploymentScenario|Arquillian|No Container", re.I),
        "`mvn test` has no Arquillian container: do not use @RunWith(Arquillian.class) or @Deployment; use plain JUnit, "
        "or call the live application with java.net.http.HttpClient and BASE_URL.",
    ),
    (
        re.compile(r"too many statically nested blocks"),
        "Do not nest more than 5 `with` blocks: use @patch decorators or contextlib.ExitStack.",
    ),
    (
        re.compile(r"NameError: name '(\w+)' is not defined"),
        "Import or define the name shown in the error (for example add the missing import at the top of the file).",
    ),
    (
        re.compile(r"Executable doesn't exist|npx playwright install|browserType\.launch", re.I),
        "The Playwright browser is not installed in this environment (an environment problem, not a test defect): "
        "do not change the test; the host installs Chromium during setup.",
    ),
    (
        re.compile(r"ERR_CONNECTION_REFUSED|ECONNREFUSED|Connection refused", re.I),
        "Nothing answered at BASE_URL: read the target only from the BASE_URL environment variable "
        "(never a hard-coded host or port); the host starts the application before ui/e2e tests run.",
    ),
    (
        re.compile(r"_AsyncGeneratorContextManager|_GeneratorContextManager.*(?:__anext__|__next__)"),
        "The target is decorated with @asynccontextmanager/@contextmanager: use `async with target(app):` (or `with`), "
        "not `.__anext__()`.",
    ),
    (
        re.compile(r"InvalidSpecError|already been mocked out"),
        "Do not autospec an attribute that may already be a mock (app.state values are plain): assign a MagicMock in a "
        "fixture that restores the previous value.",
    ),
    (
        re.compile(r"instance of Runnable expected"),
        "A MagicMock is not a LangChain Runnable: use langchain_core.language_models.fake.FakeListLLM(responses=[...]) or "
        "patch the chain builder.",
    ),
    (
        re.compile(r"Missing '[^']+' (?:radio|button|option)|<div id=\"root\">"),
        "Streamlit's HTML is an empty JavaScript shell: test the UI in-process with streamlit.testing.v1.AppTest.",
    ),
    (
        re.compile(r"SRE module mismatch|Microsoft Visual C\+\+ 14\.0"),
        "Environment problem (a mixed interpreter or a package that needs a C/C++ compiler), not a test defect: "
        "do not rewrite the test; call environment_gaps and stub packages that are not installed.",
    ),
    (
        re.compile(
            r"No module named '(?:fastapi|uvicorn|streamlit|flask|django|langchain\w*|chromadb|numpy|pandas)'"
        ),
        "This third-party package is not installed in the isolated environment (call environment_gaps). Do not import "
        "it at module level: put a stub in sys.modules before importing the code under test, or test in-process "
        "code that does not need it.",
    ),
    (
        re.compile(
            r"replicat|re-?implement|copies the (?:source|function|implementation)|is NOT exported|not exported",
            re.I,
        ),
        "Do not copy application code into the test. Import the real symbol; if it is not exported/public, test it "
        "through the exported function, component or class that uses it (render the component, call the public API), "
        "or record a testability gap.",
    ),
    (
        re.compile(r"does not match any repository path|not a valid Python identifier"),
        "Do not normalise filesystem paths into imports (never translate '-' to '_'): inspect source_files and the "
        "existing tests, and use the sys.path root and import line from `import_recipes` for a script under a "
        "non-importable directory (or the repository's public API/registry fixture, e.g. "
        '`await registry.execute("skill-name", {...})`).',
    ),
    (
        re.compile(r"generated Python source is invalid"),
        "Keep every source line syntactically complete: each `lines` entry is one real line of Python, never JSON "
        "punctuation such as `],` or `case_ids:[`; close all brackets before the array ends.",
    ),
    (
        re.compile(
            r"Structured output failed schema validation|validation errors? for |did not return a complete "
            r"JSON object|Agent response must be a JSON object|empty response instead of a JSON",
            re.I,
        ),
        "Return exactly one JSON object matching the schema; no prose or markdown fences; keep each file concise to "
        "avoid truncation.",
    ),
    (
        re.compile(r"NoSuchFieldException|field .* not found|not @FXML-injected fields|never wired", re.I),
        "Only inject fields that are declared in the source; locals created inside methods cannot be injected. "
        "Assert on observable state instead.",
    ),
    (
        re.compile(r"static call|getDatabaseConnection|constructor .{0,40}calls", re.I),
        "Do not use @InjectMocks expecting injection when the constructor acquires dependencies statically: write an "
        "integration test, test pure methods, use mockStatic only if mockito-inline is already a declared dependency, "
        "or record a testability gap.",
    ),
    (
        re.compile(r"insufficient_evidence|truncated", re.I),
        "Only assert behaviour visible in target_source; drop or retarget assertions about code that is not shown.",
    ),
    (
        re.compile(r"Toolkit not initialized|Not on FX application thread", re.I),
        "Initialise the JavaFX toolkit once (JavaFX 8: `new JFXPanel()`; 9+: `Platform.startup`) and run UI "
        "mutations on the FX thread (Platform.runLater + latch).",
    ),
    (
        re.compile(r"is not declared in \S*package\.json"),
        "Import only packages declared in that package.json; use its existing test runner (jest, vitest or Playwright), "
        "aliases and @testing-library only when declared. Otherwise test through the real module's exported API with "
        "no new dependency, or record a gap.",
    ),
    (
        re.compile(r"testEnvironment is 'node'"),
        "Keep the test to logic: with the repository's node test environment do not render components or touch "
        "document/window; test pure functions or hooks, or record a testability gap.",
    ),
    (
        re.compile(r"neither a repository file nor a configured alias"),
        "Use a relative path from the test file to an existing source file, or an alias already configured in "
        "tsconfig/jest; do not invent an alias.",
    ),
    (
        re.compile(r"does not match any repository file"),
        "Use a relative path from the test file to an existing source file, or an alias already configured in "
        "tsconfig/jest; do not invent an alias.",
    ),
    (
        re.compile(r"Cannot find module|ERR_MODULE_NOT_FOUND"),
        'Use the repository\'s module system (ESM vs CJS from package.json "type"), relative paths from the test file, '
        "and existing path aliases from tsconfig.",
    ),
    (
        re.compile(r"\bCS0246\b|\bCS0234\b"),
        "Add the using/namespace the source declares; reference only projects the test csproj already references.",
    ),
    (
        re.compile(r"cannot import name|No module named|ImportError"),
        "Import only names defined at module level in target_source and modules listed in source_files / "
        "python_import_roots: e.g. `from app.rag_pipeline import get_chain`, not a name you expect to exist but "
        "never saw declared in either list. For a hyphenated or numeric-prefixed directory copy the sys.path root "
        "and import line from `import_recipes`.",
    ),
    (
        re.compile(r"readonly attribute|can't set attribute|cannot set attribute", re.I),
        "Do not assign to read-only attributes; patch the owning module/object with monkeypatch.setattr, or capture "
        "output with capsys instead of replacing stream attributes.",
    ),
    (
        re.compile(r"fixture '(\w+)' not found"),
        "Use only built-in pytest fixtures (monkeypatch, tmp_path, capsys, capfd) or unittest.mock.",
    ),
    (
        re.compile(r"KeyError: '(\w+)'"),
        "The data you built lacks the key the code reads; construct the full shape shown in target_source, not a "
        "partial mock, e.g. if the code reads `payload['user']['id']`, build "
        "`{'user': {'id': 1, ...}}` with every key it accesses, not just the one that happened to be missing.",
    ),
    (
        re.compile(r"cannot find symbol|does not exist|has private access|cannot be applied", re.I),
        "Use only classes, methods and constructors that appear in target_source; test through the public API. Call "
        "collaborator_signatures for the real constructors/methods available on a type before guessing one.",
    ),
    (
        re.compile(r"AssertionError|expected:<|Expected|assert ", re.I),
        "Recompute the expected value from target_source instead of guessing a literal: trace the actual "
        "transformation the code applies to the input you passed, and assert that exact result. If the code's "
        "behavior is intended, correct the expectation; never weaken the assertion just to pass.",
    ),
]


def remedy_for(message: str) -> str:
    """A concrete fix for the most common generated-test failures (empty when none applies)."""
    return next((text for pattern, text in REMEDIES if pattern.search(message or "")), "")


def build_updates(
    cases: list[dict[str, Any]],
    generated: list[dict[str, Any]],
    source_files: list[str],
    requirement_coverage: list[dict[str, Any]],
    requirement_sources: dict[str, list[str]],
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    test_paths = [f["path"] for f in generated]
    tests, sources, gaps = [], [], []
    for case in cases:
        if case["status"] not in {"failed", "error", "not_executed"} or not case.get("message"):
            continue
        kind = classify(case["message"])
        test_lines, source_lines = [], []
        for path, line in locations(case["message"]):
            if hit := _match(path, test_paths):
                test_lines.append((hit, line))
            elif hit := _match(path, source_files):
                source_lines.append((hit, line))
        # A test-defect always points at the generated test; the file is known even when no line was printed.
        own = case.get("file")
        if own and (kind == "test_defect" or not source_lines or test_lines):
            lines = sorted({ln for p, ln in test_lines if p == own})
            tests.append(
                {
                    "file": own,
                    "lines": lines,
                    "case_id": case["id"],
                    "case": case["title"],
                    "classification": kind,
                    "detail": case["message"].strip().splitlines()[0][:240],
                    "action": remedy_for(case["message"]) or ACTION[kind],
                }
            )
        for path, line in dict.fromkeys(source_lines):
            sources.append(
                {
                    "file": path,
                    "line": line,
                    "case_id": case["id"],
                    "case": case["title"],
                    "classification": kind,
                    "detail": case["message"].strip().splitlines()[0][:240],
                    "action": ACTION["check_application_then_expectation"],
                }
            )
    for req in requirement_coverage:
        if req["state"] in {"untested", "not_executed"}:
            gaps.append(
                {
                    "requirement_id": req["id"],
                    "requirement": req["text"][:160],
                    "state": req["state"],
                    "source_files_to_cover": requirement_sources.get(req["id"], []),
                    "action": (
                        "Add a test case (and generated test file) for this requirement."
                        if req["state"] == "untested"
                        else "Run again after fixing the failing/uncompiled tests."
                    ),
                }
            )
    env = []
    for package in (environment or {}).get("missing_packages") or []:
        env.append(
            {
                "tool": package,
                "action": f"Declared package '{package}' is not installed, so tests that need it cannot pass: install the "
                "platform build tools or use a Python version that has wheels for the pinned version (the strict "
                "environment guardrail proposes one), then run again.",
            }
        )
    if environment and environment.get("missing_prerequisites"):
        env = [
            {"tool": t, "action": f"Install {t} on the host, then retry the environment stage."}
            for t in environment["missing_prerequisites"]
        ]
    for d in (environment or {}).get(
        "diagnoses", []
    ):  # environment/infra errors found in the run's own output
        if d["category"] in {"environment", "infra"}:
            env.append({"tool": d["code"], "action": f"{d['title']}: {d['hint']}"})
    return {
        "generated_test_files_to_update": tests,
        "repository_files_to_review": sources,
        "requirements_needing_tests": gaps,
        "environment_actions": env,
    }

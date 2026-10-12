"""Entry point of the bundled desktop backend (``maf-backend``).

Serves the loopback API exactly like ``python -m maf_test_harness.ui.api`` (``--port``, ``HARNESS_API_TOKEN``),
plus ``--self-check`` for CI: verify bundled resources, lint the skills, audit the skill scripts' imports and run one
bundled skill script end to end, no network, then exit. ``--run-python`` makes the executable act as a Python
interpreter for ``paths.python_argv()``: the frozen app's ``sys.executable`` is this program, not Python.
"""

from __future__ import annotations

import ast
import builtins
import importlib.util
import json
import multiprocessing
import os
import runpy
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from maf_test_harness import paths
from maf_test_harness.repo.requirements import NO_DECLARATIONS_MARKER

PYTHON_FLAGS = {"-B", "-E", "-I", "-O", "-OO", "-S", "-s", "-u"}


def run_python(argv: list[str]) -> int:
    """``python`` for ``[maf-backend, --run-python, ...]``: ``[-I] [-B] (-c code | -m module | script) [arg ...]``."""
    isolated = False
    # Always: the bundled scripts live in the read-only install dir (Program Files), so no __pycache__ may be written there.
    sys.dont_write_bytecode = True
    while argv and argv[0].startswith("-") and argv[0] not in {"-c", "-m"}:
        flag, rest = argv[0], argv[1:]
        if flag == "-I":
            isolated = True
        elif flag == "-B":
            sys.dont_write_bytecode = True
        elif flag == "-X" and rest:  # the harness passes -X pycache_prefix=<dir> for approved plugin code
            if rest[0].startswith("pycache_prefix="):
                sys.pycache_prefix = rest[0].partition("=")[2]
            rest = rest[1:]
        elif flag == "--check-hash-based-pycs" and rest:
            rest = rest[1:]
        elif flag not in PYTHON_FLAGS:
            print(f"maf-backend --run-python: unsupported option {flag!r}", file=sys.stderr)
            return 2
        argv = rest
    if not argv or (argv[0] in {"-c", "-m"} and len(argv) < 2):
        print(
            "usage: maf-backend --run-python [-I] [-B] (-c code | -m module | script) [arg ...]",
            file=sys.stderr,
        )
        return 2
    head, tail = argv[0], argv[1:]
    if head not in {"-c", "-m"} and not Path(head).is_file():
        print(f"can't open file '{head}': [Errno 2] No such file or directory", file=sys.stderr)
        return 2
    try:
        if head == "-c":
            sys.argv = ["-c", *tail[1:]]
            if not isolated:
                sys.path.insert(0, "")
            exec(compile(tail[0], "<string>", "exec"), {"__name__": "__main__", "__builtins__": builtins})
        elif head == "-m":
            sys.argv = [tail[0], *tail[1:]]
            if not isolated:
                sys.path.insert(0, "")
            runpy.run_module(tail[0], run_name="__main__", alter_sys=True)
        else:
            sys.argv = [head, *tail]
            if not isolated:  # like CPython: the script's own folder comes first on sys.path (not with -I)
                sys.path.insert(0, str(Path(head).resolve().parent))
            runpy.run_path(head, run_name="__main__")
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        print(exc.code, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        traceback.print_exc()
        return 1
    return 0


GUARDED_IMPORT_ERRORS = {"ImportError", "ModuleNotFoundError"}


def _caught_names(node: ast.expr | None) -> set[str]:
    targets = node.elts if isinstance(node, ast.Tuple) else [node]
    return {
        t.id if isinstance(t, ast.Name) else t.attr
        for t in targets
        if isinstance(t, (ast.Name, ast.Attribute))
    }


def _module_level_imports(nodes: Iterable[ast.AST]) -> Iterator[ast.Import | ast.ImportFrom]:
    """Imports that run when the script is loaded: not inside functions, and not inside a try that catches ImportError
    (an optional dependency the script copes without)."""
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        elif isinstance(node, ast.Try):
            guarded = any(
                h.type is None or _caught_names(h.type) & GUARDED_IMPORT_ERRORS for h in node.handlers
            )
            parts = [node.orelse, node.finalbody, *(h.body for h in node.handlers)]
            if not guarded:
                parts.append(node.body)
            for part in parts:
                yield from _module_level_imports(part)
        else:
            yield from _module_level_imports(ast.iter_child_nodes(node))


def _script_imports(script: Path) -> set[str]:
    """Top-level modules a skill script needs at load time, without relative imports and its own sibling modules."""
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    names: set[str] = set()
    for node in _module_level_imports(tree.body):
        if isinstance(node, ast.Import):
            names.update(alias.name.partition(".")[0] for alias in node.names)
        elif node.level == 0 and node.module:
            names.add(node.module.partition(".")[0])
    return names - {p.stem for p in script.parent.iterdir()}


def _unresolved_skill_imports() -> list[str]:
    problems = []
    for script in sorted(paths.skills_dir().glob("*/scripts/**/*.py")):
        for name in sorted(_script_imports(script)):
            try:
                found = importlib.util.find_spec(name) is not None
            except (ImportError, ValueError):
                found = False
            if not found:
                problems.append(f"{script.relative_to(paths.skills_dir()).as_posix()}: import {name}")
    return problems


def _run_bundled_skill(work: Path) -> dict:
    """Run the reverse-engineering host script through the same runner the host uses (allow-list and hash checks)."""
    from maf_test_harness.skills.scripts import HarnessScriptRunner

    repo = work / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    request = {"root": str(repo), "source_files": ["app.py"]}
    runner = HarnessScriptRunner(paths.skills_dir(), tiers=("host",))
    return runner.run(
        "reverse-engineering",
        "scripts/analyze.py",
        ["--request", "input/request.json", "--output", "output.json"],
        files={"request.json": json.dumps(request)},
        output_json="output.json",
    )


SMOKE_ARGS = ["--request", "input/request.json", "--output", "output.json"]
# Small repositories for each stack, a monorepo and an empty one. Names are NFC; one folder has a space and one file a
# non-ASCII name, the Windows path cases. Declarations are heuristic outside Python, which the scripts report as gaps.
SAMPLE_FILES: dict[str, dict[str, str]] = {
    "python repo": {
        "pyproject.toml": '[project]\nname = "sample"\nversion = "0.1.0"\n',
        "src/sample/__init__.py": "",
        "src/sample/módulo.py": "def soma(a, b):\n    return a + b\n\n\nclass Calc:\n    def dividir(self, a, b):\n        return a / b\n",
        "tests/test_módulo.py": "from sample.módulo import soma\n\n\ndef test_soma():\n    assert soma(1, 2) == 3\n",
    },
    "js ts repo": {
        "package.json": '{"name": "sample", "version": "0.1.0", "main": "src/index.ts"}\n',
        "src/index.ts": "export function greet(name: string): string {\n  return `hello ${name}`;\n}\n",
    },
    "java repo": {
        "pom.xml": "<project><modelVersion>4.0.0</modelVersion><groupId>demo</groupId><artifactId>app</artifactId>"
        "<version>1.0</version></project>\n",
        "src/main/java/App.java": "public class App {\n    public static int add(int a, int b) {\n        return a + b;\n    }\n\n"
        "    public static void main(String[] args) {\n        System.out.println(add(1, 2));\n    }\n}\n",
    },
    "dotnet repo": {
        "App.csproj": '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup><OutputType>Exe</OutputType>'
        "<TargetFramework>net8.0</TargetFramework></PropertyGroup>\n</Project>\n",
        "Program.cs": "public static class Program\n{\n    public static int Add(int a, int b) => a + b;\n\n"
        "    public static void Main() { }\n}\n",
    },
    "go repo": {
        "go.mod": "module example.com/sample\n\ngo 1.22\n",
        "main.go": "package main\n\nfunc Add(a int, b int) int {\n\treturn a + b\n}\n\nfunc main() {}\n",
    },
    "mixed monorepo": {
        "services/api/app.py": "def handle(request):\n    return {'ok': True}\n",
        "web/src/index.ts": "export const version = '1';\nexport function render(page: string) {\n  return page;\n}\n",
        "tools/main.go": "package main\n\nfunc Tool() int {\n\treturn 1\n}\n",
        "svc/src/main/java/Svc.java": "public class Svc {\n    public static int size() {\n        return 0;\n    }\n}\n",
    },
    "empty repo": {},
}


@dataclass(frozen=True)
class SampleRepo:
    files: dict[str, str]
    expect_facts: bool  # False: the repository has no declarations, so requirement-analysis must refuse it


SAMPLE_REPOS: dict[str, SampleRepo] = {
    "python repo": SampleRepo(SAMPLE_FILES["python repo"], expect_facts=True),
    "js ts repo": SampleRepo(SAMPLE_FILES["js ts repo"], expect_facts=True),
    "java repo": SampleRepo(SAMPLE_FILES["java repo"], expect_facts=True),
    "dotnet repo": SampleRepo(SAMPLE_FILES["dotnet repo"], expect_facts=True),
    "go repo": SampleRepo(SAMPLE_FILES["go repo"], expect_facts=True),
    "mixed monorepo": SampleRepo(SAMPLE_FILES["mixed monorepo"], expect_facts=True),
    "empty repo": SampleRepo(SAMPLE_FILES["empty repo"], expect_facts=False),
}

# Minimal valid inputs for the validators, built from the contracts their assets declare (no network, no toolchains).
VALIDATOR_INPUTS: dict[str, dict[str, object]] = {
    "traceability": {
        "metadata": {
            "generated_at": "2026-01-01T00:00:00Z",
            "generator_version": "self-check",
            "source_skill_names": ["traceability-generator"],
        },
        "traceability_matrix": [
            {
                "requirement_id": "REQ-1",
                "test_case_id": "TC-1",
                "artifact_id": "ART-1",
                "artifact_type": "test",
                "status": "covered",
            }
        ],
        "gaps_report": {
            "uncovered_requirements": [],
            "orphan_tests": [],
            "missing_links": [],
            "warnings": [],
            "notes": [],
        },
    },
    "traceability_source": {
        "requirements": [{"id": "REQ-1"}],
        "test_cases": [{"id": "TC-1"}],
        "generated_artifacts": [{"id": "ART-1"}],
    },
    "fixture": {
        "positive_fixtures": [
            {
                "test_case_id": "TC-1",
                "payload": {"name": "ok"},
                "traceability_links": [{"source_id": "REQ-1", "type": "requirement"}],
            }
        ],
        "negative_fixtures": [],
        "boundary_fixtures": [],
        "api_payload_examples": {"requests": [], "responses": []},
        "summary_metadata": {
            "fixture_count": 1,
            "positive_count": 1,
            "negative_count": 0,
            "boundary_count": 0,
            "api_payload_count": 0,
            "coverage_percentage": 100,
        },
        "gaps_report": {"coverage_percentage": 100},
    },
    "contract": {
        "api_contracts": [],
        "validation_rules": [],
        "gaps_report": {
            "invalid_document": False,
            "missing_schemas": [],
            "incomplete_endpoints": [],
            "notes": "",
        },
    },
}

SkillRun = Callable[..., dict]  # HarnessScriptRunner.run, or any runner with the same keyword interface


def write_sample_repo(base: Path, name: str, files: dict[str, str]) -> Path:
    root = base / unicodedata.normalize("NFC", name)
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        target = root / unicodedata.normalize("NFC", rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def _tail(outcome: dict) -> str:
    return (outcome.get("stderr") or outcome.get("stdout") or "")[
        -400:
    ] or f"exit code {outcome.get('exit_code')}"


def run_skill_smoke(base: Path, run: SkillRun, report: Callable[[str], None]) -> list[str]:
    """Run the host analysis scripts on every sample repo, then each validator once, through ``run``.

    Returns the problems found and reports one line per script and sample. Each sample states whether it must yield
    declarations; a sample with none must be refused by requirement-analysis with NO_DECLARATIONS_MARKER. A run that
    raises is recorded as a problem and the remaining runs still happen.
    """
    problems: list[str] = []

    def check(label: str, started: float, ok: bool, detail: str = "") -> None:
        report(
            f"  {'OK  ' if ok else 'FAIL'} {label} ({time.monotonic() - started:.2f}s)"
            + ("" if ok else f": {detail}")
        )
        if not ok:
            problems.append(f"{label}: {detail}")

    def call(*args, **kwargs) -> dict:
        try:
            return run(*args, **kwargs)
        except Exception as exc:  # recorded as a failed run; never aborts the self-check
            return {"exit_code": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}", "data": None}

    for name, sample in SAMPLE_REPOS.items():
        try:
            root = write_sample_repo(base, name, sample.files)
            source_files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
            started = time.monotonic()
            analysis = call(
                "reverse-engineering",
                "scripts/analyze.py",
                SMOKE_ARGS,
                files={"request.json": json.dumps({"root": str(root), "source_files": source_files})},
                output_json="output.json",
            )
            facts = (analysis.get("data") or {}).get("facts")
            ok = analysis["exit_code"] == 0 and isinstance(facts, list) and bool(facts) == sample.expect_facts
            detail = (
                _tail(analysis)
                if analysis["exit_code"] != 0
                else f"{len(facts or [])} facts, expected {sample.expect_facts}"
            )
            check(f"reverse-engineering [{name}]", started, ok, detail)
            if not ok:
                continue
            label = f"requirement-analysis [{name}]" + (
                "" if sample.expect_facts else " (refusal expected: no declarations)"
            )
            started = time.monotonic()
            draft = call(
                "requirement-analysis",
                "scripts/analyze.py",
                SMOKE_ARGS,
                files={
                    "request.json": json.dumps(
                        {"analysis": analysis["data"], "app_name": name, "knowledge": ""}
                    )
                },
                output_json="output.json",
            )
            if sample.expect_facts:
                ok = draft["exit_code"] == 0 and isinstance(draft.get("data"), dict)
            else:
                ok = (
                    draft["exit_code"] not in (0, None)
                    and NO_DECLARATIONS_MARKER in draft["stderr"]
                    and draft.get("data") is None
                )
            check(label, started, ok, _tail(draft))
        except Exception as exc:  # the sample itself could not be written or analysed
            check(f"sample [{name}]", time.monotonic(), False, f"{type(exc).__name__}: {exc}")

    inputs = VALIDATOR_INPUTS
    validators = [
        (
            "traceability-generator",
            "scripts/validate_traceability.py",
            ["input/traceability.json", "--source", "input/source.json"],
            {"traceability.json": inputs["traceability"], "source.json": inputs["traceability_source"]},
            "Traceability validation passed.",
            "minimal valid input",
        ),
        (
            "test-data-generator",
            "scripts/validate_fixtures.py",
            ["input/fixture.json"],
            {"fixture.json": inputs["fixture"]},
            "VALIDATION PASSED",
            "minimal valid input",
        ),
        (
            "api-contract-analyzer",
            "scripts/validate_contract.py",
            ["input/contract.json"],
            {"contract.json": inputs["contract"]},
            "Contract report is valid.",
            "minimal valid input",
        ),
        (
            "test-data-generator",
            "scripts/deterministic_helpers.py",
            ["--seed", "42", "--count", "2"],
            {},
            '"seed": 42',
            "--seed 42",
        ),
    ]
    # validate_design.py is not in SKILLS.lock.json, so the host runner refuses it; the test suite import-checks it.
    report("  SKIP test-design-generator/scripts/validate_design.py: not hash-locked in SKILLS.lock.json")
    for skill, rel, args, files, expected, mode in validators:
        started = time.monotonic()
        outcome = call(skill, rel, args, files={k: json.dumps(v) for k, v in files.items()})
        ok = outcome["exit_code"] == 0 and expected in outcome["stdout"]
        check(f"{rel} [{mode}]", started, ok, _tail(outcome))
    return problems


def _console_safe() -> None:
    """Child stderr can carry non-ASCII text; a cp1252 Windows console must not crash the report on it."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # not a reconfigurable text stream
            pass


def _self_check_steps() -> bool:
    """Every check; returns True when any failed. Each step reports its own failure, so the rest still run."""
    missing = [
        str(p)
        for p in (
            paths.skills_dir() / "SKILLS.lock.json",
            paths.prompts_dir() / "personas.json",
            paths.prompts_dir() / "repository-review.md",
        )
        if not p.is_file()
    ]
    from maf_test_harness.qa import step_reports

    if not (step_reports.TEMPLATES / "00-summary.md").is_file():
        missing.append(str(step_reports.TEMPLATES / "00-summary.md"))
    if missing:
        print("self-check FAILED, missing bundled resources:\n  " + "\n  ".join(missing), file=sys.stderr)
        return True
    from maf_test_harness import cli

    try:
        lint = cli.main(["lint-skills"])
    except Exception:
        traceback.print_exc()
        lint = 1
    print(f"lint-skills {'OK' if not lint else 'FAILED'}", flush=True)
    failed = bool(lint)
    try:
        imports = _unresolved_skill_imports()
    except Exception:
        traceback.print_exc()
        imports = ["<import audit raised>"]
    if imports:
        print(
            "self-check FAILED, skill script imports unavailable in this runtime:\n  " + "\n  ".join(imports),
            file=sys.stderr,
        )
        failed = True
    try:
        with tempfile.TemporaryDirectory(prefix="maf-self-check-") as tmp:
            outcome = _run_bundled_skill(Path(tmp))
    except Exception as exc:
        outcome = {"exit_code": None, "stderr": f"{type(exc).__name__}: {exc}", "data": None}
    if outcome["exit_code"] != 0 or not isinstance(outcome.get("data"), dict):
        print(f"self-check FAILED, bundled skill script did not run:\n{outcome['stderr']}", file=sys.stderr)
        failed = True
    try:
        from maf_test_harness.skills.scripts import HarnessScriptRunner

        runner = HarnessScriptRunner(paths.skills_dir(), tiers=("host", "model"))
        started = time.monotonic()
        print("skill smoke (sample repositories and validators):", flush=True)
        with tempfile.TemporaryDirectory(prefix="maf-self-check-") as tmp:
            smoke = run_skill_smoke(Path(tmp), runner.run, lambda line: print(line, flush=True))
        if smoke:
            print("self-check FAILED, skill smoke:\n  " + "\n  ".join(smoke), file=sys.stderr)
            failed = True
        print(f"skill smoke {'FAILED' if smoke else 'OK'} in {time.monotonic() - started:.1f}s", flush=True)
    except Exception:
        traceback.print_exc()
        failed = True
    return failed


def self_check() -> int:
    _console_safe()
    try:
        failed = _self_check_steps()
    except Exception:  # whatever went wrong, the verdict line below is always printed
        traceback.print_exc()
        failed = True
    print(f"self-check {'FAILED' if failed else 'OK'} (resources: {paths.resource_root()})", flush=True)
    return int(failed)


def _exit_when_stdin_closes() -> None:
    """Parent-death signal: the Electron shell holds this process's stdin pipe, so EOF means the shell is gone."""

    def watch() -> None:
        try:
            sys.stdin.buffer.read()
        except (OSError, ValueError, AttributeError):
            pass
        os._exit(0)

    threading.Thread(target=watch, name="parent-stdin-watch", daemon=True).start()


def _strip_instance(argv: list[str]) -> list[str]:
    """Drop `--instance <id>`: the desktop shell passes a per-launch id only so orphan cleanup can recognise this
    process by its command line. The API's own argument parser does not know it."""
    if "--instance" not in argv:
        return argv
    i = argv.index("--instance")
    return argv[:i] + argv[i + 2 :]


def main(argv: list[str] | None = None) -> int:
    argv = _strip_instance(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--run-python"]:
        return run_python(argv[1:])
    if "--self-check" in argv:
        return self_check()
    # Opt-in (set by the desktop shell only): a terminal or CI launch must not exit on an EOF of its stdin.
    if os.environ.get("HARNESS_EXIT_ON_STDIN_EOF") == "1":
        _exit_when_stdin_closes()
    from maf_test_harness.ui import api

    sys.argv = [sys.argv[0], *argv]
    api.main()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())

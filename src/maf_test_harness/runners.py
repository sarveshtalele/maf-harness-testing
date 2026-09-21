"""Per-ecosystem runner adapters: turn a StackProfile + generated files into an explicit, reviewable plan.

The host, never a model, chooses every command. The plan is shown to the human before anything runs.
Execution is local-process (not a security sandbox); see execution.run_command.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from . import platform_compat
from .execution import EXPORT_SKIP_DIRS, read_junit_cases, run_command
from .skill_contracts import extra_import_roots
from .stack import REQUIRED_TOOLS, StackProfile

REPORT_DIR = ".harness-reports"
VENV_DIR = ".harness-venv"


@dataclass
class Step:
    name: str
    argv: list[str]
    kind: str = "test"  # setup | test
    cwd: str = "."  # relative to workspace
    timeout: float = 900
    env: dict[str, str] = field(default_factory=dict)
    parser: str = ""  # junit | jest | go | trx | ""
    report_glob: str = ""  # relative to workspace, for junit/jest/trx
    continue_on_failure: bool = False


@dataclass
class RunPlan:
    adapter: str
    steps: list[Step]
    notes: list[str] = field(default_factory=list)
    blocked_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _playwright_browsers() -> dict[str, str]:
    """Browsers are cached under the real HOME, which command execution deliberately hides."""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return {"PLAYWRIGHT_BROWSERS_PATH": os.environ["PLAYWRIGHT_BROWSERS_PATH"]}
    candidate = platform_compat.playwright_cache_dir()
    return {"PLAYWRIGHT_BROWSERS_PATH": str(candidate)} if candidate else {}


# Third-party test helpers the python adapter may install (allow-list: never install arbitrary model-named packages).
PYTHON_TEST_HELPERS = {
    "pytest-mock": (r"\bmocker\b", r"pytest_mock"),
    "pytest-asyncio": (r"pytest\.mark\.asyncio", r"pytest_asyncio"),
    "requests-mock": (r"requests_mock",),
    "responses": (r"^\s*(?:import|from)\s+responses\b",),
    "freezegun": (r"freezegun",),
    "hypothesis": (r"^\s*(?:import|from)\s+hypothesis\b",),
}


def python_test_helpers(files: list[dict]) -> list[str]:
    text = "\n".join(f["content"] for f in files)
    return [
        pkg
        for pkg, patterns in PYTHON_TEST_HELPERS.items()
        if any(re.search(pattern, text, re.M) for pattern in patterns)
    ]


def _python_import_roots(ws: Path) -> list[str]:
    """Import roots for modules living in directories that are not valid package names (see skill_contracts).

    Shares its noise-directory list with EXPORT_SKIP_DIRS (execution.py): a directory that must never be
    copied into a deliverable (a stray tool download, a venv) must equally never be walked here -- letting
    its files leak into PYTHONPATH can shadow the interpreter's own stdlib with a mismatched one.
    """
    found = []
    for folder, dirs, names in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in EXPORT_SKIP_DIRS and not d.startswith("%")]
        found += [(Path(folder) / n).relative_to(ws).as_posix() for n in names if n.endswith(".py")]
    return extra_import_roots(found)


def _repo_has_pytest_config(ws: Path) -> bool:
    def contains(name: str, needle: str) -> bool:
        path = ws / name
        return path.is_file() and needle in path.read_text(encoding="utf-8", errors="replace")

    return (
        (ws / "pytest.ini").is_file()
        or contains("pyproject.toml", "[tool.pytest")
        or contains("tox.ini", "[pytest]")
        or contains("setup.cfg", "[tool:pytest]")
    )


def _paths(files: list[dict], suffixes: tuple[str, ...] | None = None) -> list[str]:
    return [f["path"] for f in files if not suffixes or PurePosixPath(f["path"]).suffix.lower() in suffixes]


def _java_class(path: str) -> str:
    return PurePosixPath(path).stem


def resolve_ecosystem(stack: StackProfile, files: list[dict] | None = None) -> str:
    """The ecosystem to provision/run: generated file types win over the repo's primary one (e.g. specs in Java)."""
    exts = {PurePosixPath(f["path"]).suffix.lower() for f in files or []}
    if not exts:
        return stack.primary
    for eco, suffixes in (
        ("python", {".py"}),
        ("node", {".js", ".jsx", ".ts", ".tsx", ".mjs"}),
        ("maven", {".java", ".kt"}),
        ("go", {".go"}),
        ("dotnet", {".cs"}),
    ):
        if exts <= suffixes:
            return "gradle" if eco == "maven" and stack.ecosystems[:1] == ["gradle"] else eco
    return stack.primary


def setup_steps(
    eco: str,
    ws: Path,
    cache: str,
    timeout: float,
    *,
    helpers: list[str] | None = None,
    need_playwright: bool = False,
    env_ready: bool = False,
    extra_requirements: list[str] | None = None,
    extra_packages: list[str] | None = None,
) -> list[Step]:
    """Dependency installation into an ISOLATED location (venv / project node_modules / cache-dir repositories).

    With ``env_ready`` the repository dependencies are already installed; only per-test extras remain.
    """
    helpers = helpers or []
    steps: list[Step] = []
    if eco == "python":
        # Absolute, not relative to VENV_DIR alone: a relative argv[0] resolves against the calling
        # process's own directory on Windows (CreateProcess does not consult the child's cwd for exe
        # lookup, unlike POSIX fork+chdir+exec), so it silently fails to find the interpreter when the
        # workspace differs from the harness's own process directory.
        venv_py = platform_compat.venv_python(ws / VENV_DIR)
        pip = {"PIP_CACHE_DIR": platform_compat.cache_path(cache, "pip")}
        pip_cmd = [venv_py, "-m", "pip", "install", "--disable-pip-version-check", "-q"]
        if not env_ready:
            steps += [
                # sys.executable (the interpreter already running the harness), never a bare "python3"/"python":
                # on Windows those names can resolve to the Python Install Manager's App Execution Alias, which
                # silently downloads and installs a whole interpreter over the network instead of erroring.
                Step("create-venv", [sys.executable, "-m", "venv", VENV_DIR], "setup", timeout=timeout),
                Step("install-pytest", [*pip_cmd, "pytest"], "setup", env=pip, timeout=timeout),
            ]
            if (ws / "requirements.txt").is_file():
                steps.append(
                    Step(
                        "install-requirements",
                        [*pip_cmd, "-r", "requirements.txt"],
                        "setup",
                        env=pip,
                        timeout=timeout,
                        continue_on_failure=True,
                    )
                )
            elif (ws / "pyproject.toml").is_file() or (ws / "setup.py").is_file():
                steps.append(
                    Step(
                        "install-project",
                        [*pip_cmd, "-e", "."],
                        "setup",
                        env=pip,
                        timeout=timeout,
                        continue_on_failure=True,
                    )
                )
            for rel in extra_requirements or []:
                steps.append(
                    Step(
                        f"install-{rel.replace('/', '_')}",
                        [*pip_cmd, "-r", rel],
                        "setup",
                        env=pip,
                        timeout=timeout,
                        continue_on_failure=True,
                    )
                )
            if extra_packages:
                steps.append(
                    Step(
                        "install-documented-packages",
                        [*pip_cmd, *extra_packages],
                        "setup",
                        env=pip,
                        timeout=timeout,
                        continue_on_failure=True,
                    )
                )
        if helpers:
            steps.append(
                Step("install-test-helpers", [*pip_cmd, *helpers], "setup", env=pip, timeout=timeout)
            )
    elif eco == "node":
        npm = {"npm_config_cache": platform_compat.cache_path(cache, "npm")}
        if not env_ready:
            command = "ci" if (ws / "package-lock.json").is_file() else "install"
            steps.append(
                Step(
                    "npm-install",
                    [
                        *platform_compat.resolve_tool("npm"),
                        command,
                        "--no-audit",
                        "--no-fund",
                        "--ignore-scripts",
                    ],
                    "setup",
                    env=npm,
                    timeout=timeout,
                )
            )
        if need_playwright:
            steps += [
                Step(
                    "install-playwright",
                    [
                        *platform_compat.resolve_tool("npm"),
                        "install",
                        "--no-save",
                        "--no-audit",
                        "--no-fund",
                        "@playwright/test",
                    ],
                    "setup",
                    env=npm,
                    timeout=timeout,
                ),
                Step(
                    "install-browser",
                    [
                        *platform_compat.resolve_tool("npx"),
                        "--no-install",
                        "playwright",
                        "install",
                        "chromium",
                    ],
                    "setup",
                    env=_playwright_browsers(),
                    timeout=timeout,
                    continue_on_failure=True,
                ),
            ]
    elif not env_ready:
        if eco == "maven":
            mvn = platform_compat.resolve_wrapper(ws, "mvnw", "mvnw.cmd", "mvn")
            steps.append(
                Step(
                    "resolve-dependencies",
                    [
                        *mvn,
                        "-B",
                        "-ntp",
                        f"-Dmaven.repo.local={platform_compat.cache_path(cache, 'm2')}",
                        "dependency:go-offline",
                    ],
                    "setup",
                    timeout=timeout,
                    continue_on_failure=True,
                )
            )
        elif eco == "gradle":
            gradle = platform_compat.resolve_wrapper(ws, "gradlew", "gradlew.bat", "gradle")
            steps.append(
                Step(
                    "resolve-dependencies",
                    [*gradle, "--no-daemon", "dependencies"],
                    "setup",
                    env={"GRADLE_USER_HOME": platform_compat.cache_path(cache, "gradle")},
                    timeout=timeout,
                    continue_on_failure=True,
                )
            )
        elif eco == "go":
            steps.append(
                Step(
                    "download-modules",
                    ["go", "mod", "download"],
                    "setup",
                    env={
                        "GOMODCACHE": platform_compat.cache_path(cache, "go-mod"),
                        "GOFLAGS": "-mod=mod",
                    },
                    timeout=timeout,
                    continue_on_failure=True,
                )
            )
        elif eco == "dotnet":
            steps.append(
                Step("restore", ["dotnet", "restore"], "setup", timeout=timeout, continue_on_failure=True)
            )
    return steps


def build_plan(
    stack: StackProfile,
    workspace: Path,
    files: list[dict],
    *,
    cache_dir: Path,
    timeout: float = 900,
    env_ready: bool = False,
) -> RunPlan:
    """Choose the adapter for the generated tests: (remaining) setup steps, then the test step."""
    ws = Path(workspace)
    eco = resolve_ecosystem(stack, files)
    cache = str(Path(cache_dir).resolve())
    reports = ws / REPORT_DIR
    missing = [t for t in REQUIRED_TOOLS.get(eco, ()) if not platform_compat.tool_present(t)]
    if missing and not (eco == "maven" and (ws / "mvnw").exists()):
        return RunPlan(
            eco, [], blocked_reason=f"Required tool(s) not installed on the host: {', '.join(missing)}"
        )

    if eco == "python":
        # The workspace may live under the harness; without its own pytest config, pytest would walk up and pick the
        # harness's config. Pin the rootdir with an empty config unless the repository ships its own.
        isolate: list[str] = []
        if not _repo_has_pytest_config(ws):
            reports.mkdir(exist_ok=True)
            (reports / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            isolate = ["-c", f"{REPORT_DIR}/pytest.ini", "--rootdir", "."]
        steps = setup_steps(eco, ws, cache, timeout, helpers=python_test_helpers(files), env_ready=env_ready)
        steps.append(
            Step(
                "pytest",
                [
                    platform_compat.venv_python(ws / VENV_DIR),
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                    "-q",
                    *isolate,
                    "--continue-on-collection-errors",
                    "--import-mode=importlib",
                    f"--junitxml={REPORT_DIR}/junit.xml",
                    *_paths(files, (".py",)),
                ],
                "test",
                env={
                    "PYTHONPATH": platform_compat.pythonpath_join(
                        [str(ws), str(ws / "src"), *(str(ws / r) for r in _python_import_roots(ws))]
                    )
                },
                timeout=timeout,
                parser="junit",
                report_glob=f"{REPORT_DIR}/junit.xml",
            )
        )
        return RunPlan("python-pytest", steps)

    if eco == "node":
        if not (ws / "package.json").is_file():  # e.g. Playwright specs added to a Java/Python repository
            (ws / "package.json").write_text(
                '{"private": true, "name": "generated-tests"}\n', encoding="utf-8"
            )
        uses_playwright = any("@playwright/test" in f["content"] for f in files)
        runner = "playwright" if uses_playwright else stack.test_runner or "jest"
        steps = setup_steps(
            eco, ws, cache, timeout, env_ready=env_ready, need_playwright=runner == "playwright"
        )
        if runner == "playwright" and stack.test_runner == "playwright":
            steps = [st for st in steps if st.name != "install-playwright"]  # repo already depends on it
        paths = _paths(files)
        if runner == "playwright":
            steps.append(
                Step(
                    "playwright",
                    [
                        *platform_compat.resolve_tool("npx"),
                        "--no-install",
                        "playwright",
                        "test",
                        *paths,
                        "--reporter=junit",
                    ],
                    "test",
                    parser="junit",
                    report_glob=f"{REPORT_DIR}/junit.xml",
                    timeout=timeout,
                    env={
                        "PLAYWRIGHT_JUNIT_OUTPUT_NAME": str(reports / "junit.xml"),
                        **_playwright_browsers(),
                    },
                )
            )
        elif runner == "vitest":
            steps.append(
                Step(
                    "vitest",
                    [
                        *platform_compat.resolve_tool("npx"),
                        "--no-install",
                        "vitest",
                        "run",
                        *paths,
                        "--reporter=junit",
                        f"--outputFile={REPORT_DIR}/junit.xml",
                    ],
                    "test",
                    parser="junit",
                    report_glob=f"{REPORT_DIR}/junit.xml",
                    timeout=timeout,
                )
            )
        else:
            steps.append(
                Step(
                    "jest",
                    [
                        *platform_compat.resolve_tool("npx"),
                        "--no-install",
                        "jest",
                        "--ci",
                        *paths,
                        "--json",
                        f"--outputFile={REPORT_DIR}/jest.json",
                    ],
                    "test",
                    parser="jest",
                    report_glob=f"{REPORT_DIR}/jest.json",
                    timeout=timeout,
                )
            )
        return RunPlan(f"node-{runner}", steps)

    steps = setup_steps(eco, ws, cache, timeout, env_ready=env_ready)
    if eco == "maven":
        mvn = platform_compat.resolve_wrapper(ws, "mvnw", "mvnw.cmd", "mvn")
        classes = ",".join(_java_class(p) for p in _paths(files, (".java",)))
        steps.append(
            Step(
                "mvn-test",
                [
                    *mvn,
                    "-B",
                    "-ntp",
                    f"-Dmaven.repo.local={platform_compat.cache_path(cache, 'm2')}",
                    "test",
                    f"-Dtest={classes}",
                    "-DfailIfNoTests=false",
                    "-Dsurefire.failIfNoSpecifiedTests=false",
                ],
                "test",
                parser="junit",
                report_glob="**/target/surefire-reports/TEST-*.xml",
                timeout=timeout,
                env={"MAVEN_OPTS": "-Xmx1g"},
            )
        )
        return RunPlan("java-maven", steps)
    if eco == "gradle":
        gradle = platform_compat.resolve_wrapper(ws, "gradlew", "gradlew.bat", "gradle")
        filters = [arg for p in _paths(files, (".java", ".kt")) for arg in ("--tests", _java_class(p))]
        steps.append(
            Step(
                "gradle-test",
                [*gradle, "test", "--no-daemon", *filters],
                "test",
                parser="junit",
                report_glob="**/build/test-results/test/*.xml",
                timeout=timeout,
                env={"GRADLE_USER_HOME": platform_compat.cache_path(cache, "gradle")},
            )
        )
        return RunPlan("java-gradle", steps)
    if eco == "go":
        packages = sorted(
            {
                f"./{PurePosixPath(p).parent.as_posix()}"
                if PurePosixPath(p).parent.as_posix() != "."
                else "."
                for p in _paths(files, (".go",))
            }
        )
        steps.append(
            Step(
                "go-test",
                ["go", "test", "-json", "-count=1", *packages],
                "test",
                parser="go",
                timeout=timeout,
                env={
                    "GOCACHE": platform_compat.cache_path(cache, "go-build"),
                    "GOMODCACHE": platform_compat.cache_path(cache, "go-mod"),
                    "GOFLAGS": "-mod=mod",
                },
            )
        )
        return RunPlan("go-test", steps)
    if eco == "dotnet":
        steps.append(
            Step(
                "dotnet-test",
                [
                    "dotnet",
                    "test",
                    "--logger",
                    "trx;LogFileName=results.trx",
                    "--results-directory",
                    str(reports),
                ],
                "test",
                parser="trx",
                report_glob=f"{REPORT_DIR}/*.trx",
                timeout=timeout,
            )
        )
        return RunPlan("dotnet-test", steps)
    return RunPlan(eco, [], blocked_reason=f"No runner adapter for ecosystem '{eco}' yet.")


def execute_setup(steps: list[Step], workspace: Path, out_dir: Path) -> dict:
    """Run installation steps only; ``ready`` means every non-optional step succeeded."""
    workspace, out_dir = Path(workspace).resolve(), Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    report: dict = {
        "status": "ready",
        "steps": [],
        "warnings": [],
        "isolation": "local-process-not-security-sandbox",
    }
    for index, step in enumerate(steps, 1):
        result = run_command(
            step.argv,
            (workspace / step.cwd).resolve(),
            out_dir / f"{index:02d}-{step.name}",
            timeout=step.timeout,
            env=_expand_env(step.env, workspace),
            inherit_env=True,
        )
        status = result["status"]
        if (
            step.name == "create-venv"
            and status in {"command_succeeded", "passed"}
            and not Path(platform_compat.venv_python(workspace / VENV_DIR)).exists()
        ):
            # A zero exit code alone does not prove a venv was created (e.g. Windows can silently redirect
            # this invocation through an App Execution Alias / install-manager stub); check the artifact too.
            status = "blocked"
        report["steps"].append(
            {
                "name": step.name,
                "argv": step.argv,
                "status": status,
                "exit_code": result.get("exit_code"),
                "duration_seconds": result.get("duration_seconds"),
                "stdout": str(out_dir / f"{index:02d}-{step.name}" / "stdout.log"),
            }
        )
        if status not in {"command_succeeded", "passed"}:
            if step.continue_on_failure:
                report["warnings"].append(f"optional step '{step.name}' failed ({status})")
            else:
                report.update(status="setup_failed", error=f"Step '{step.name}' failed: {status}")
                break
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------- result parsers
def parse_jest_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for suite in data.get("testResults", []):
        for test in suite.get("assertionResults", []):
            status = {
                "passed": "passed",
                "failed": "failed",
                "pending": "skipped",
                "skipped": "skipped",
                "todo": "skipped",
            }.get(test.get("status"), "failed")
            cases.append(
                {
                    "name": test.get("title", ""),
                    "classname": suite.get("name", ""),
                    "status": status,
                    "message": "\n".join(test.get("failureMessages") or [])[:1500],
                }
            )
    return cases


def parse_go_json(stdout: Path) -> list[dict]:
    cases: dict[str, dict] = {}
    for line in stdout.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        test = event.get("Test")
        if not test:
            continue
        entry = cases.setdefault(
            f"{event.get('Package')}::{test}",
            {"name": test, "classname": event.get("Package", ""), "status": "failed", "message": ""},
        )
        if event.get("Action") == "output":
            entry["message"] = (entry["message"] + event.get("Output", ""))[-1500:]
        elif event.get("Action") in {"pass", "fail", "skip"}:
            entry["status"] = {"pass": "passed", "fail": "failed", "skip": "skipped"}[event["Action"]]
    return list(cases.values())


def parse_trx(path: Path) -> list[dict]:
    root = ET.parse(path).getroot()
    cases = []
    for result in root.iter():
        if result.tag.endswith("UnitTestResult"):
            outcome = result.get("outcome", "")
            message = next((el.text or "" for el in result.iter() if el.tag.endswith("Message")), "")
            cases.append(
                {
                    "name": result.get("testName", ""),
                    "classname": "",
                    "message": message[:1500],
                    "status": "passed"
                    if outcome == "Passed"
                    else "skipped"
                    if outcome in {"NotExecuted", "Inconclusive"}
                    else "failed",
                }
            )
    return cases


def summarize(cases: list[dict]) -> dict[str, int]:
    counts = {"tests": len(cases), "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for case in cases:
        key = {"error": "errors"}.get(case["status"], case["status"])
        counts[key] = counts.get(key, 0) + 1
    return counts


def _step_cases(step: Step, workspace: Path, result: dict, started: float) -> list[dict]:
    """Fresh (post-start) report files only, so stale reports can never masquerade as this run."""
    if step.parser == "go":
        return parse_go_json(Path(result["stdout_path"]))
    reports = sorted(
        p for p in workspace.glob(step.report_glob) if p.is_file() and p.stat().st_mtime >= started
    )
    if not reports:
        return []
    if step.parser == "jest":
        return [c for p in reports for c in parse_jest_json(p)]
    if step.parser == "trx":
        return [c for p in reports for c in parse_trx(p)]
    return read_junit_cases(reports)


def _expand_env(env: dict[str, str], workspace: Path) -> dict[str, str]:
    return {k: v.replace("{ws}", str(workspace)) for k, v in env.items()}


_DIAGNOSTIC_LINE = re.compile(r"error|cannot find|does not exist|fail|exception|denied", re.I)
MAX_QUARANTINE_ROUNDS = 3


def _decisive_lines(entry: dict) -> list[str]:
    """Error-looking lines of a failed step: the only evidence when the build broke before any test ran."""
    stdout = Path(entry["stdout"])
    text = stdout.read_text(encoding="utf-8", errors="replace")
    text += "\n" + stdout.with_name("stderr.log").read_text(encoding="utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if _DIAGNOSTIC_LINE.search(line)]


def _compile_failed_files(diagnostics: list[str], generated_paths: list[str]) -> list[str]:
    by_name = {PurePosixPath(p).name: p for p in generated_paths}
    hit = {
        by_name[m.group(1)]
        for line in diagnostics
        for m in [re.search(r"([\w$-]+\.(?:java|kt|cs))\b", line)]
        if m and m.group(1) in by_name
    }
    return sorted(hit)


def _quarantine(workspace: Path, paths: list[str]) -> None:
    for rel in paths:
        target = workspace / ".harness-quarantine" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        (workspace / rel).replace(target)


def _run_test_step(step: Step, workspace: Path, out_dir: Path, label: str) -> tuple[dict, list[dict]]:
    started = time.time()
    result = run_command(
        step.argv,
        (workspace / step.cwd).resolve(),
        out_dir / label,
        timeout=step.timeout,
        env=_expand_env(step.env, workspace),
        inherit_env=True,
    )
    stdout = str(out_dir / label / "stdout.log")
    result["stdout_path"] = stdout
    entry = {
        "name": step.name,
        "kind": step.kind,
        "argv": step.argv,
        "status": result["status"],
        "exit_code": result.get("exit_code"),
        "duration_seconds": result.get("duration_seconds"),
        "stdout": stdout,
    }
    try:
        cases = _step_cases(step, workspace, result, started)
    except (ET.ParseError, OSError, ValueError, json.JSONDecodeError) as exc:
        cases, entry["parse_error"] = [], str(exc)
    entry["summary"] = summarize(cases)
    return entry, cases


def execute_plan(
    plan: RunPlan, workspace: Path, out_dir: Path, generated_paths: list[str] | None = None
) -> dict:
    """Run every step; return an aggregate report. Never reports success without fresh test evidence.

    Compiled languages fail as a whole when one generated file does not compile. When the build breaks before any
    test ran, the offending generated files (named in the compiler output) are quarantined and the build is retried,
    so the remaining tests still produce evidence; quarantined cases are reported as errors with the compiler lines.
    """
    workspace, out_dir = Path(workspace).resolve(), Path(out_dir).resolve()
    (workspace / REPORT_DIR).mkdir(exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=False)
    report: dict = {
        "adapter": plan.adapter,
        "workspace": str(workspace),
        "steps": [],
        "cases": [],
        "status": "no_test_evidence",
        "isolation": "local-process-not-security-sandbox",
    }
    if plan.blocked_reason:
        report.update(status="blocked", error=plan.blocked_reason)
        return report
    setup_failed = False
    diagnostics: list[str] = []
    for index, step in enumerate(plan.steps, 1):
        label = f"{index:02d}-{step.name}"
        if step.kind == "test":
            entry, cases = _run_test_step(step, workspace, out_dir, label)
            for retry in range(1, MAX_QUARANTINE_ROUNDS + 1):
                if cases or entry["status"] in {"command_succeeded", "passed"} or not generated_paths:
                    break
                lines = _decisive_lines(entry)
                diagnostics += [ln for ln in lines if ln not in diagnostics]
                bad = [
                    p
                    for p in _compile_failed_files(lines, generated_paths)
                    if p not in report.get("quarantined", []) and (workspace / p).exists()
                ]
                if not bad or len(bad) + len(report.get("quarantined", [])) >= len(generated_paths):
                    break
                _quarantine(workspace, bad)
                report.setdefault("quarantined", []).extend(bad)
                report["steps"].append(entry)  # keep the failed build as evidence
                entry, cases = _run_test_step(step, workspace, out_dir, f"{label}-retry{retry}")
            report["steps"].append(entry)
            report["cases"].extend(cases)
            continue
        result = run_command(
            step.argv,
            (workspace / step.cwd).resolve(),
            out_dir / label,
            timeout=step.timeout,
            env=_expand_env(step.env, workspace),
            inherit_env=True,
        )
        report["steps"].append(
            {
                "name": step.name,
                "kind": step.kind,
                "argv": step.argv,
                "status": result["status"],
                "exit_code": result.get("exit_code"),
                "duration_seconds": result.get("duration_seconds"),
                "stdout": str(out_dir / label / "stdout.log"),
            }
        )
        if result["status"] not in {"command_succeeded", "passed"}:
            if step.continue_on_failure:
                report.setdefault("warnings", []).append(
                    f"setup step '{step.name}' failed ({result['status']})"
                )
            else:
                setup_failed = True
                report.update(
                    status="setup_failed", error=f"Setup step '{step.name}' failed: {result['status']}"
                )
                break
    if not setup_failed:
        if not report["cases"] or report.get("quarantined"):
            last = next((e for e in reversed(report["steps"]) if e["kind"] == "test"), None)
            if last and last["status"] not in {"command_succeeded", "passed"} and not report["cases"]:
                diagnostics += [ln for ln in _decisive_lines(last) if ln not in diagnostics]
            report["diagnostics"] = diagnostics[:400]
        counts = summarize(report["cases"])
        report["summary"] = counts
        if counts["tests"] == 0:
            report["status"] = "no_test_evidence"
        elif counts["failed"] or counts["errors"] or report.get("quarantined"):
            report["status"] = "failed"
        elif counts["passed"] == 0:
            report["status"] = "no_test_evidence"
        else:
            report["status"] = "passed_with_skips" if counts["skipped"] else "passed"
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


_NAME_PATTERNS = {
    ".py": r"def\s+(test_[A-Za-z0-9_]+)\s*\(",
    ".java": r"@Test[^\n]*\n(?:\s*@[^\n]+\n)*\s*(?:public\s+|protected\s+)?void\s+(\w+)\s*\(",
    ".kt": r"@Test[^\n]*\n\s*fun\s+(\w+)\s*\(",
    ".go": r"func\s+(Test\w+)\s*\(",
    ".cs": r"\[(?:Fact|Test|TestMethod|Theory)[^\]]*\]\s*(?:public\s+)?(?:async\s+)?(?:Task|void)\s+(\w+)\s*\(",
}


def declared_test_name(path: str, content: str) -> str | None:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in _NAME_PATTERNS:
        m = re.search(_NAME_PATTERNS[suffix], content)
        return m.group(1) if m else None
    m = re.search(r"""\b(?:test|it)\s*\(\s*(['"`])(.+?)\1""", content)
    return m.group(2) if m else None


def map_results_to_cases(cases: list[dict], files: list[dict]) -> list[dict]:
    """Attach the approved case ID to each executed test (one file per case, one test per file).

    Matching order: declared test name, then the file stem in the report's class/module name. Collection and compile
    errors are reported by the runners with the *dotted module path* as the name and no class, so the stem is also
    matched against the name; otherwise those failures lose their case and their message.
    """
    by_name: dict[str, str] = {}
    by_stem: dict[str, str] = {}
    for f in files:
        case_id = f["case_ids"][0]
        name = declared_test_name(f["path"], f["content"])
        if name:
            by_name[name] = case_id
        by_stem[PurePosixPath(f["path"]).stem] = case_id
    mapped = []
    for case in cases:
        label = case.get("classname") or ""
        case_id = by_name.get(case["name"]) or next(
            (
                cid
                for stem, cid in by_stem.items()
                if stem and (stem in label or case["name"] == stem or case["name"].endswith("." + stem))
            ),
            None,
        )
        mapped.append({**case, "case_id": case_id})
    return mapped

"""Per-ecosystem runner adapters: turn a StackProfile + generated files into an explicit, reviewable plan.

The host, never a model, chooses every command. The plan is shown to the human before anything runs.
Execution is local-process (not a security sandbox); see execution.run_command.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath

from .. import errors
from ..repo.stack import REQUIRED_TOOLS, StackProfile
from ..skills.contracts import extra_import_roots
from . import platform_compat
from .execution import EXPORT_SKIP_DIRS, read_junit_cases, run_command
from .regression import maven_regression_steps

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
    scope: str = "generated"


@dataclass
class RunPlan:
    adapter: str
    steps: list[Step]
    notes: list[str] = field(default_factory=list)
    blocked_reason: str | None = None
    app: dict | None = None  # how to start the application under test (E2E), see app_launcher
    scope_expectations: dict = field(default_factory=dict)

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
    "httpx": (r"(?:starlette|fastapi)\.testclient", r"^\s*(?:import|from)\s+httpx\b"),  # TestClient needs it
    "respx": (r"\brespx\b",),
}
_DEP_FILES = ("requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "tox.ini")


def _declared(ws: Path, pkg: str) -> bool:
    """Whether the repository's own dependency files already name ``pkg`` (its pins are never touched)."""
    norm = re.compile(rf"(?<![\w-]){re.escape(pkg).replace('-', '[-_.]')}(?![\w-])", re.I)
    return any(
        norm.search(f.read_text(encoding="utf-8", errors="replace"))
        for pattern in _DEP_FILES
        for f in ws.glob(pattern)
        if f.is_file()
    )


def python_test_helpers(files: list[dict], ws: Path | None = None) -> list[str]:
    text = "\n".join(f["content"] for f in files)
    return [
        pkg
        for pkg, patterns in PYTHON_TEST_HELPERS.items()
        if any(re.search(pattern, text, re.M) for pattern in patterns) and not (ws and _declared(ws, pkg))
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


def repository_components(stack: StackProfile, workspace: Path) -> list[tuple[str, str]]:
    """Manifest-backed install locations; nested Python requirements share the root venv."""
    manifests = {
        "package.json": "node",
        "pom.xml": "maven",
        "build.gradle": "gradle",
        "build.gradle.kts": "gradle",
        "go.mod": "go",
    }
    components = []
    if "python" in stack.ecosystems or stack.primary == "python":
        components.append(("python", "."))
    for rel in stack.build_files:
        path = PurePosixPath(rel)
        eco = manifests.get(path.name)
        target = (workspace / rel).resolve()
        if eco and target.is_relative_to(workspace.resolve()) and target.is_file():
            item = (eco, path.parent.as_posix())
            if item not in components:
                components.append(item)
    return components or [(stack.primary, ".")]


def generated_groups(
    stack: StackProfile, workspace: Path, files: list[dict]
) -> dict[tuple[str, str], list[dict]]:
    groups = {}
    components = repository_components(stack, workspace)
    for file in files:
        eco = resolve_ecosystem(stack, [file])
        candidates = [
            cwd
            for kind, cwd in components
            if kind == eco and (cwd == "." or file["path"].startswith(cwd + "/"))
        ]
        cwd = max(candidates, key=len) if candidates else "."
        groups.setdefault((eco, cwd), []).append(file)
    return groups


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
                Step("install-test-tooling", [*pip_cmd, *helpers], "setup", env=pip, timeout=timeout)
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
                        "--ignore-scripts",
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
    assume_present: frozenset[str] = frozenset(),
    ready_components: frozenset[tuple[str, str]] | None = None,
    _grouped: bool = False,
) -> RunPlan:
    """Choose the adapter for the generated tests: (remaining) setup steps, then the test step.

    ``assume_present`` names required tools a provisioned toolchain covers, so they are not reported missing.
    """
    ws = Path(workspace)
    groups = generated_groups(stack, ws, files)
    if not _grouped and (len(groups) > 1 or any(cwd != "." for _, cwd in groups)):
        from ..repo.stack import detect_stack

        steps, notes, blocked = [], [], []
        for (kind, cwd), members in groups.items():
            root = (ws / cwd).resolve()
            local = detect_stack(root)
            local = replace(local, primary=kind, ecosystems=[kind])
            relative = [
                {**f, "path": str(PurePosixPath(f["path"]).relative_to(cwd)) if cwd != "." else f["path"]}
                for f in members
            ]
            child = build_plan(
                local,
                root,
                relative,
                cache_dir=cache_dir,
                timeout=timeout,
                env_ready=(kind, cwd) in (ready_components or frozenset())
                if ready_components is not None
                else env_ready and kind == stack.primary,
                assume_present=assume_present,
                _grouped=True,
            )
            if child.blocked_reason:
                blocked.append(f"{kind} {cwd}: {child.blocked_reason}")
            for step in child.steps:
                step.cwd = (PurePosixPath(cwd) / step.cwd).as_posix()
                if step.report_glob and cwd != ".":
                    step.report_glob = (PurePosixPath(cwd) / step.report_glob).as_posix()
                steps.append(step)
            notes.append(f"{kind} component: {cwd} ({len(members)} generated files)")
        return RunPlan("multi-component", steps, notes=notes, blocked_reason="; ".join(blocked) or None)
    eco = resolve_ecosystem(stack, files)
    if ready_components is not None:
        env_ready = (eco, ".") in ready_components
    cache = str(Path(cache_dir).resolve())
    reports = ws / REPORT_DIR
    missing = [
        t
        for t in REQUIRED_TOOLS.get(eco, ())
        if t not in assume_present and not platform_compat.tool_present(t)
    ]
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
        steps = setup_steps(
            eco, ws, cache, timeout, helpers=python_test_helpers(files, ws), env_ready=env_ready
        )
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
                    f"--junitxml={REPORT_DIR}/pytest-results.xml",
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
                report_glob=f"{REPORT_DIR}/pytest-results.xml",
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
            config_name = ".harness-playwright-report.config.ts"
            original = next(
                (
                    name
                    for name in (
                        "playwright.config.ts",
                        "playwright.config.js",
                        "playwright.config.mts",
                        "playwright.config.mjs",
                        "playwright.config.cts",
                        "playwright.config.cjs",
                    )
                    if (ws / name).is_file()
                ),
                None,
            )
            # Keep the wrapper beside the original so its relative paths retain their meaning.
            config = (f"import original from './{original}';\n" if original else "const original = {};\n") + (
                "const capture = { screenshot: { mode: 'only-on-failure', fullPage: true }, trace: 'retain-on-failure', baseURL: process.env.BASE_URL || original.use?.baseURL };\n"
                "export default { ...original, use: { ...original.use, ...capture }, "
                "projects: original.projects?.map(project => ({ ...project, use: { ...project.use, ...capture } })) };\n"
            )
            config_path = ws / config_name
            legacy_config = config.replace(", baseURL: process.env.BASE_URL || original.use?.baseURL", "")
            if config_path.exists() and config_path.read_text(encoding="utf-8") not in {
                config,
                legacy_config,
            }:
                raise ValueError(f"Refusing to overwrite existing {config_name}")
            config_path.write_text(config, encoding="utf-8")
            steps.append(
                Step(
                    "playwright",
                    [
                        *platform_compat.resolve_tool("npx"),
                        "--no-install",
                        "playwright",
                        "test",
                        *paths,
                        "--reporter=junit,html",
                        f"--config={config_name}",
                        f"--output={REPORT_DIR}/test-results",
                    ],
                    "test",
                    parser="junit",
                    report_glob=f"{REPORT_DIR}/junit.xml",
                    timeout=timeout,
                    env={
                        "PLAYWRIGHT_JUNIT_OUTPUT_NAME": str(reports / "junit.xml"),
                        "PLAYWRIGHT_HTML_OUTPUT_DIR": str(reports / "playwright-report"),
                        "PLAYWRIGHT_HTML_OPEN": "never",
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
    if eco in {"maven", "gradle"}:
        provider_calls = any(
            re.search(
                r"\bValidation\.(?:buildDefaultValidatorFactory|byDefaultProvider|byProvider)\s*\(",
                file["content"],
            )
            for file in files
        )
        manifests = "\n".join(
            (ws / name).read_text(errors="replace")
            for name in ("pom.xml", "build.gradle", "build.gradle.kts")
            if (ws / name).is_file()
        )
        if provider_calls and not re.search(r"hibernate-validator|bval-jsr", manifests):
            return RunPlan(
                "java-" + eco,
                [],
                blocked_reason="Standalone Bean Validation needs a runtime provider (hibernate-validator or bval-jsr), not only the Java EE API. Declare and approve a test-runtime provider or revise unsupported standalone cases.",
            )
    if eco == "maven":
        mvn = platform_compat.resolve_wrapper(ws, "mvnw", "mvnw.cmd", "mvn")
        regression, expectations, notes = maven_regression_steps(
            ws, files, mvn, platform_compat.cache_path(cache, "m2"), timeout
        )
        steps.extend(Step(**item) for item in regression)
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
                    "-DfailIfNoTests=true",
                    "-Dsurefire.failIfNoSpecifiedTests=true",
                ],
                "test",
                parser="junit",
                report_glob="**/target/surefire-reports/TEST-*.xml",
                timeout=timeout,
                env={"MAVEN_OPTS": "-Xmx1g"},
            )
        )
        return RunPlan("java-maven", steps, notes=notes, scope_expectations=expectations)
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


MAX_RECOVERIES = 4  # automatic recovery actions per setup run, across all steps


def execute_setup(
    steps: list[Step],
    workspace: Path,
    out_dir: Path,
    *,
    recover: bool = True,
    allow_downloads: bool = False,
) -> dict:
    """Run installation steps only; ``ready`` means every non-optional step succeeded.

    A failed required step is diagnosed (see ``errors/``); when the playbook allows an automatic recipe (see
    ``recovery.py``) it is applied and the step runs again, at most ``MAX_RECOVERIES`` times per run and once per recipe
    per step. Every attempt keeps its own evidence directory and the report records what was tried and why.
    """
    from . import recovery  # imported here: recovery uses Step, which lives in this module

    workspace, out_dir = Path(workspace).resolve(), Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    steps = list(steps)
    report: dict = {
        "status": "ready",
        "steps": [],
        "warnings": [],
        "recoveries": [],
        "isolation": "local-process-not-security-sandbox",
    }
    tried: dict[str, list[str]] = {}
    runs: dict[str, int] = {}
    index = 0
    while index < len(steps):
        step = steps[index]
        runs[step.name] = runs.get(step.name, 0) + 1
        label = f"{index + 1:02d}-{step.name}" + (
            f"-retry{runs[step.name] - 1}" if runs[step.name] > 1 else ""
        )
        result = run_command(
            step.argv,
            (workspace / step.cwd).resolve(),
            out_dir / label,
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
        entry = {
            "name": step.name,
            "argv": step.argv,
            "status": status,
            "exit_code": result.get("exit_code"),
            "duration_seconds": result.get("duration_seconds"),
            "stdout": str(out_dir / label / "stdout.log"),
        }
        if runs[step.name] > 1:
            entry["attempt"] = runs[step.name]
        report["steps"].append(entry)
        if status in {"command_succeeded", "passed"}:
            index += 1
            continue
        diagnoses = _diagnose_step(result, out_dir / label, step.argv)
        if diagnoses:
            entry["diagnoses"] = [d.to_dict() for d in diagnoses[:3]]
        if step.continue_on_failure:
            report["warnings"].append(f"optional step '{step.name}' failed ({status})" + _why(diagnoses))
            items = _pip_items(step, workspace)
            if items:
                installed, failed = _install_each(step, items[0], items[1], workspace, out_dir, index + 1)
                entry["fallback"] = {"installed": installed, "failed": failed}
                if len(installed) > 1 and failed:  # one resolution for what installed, so pins hold together
                    entry["fallback"]["joint_reinstall"] = _joint_reinstall(
                        step, items[0], installed, workspace, out_dir, index + 1
                    )
                report["warnings"].append(
                    f"'{step.name}': pip installs are all-or-nothing, so one unbuildable package blocked "
                    f"the rest; retried one by one -> {len(installed)} installed, {len(failed)} failed"
                    + (f" ({', '.join(failed)})" if failed else "")
                    + ". A package that needs a C/C++ compiler (e.g. chroma-hnswlib) has no wheel on this "
                    "platform: install the build tools or expect tests importing it to fail."
                )
            index += 1
            continue
        action = None
        if recover and len(report["recoveries"]) < MAX_RECOVERIES:
            action, considered = recovery.choose(
                step, diagnoses, tried.setdefault(step.name, []), workspace, allow_downloads=allow_downloads
            )
            if considered:
                entry["recovery"] = considered
        if action is None:
            report.update(
                status="setup_failed", error=f"Step '{step.name}' failed: {status}" + _why(diagnoses)
            )
            break
        tried[step.name].append(action.recipe)
        report["recoveries"].append({"step": step.name, "recipe": action.recipe, "note": action.note})
        report["warnings"].append(
            f"step '{step.name}' failed ({diagnoses[0].code}); applied recovery '{action.recipe}': {action.note}"
        )
        if action.prepare:
            action.prepare()
        steps[index] = action.step
        if action.rewind_to:
            index = next((i for i, s in enumerate(steps) if s.name == action.rewind_to), index)
    if (
        recover
        and report["status"] == "ready"
        and any("pip" in s.argv and "install" in s.argv for s in steps)
    ):
        from . import env_check

        pip_steps = [s for s in steps if "pip" in s.argv and "install" in s.argv]
        declared_specs = [spec for s in pip_steps for spec in (_pip_items(s, workspace) or ([], []))[1]]
        conflicts = repair_conflicts(
            workspace, out_dir, declared_specs, env=_expand_env(pip_steps[0].env, workspace)
        )
        if conflicts["found"]:
            report["dependency_conflicts"] = conflicts
            fixed = not conflicts["remaining"]
            names = ", ".join(sorted({c["dependency"] for c in conflicts["found"]}))
            report["warnings"].append(
                f"pip check reported problems ({names}); "
                + (
                    "repaired by reinstalling each at the range its dependent package requires"
                    if fixed
                    else "still inconsistent after repair"
                )
                + (
                    ": " + "; ".join(env_check.describe_conflict(c) for c in conflicts["remaining"][:3])
                    if not fixed
                    else ""
                )
            )
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _ecosystem_of(argv: list[str]) -> str | None:
    """Which toolchain a command belongs to, from its executable name (used to pick the right error tables)."""
    exe = Path(argv[0]).name.lower() if argv else ""
    for names, eco in (
        (("python", "pip", "py", "uv"), "python"),
        (("npm", "npx", "node"), "node"),
        (("mvn", "mvnw", "gradle", "gradlew", "java"), "java"),
        (("dotnet",), "dotnet"),
        (("go",), "go"),
    ):
        if exe.split(".")[0] in names:
            return eco
    return None


def _diagnose_step(result: dict, step_dir: Path, argv: list[str] | None = None) -> list:
    """Known errors in a failed step's logs (see errors/): which code, whose fault, what to do."""
    text = ""
    for name in ("stdout.log", "stderr.log"):
        try:
            text += (step_dir / name).read_text(encoding="utf-8", errors="replace")[-20_000:] + chr(10)
        except OSError:
            pass
    return errors.classify_result(result, text, _ecosystem_of(argv or []))


def _why(diagnoses: list) -> str:
    return f" [{diagnoses[0].code}: {diagnoses[0].title}. {diagnoses[0].hint}]" if diagnoses else ""


def _pip_items(step: Step, workspace: Path) -> tuple[list[str], list[str]] | None:
    """(pip base argv, requirement specs) of a failed multi-package pip step, else None."""
    argv = step.argv
    if "pip" not in argv or "install" not in argv:
        return None
    if "-r" in argv:
        at = argv.index("-r")
        path = (workspace / step.cwd / argv[at + 1]).resolve()
        if not path.is_file() or not path.is_relative_to(workspace.resolve()):
            return None
        lines = (
            line.split(" #")[0].strip()
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        )
        return argv[:at], [line for line in lines if line and not line.startswith(("#", "-"))]
    base = argv[: argv.index("install") + 1]
    tail = [a for a in argv[len(base) :] if not a.startswith("-")]
    return (argv[: len(argv) - len(tail)], tail) if len(tail) > 1 else None


def _install_each(
    step: Step, base: list[str], specs: list[str], workspace: Path, out_dir: Path, index: int
) -> tuple[list[str], list[str]]:
    """Retry a failed bulk install package by package; only specs already in the approved step are installed."""
    installed: list[str] = []
    failed: list[str] = []
    for n, spec in enumerate(specs, 1):
        result = run_command(
            [*base, spec],
            (workspace / step.cwd).resolve(),
            out_dir / f"{index:02d}-{step.name}-each-{n:02d}",
            timeout=step.timeout,
            env=_expand_env(step.env, workspace),
            inherit_env=True,
        )
        (installed if result["status"] == "command_succeeded" else failed).append(spec)
    return installed, failed


def _joint_reinstall(
    step: Step, base: list[str], specs: list[str], workspace: Path, out_dir: Path, index: int
) -> str:
    """Install the specs that installed on their own in ONE pip run, so the resolver honours every pin together.

    Installing requirements one at a time lets a later package pull a newer dependency than an earlier pin allows
    (``streamlit`` bringing ``starlette 1.x`` next to ``fastapi==0.110.0``); a single resolution cannot.
    """
    result = run_command(
        [*base, *specs],
        (workspace / step.cwd).resolve(),
        out_dir / f"{index:02d}-{step.name}-joint",
        timeout=step.timeout,
        env=_expand_env(step.env, workspace),
        inherit_env=True,
    )
    return result["status"]


MAX_CONFLICT_ROUNDS = 2


def _spec_name(spec: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
    return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else ""


def repair_conflicts(
    workspace: Path,
    out_dir: Path,
    specs: list[str] | None = None,
    timeout: float = 600,
    env: dict[str, str] | None = None,
) -> dict:
    """Make the installed set consistent again, with the requirements the repository itself declared.

    Round 1 resolves **jointly**: every declared requirement that is installed, plus the ranges ``pip check`` says the
    installed packages need, in one pip run. The resolver then backtracks to a set that satisfies all of them (for
    ``fastapi==0.110.0`` next to ``streamlit>=1.32.0`` it picks an older streamlit that allows ``starlette<0.37``); no
    pin of the repository is loosened. If that is not enough, round 2 reinstalls only the conflicting dependencies at
    the reported ranges, unless that would repeat round 1. The ranges come from installed package metadata, not from a
    guess; what remains is reported so tests and the human know the environment is inconsistent.
    """
    from . import env_check

    workspace = Path(workspace).resolve()
    python = platform_compat.venv_python(workspace / VENV_DIR)

    def problems() -> list[dict[str, str]]:
        both = env_check.dependency_problems(
            workspace
        )  # version conflicts, then dependencies that are not installed
        return [*both["conflicts"], *both["unmet"]]

    found = problems()
    summary: dict = {"found": [dict(c) for c in found], "repaired": [], "remaining": found}
    if not found:
        return summary
    installed = env_check.installed_distributions(workspace) or set()
    declared = [s for s in dict.fromkeys(specs or []) if _spec_name(s) in installed]
    tried: list[list[str]] = []
    for round_no in range(1, MAX_CONFLICT_ROUNDS + 1):
        ranges = list(dict.fromkeys(c["requirement"] for c in summary["remaining"]))
        wanted = [*declared, *ranges] if round_no == 1 else ranges
        if not wanted or wanted in tried:
            break
        tried.append(wanted)
        result = run_command(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", *wanted],
            workspace,
            Path(out_dir) / f"conflict-repair-{round_no}",
            timeout=timeout,
            env=env,
            inherit_env=True,
        )
        summary["repaired"].append({"round": round_no, "requirements": wanted, "status": result["status"]})
        summary["remaining"] = problems()
        if not summary["remaining"]:
            break
    return summary


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
    if step.name == "playwright":
        # Preserve each attempt before a subsequent Playwright run replaces its working reports.
        for name in ("playwright-report", "test-results"):
            source = workspace / step.cwd / REPORT_DIR / name
            if source.is_dir() and any(
                p.is_file() and p.stat().st_mtime >= started for p in source.rglob("*")
            ):
                shutil.copytree(source, out_dir / label / "browser-reports" / name, symlinks=True)
    entry = {
        "name": step.name,
        "kind": step.kind,
        "argv": step.argv,
        "status": result["status"],
        "exit_code": result.get("exit_code"),
        "duration_seconds": result.get("duration_seconds"),
        "stdout": stdout,
        "scope": step.scope,
    }
    try:
        cases = _step_cases(step, workspace, result, started)
    except (ET.ParseError, OSError, ValueError, json.JSONDecodeError) as exc:
        cases, entry["parse_error"] = [], str(exc)
    entry["summary"] = summarize(cases)
    cases = [{**case, "scope": step.scope} for case in cases]
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
        "scope_expectations": plan.scope_expectations,
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
            diagnoses = _diagnose_step(result, out_dir / label, step.argv)
            if diagnoses:
                report["steps"][-1]["diagnoses"] = [d.to_dict() for d in diagnoses[:3]]
            if step.continue_on_failure:
                report.setdefault("warnings", []).append(
                    f"setup step '{step.name}' failed ({result['status']})" + _why(diagnoses)
                )
            else:
                setup_failed = True
                report.update(
                    status="setup_failed",
                    error=f"Setup step '{step.name}' failed: {result['status']}" + _why(diagnoses),
                )
                break
    if not setup_failed:
        if not report["cases"] or report.get("quarantined"):
            last = next((e for e in reversed(report["steps"]) if e["kind"] == "test"), None)
            if last and last["status"] not in {"command_succeeded", "passed"} and not report["cases"]:
                diagnostics += [ln for ln in _decisive_lines(last) if ln not in diagnostics]
            report["diagnostics"] = diagnostics[:400]
            found = errors.classify(
                chr(10).join(diagnostics), plan.adapter.split("-")[0] if plan.adapter else None
            )
            if found:
                report["diagnoses"] = [d.to_dict() for d in found[:5]]
        counts = summarize(report["cases"])
        report["summary"] = counts
        if counts["tests"] == 0:
            report["status"] = "no_test_evidence"
        elif (
            counts["failed"]
            or counts["errors"]
            or report.get("quarantined")
            or any(
                step["kind"] == "test" and step["status"] not in {"command_succeeded", "passed"}
                for step in report["steps"]
            )
        ):
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

    Matching order: full file/module identity, then a unique filename/stem, then a unique declared name. Collection and compile
    errors are reported by the runners with the *dotted module path* as the name and no class, so the stem is also
    matched against the name; otherwise those failures lose their case and their message.
    """
    indexed = [(f, declared_test_name(f["path"], f["content"])) for f in files]
    mapped = []
    for case in cases:
        name = str(case.get("name", "")).split("[", 1)[0]
        label = str(case.get("classname", "")).replace("\\", "/")
        report_file = str(case.get("file", "")).replace("\\", "/")
        exact = []
        identified = []
        named = []
        for file, declared in indexed:
            path = PurePosixPath(file["path"])
            module = path.with_suffix("").as_posix().replace("/", ".")
            file_match = report_file and (report_file == str(path) or report_file.endswith("/" + str(path)))
            module_match = label == module or label.startswith(module + ".") or name == module
            if file_match or module_match:
                exact.append(file["case_ids"][0])
            elif (
                label.split(".")[-1] == path.stem
                or name == path.stem
                or PurePosixPath(label).name == path.name
                or (report_file and PurePosixPath(report_file).name == path.name)
            ):
                identified.append(file["case_ids"][0])
            if declared and name == declared:
                named.append(file["case_ids"][0])
        candidates = set(exact or identified or named)
        mapped.append(
            {
                **case,
                "case_id": next(iter(candidates)) if len(candidates) == 1 else None,
                **({"mapping_error": "ambiguous test identity"} if len(candidates) > 1 else {}),
            }
        )
    return mapped

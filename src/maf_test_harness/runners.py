"""Per-ecosystem runner adapters: turn a StackProfile + generated files into an explicit, reviewable plan.

The host, never a model, chooses every command. The plan is shown to the human before anything runs.
Execution is local-process (not a security sandbox); see execution.run_command.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from .execution import read_junit_cases, run_command
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
    for candidate in (Path.home() / "Library/Caches/ms-playwright", Path.home() / ".cache/ms-playwright"):
        if candidate.is_dir():
            return {"PLAYWRIGHT_BROWSERS_PATH": str(candidate)}
    return {}


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
    return [pkg for pkg, patterns in PYTHON_TEST_HELPERS.items()
            if any(re.search(pattern, text, re.M) for pattern in patterns)]


def _repo_has_pytest_config(ws: Path) -> bool:
    def contains(name: str, needle: str) -> bool:
        path = ws / name
        return path.is_file() and needle in path.read_text(encoding="utf-8", errors="replace")
    return ((ws / "pytest.ini").is_file() or contains("pyproject.toml", "[tool.pytest")
            or contains("tox.ini", "[pytest]") or contains("setup.cfg", "[tool:pytest]"))


def _paths(files: list[dict], suffixes: tuple[str, ...] | None = None) -> list[str]:
    return [f["path"] for f in files if not suffixes or PurePosixPath(f["path"]).suffix.lower() in suffixes]


def _java_class(path: str) -> str:
    return PurePosixPath(path).stem


def build_plan(stack: StackProfile, workspace: Path, files: list[dict], *, cache_dir: Path,
               timeout: float = 900) -> RunPlan:
    """Choose the adapter for the primary ecosystem of the *generated tests*."""
    ws = Path(workspace)
    generated = _paths(files)
    exts = {PurePosixPath(p).suffix.lower() for p in generated}
    eco = stack.primary
    # Generated file types win over the repo's primary ecosystem (e.g. Playwright specs in a Java repo).
    if exts <= {".py"}:
        eco = "python"
    elif exts <= {".js", ".jsx", ".ts", ".tsx", ".mjs"}:
        eco = "node"
    elif exts <= {".java", ".kt"}:
        eco = "gradle" if "gradle" in stack.ecosystems[:1] else "maven"
    elif exts <= {".go"}:
        eco = "go"
    elif exts <= {".cs"}:
        eco = "dotnet"
    cache = str(Path(cache_dir).resolve())
    reports = ws / REPORT_DIR
    missing = [t for t in REQUIRED_TOOLS.get(eco, ()) if shutil.which(t) is None]
    if missing and not (eco == "maven" and (ws / "mvnw").exists()):
        return RunPlan(eco, [], blocked_reason=f"Required tool(s) not installed on the host: {', '.join(missing)}")

    if eco == "python":
        venv_py = f"{VENV_DIR}/bin/python"
        helpers = python_test_helpers(files)
        steps = [Step("create-venv", ["python3", "-m", "venv", VENV_DIR], "setup", timeout=timeout),
                 Step("install-pytest", [venv_py, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                                         "pytest", *helpers],
                      "setup", env={"PIP_CACHE_DIR": f"{cache}/pip"}, timeout=timeout)]
        # The workspace lives under the harness; without its own pytest config, pytest would walk up and pick the
        # harness's config. Pin the rootdir with an empty config unless the repository ships its own.
        isolate: list[str] = []
        if not _repo_has_pytest_config(ws):
            reports.mkdir(exist_ok=True)
            (reports / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            isolate = ["-c", f"{REPORT_DIR}/pytest.ini", "--rootdir", "."]
        if (ws / "requirements.txt").is_file():
            steps.append(Step("install-requirements", [venv_py, "-m", "pip", "install", "--disable-pip-version-check",
                                                       "-q", "-r", "requirements.txt"], "setup",
                              env={"PIP_CACHE_DIR": f"{cache}/pip"}, timeout=timeout, continue_on_failure=True))
        elif (ws / "pyproject.toml").is_file() or (ws / "setup.py").is_file():
            steps.append(Step("install-project", [venv_py, "-m", "pip", "install", "--disable-pip-version-check",
                                                  "-q", "-e", "."], "setup", env={"PIP_CACHE_DIR": f"{cache}/pip"},
                              timeout=timeout, continue_on_failure=True))
        steps.append(Step("pytest", [venv_py, "-m", "pytest", "-p", "no:cacheprovider", "-q", *isolate,
                                     "--continue-on-collection-errors", "--import-mode=importlib",
                                     f"--junitxml={REPORT_DIR}/junit.xml", *_paths(files, (".py",))], "test",
                          env={"PYTHONPATH": f"{ws}:{ws / 'src'}"}, timeout=timeout, parser="junit",
                          report_glob=f"{REPORT_DIR}/junit.xml"))
        return RunPlan("python-pytest", steps)

    if eco == "node":
        if not (ws / "package.json").is_file():  # e.g. Playwright specs added to a Java/Python repository
            (ws / "package.json").write_text('{"private": true, "name": "generated-tests"}\n', encoding="utf-8")
        lock = (ws / "package-lock.json").is_file()
        steps = [Step("npm-install", ["npm", "ci" if lock else "install", "--no-audit", "--no-fund", "--ignore-scripts"],
                      "setup", env={"npm_config_cache": f"{cache}/npm"}, timeout=timeout)]
        uses_playwright = any("@playwright/test" in f["content"] for f in files)
        runner = "playwright" if uses_playwright else stack.test_runner or "jest"
        if uses_playwright and stack.test_runner != "playwright":
            steps.append(Step("install-playwright", ["npm", "install", "--no-save", "--no-audit", "--no-fund",
                                                     "@playwright/test"], "setup",
                              env={"npm_config_cache": f"{cache}/npm"}, timeout=timeout))
        if runner == "playwright":
            steps.append(Step("install-browser", ["npx", "--no-install", "playwright", "install", "chromium"],
                              "setup", env=_playwright_browsers(), timeout=timeout, continue_on_failure=True))
            steps.append(Step("playwright", ["npx", "--no-install", "playwright", "test", *_paths(files),
                                             "--reporter=junit"], "test", parser="junit",
                              env={"PLAYWRIGHT_JUNIT_OUTPUT_NAME": str(reports / "junit.xml"), **_playwright_browsers()},
                              report_glob=f"{REPORT_DIR}/junit.xml", timeout=timeout))
        elif runner == "vitest":
            steps.append(Step("vitest", ["npx", "--no-install", "vitest", "run", *_paths(files), "--reporter=junit",
                                         f"--outputFile={REPORT_DIR}/junit.xml"], "test", parser="junit",
                              report_glob=f"{REPORT_DIR}/junit.xml", timeout=timeout))
        else:
            steps.append(Step("jest", ["npx", "--no-install", "jest", "--ci", *_paths(files), "--json",
                                       f"--outputFile={REPORT_DIR}/jest.json"], "test", parser="jest",
                              report_glob=f"{REPORT_DIR}/jest.json", timeout=timeout))
        return RunPlan(f"node-{runner}", steps)

    if eco == "maven":
        mvn = "./mvnw" if (ws / "mvnw").exists() else "mvn"
        classes = ",".join(_java_class(p) for p in _paths(files, (".java",)))
        return RunPlan("java-maven", [Step(
            "mvn-test", [mvn, "-B", "-ntp", f"-Dmaven.repo.local={cache}/m2", "test", f"-Dtest={classes}",
                         "-DfailIfNoTests=false", "-Dsurefire.failIfNoSpecifiedTests=false"], "test",
            parser="junit", report_glob="**/target/surefire-reports/TEST-*.xml", timeout=timeout,
            env={"MAVEN_OPTS": "-Xmx1g"})])

    if eco == "gradle":
        gradle = "./gradlew" if (ws / "gradlew").exists() else "gradle"
        filters = [arg for p in _paths(files, (".java", ".kt")) for arg in ("--tests", _java_class(p))]
        return RunPlan("java-gradle", [Step(
            "gradle-test", [gradle, "test", "--no-daemon", *filters], "test", parser="junit",
            report_glob="**/build/test-results/test/*.xml", timeout=timeout,
            env={"GRADLE_USER_HOME": f"{cache}/gradle"})])

    if eco == "go":
        packages = sorted({f"./{PurePosixPath(p).parent.as_posix()}" if PurePosixPath(p).parent.as_posix() != "." else "."
                           for p in _paths(files, (".go",))})
        return RunPlan("go-test", [Step(
            "go-test", ["go", "test", "-json", "-count=1", *packages], "test", parser="go", timeout=timeout,
            env={"GOCACHE": f"{cache}/go-build", "GOMODCACHE": f"{cache}/go-mod", "GOFLAGS": "-mod=mod"})])

    if eco == "dotnet":
        return RunPlan("dotnet-test", [Step(
            "dotnet-test", ["dotnet", "test", "--logger", "trx;LogFileName=results.trx",
                            "--results-directory", str(reports)], "test", parser="trx",
            report_glob=f"{REPORT_DIR}/*.trx", timeout=timeout)])

    return RunPlan(eco, [], blocked_reason=f"No runner adapter for ecosystem '{eco}' yet.")


# ---------------------------------------------------------------- result parsers
def parse_jest_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for suite in data.get("testResults", []):
        for test in suite.get("assertionResults", []):
            status = {"passed": "passed", "failed": "failed", "pending": "skipped", "skipped": "skipped",
                      "todo": "skipped"}.get(test.get("status"), "failed")
            cases.append({"name": test.get("title", ""), "classname": suite.get("name", ""), "status": status,
                          "message": "\n".join(test.get("failureMessages") or [])[:1500]})
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
        entry = cases.setdefault(f"{event.get('Package')}::{test}", {
            "name": test, "classname": event.get("Package", ""), "status": "failed", "message": ""})
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
            cases.append({"name": result.get("testName", ""), "classname": "", "message": message[:1500],
                          "status": "passed" if outcome == "Passed" else "skipped" if outcome in {
                              "NotExecuted", "Inconclusive"} else "failed"})
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
    reports = sorted(p for p in workspace.glob(step.report_glob) if p.is_file() and p.stat().st_mtime >= started)
    if not reports:
        return []
    if step.parser == "jest":
        return [c for p in reports for c in parse_jest_json(p)]
    if step.parser == "trx":
        return [c for p in reports for c in parse_trx(p)]
    return read_junit_cases(reports)


def _expand_env(env: dict[str, str], workspace: Path) -> dict[str, str]:
    return {k: v.replace("{ws}", str(workspace)) for k, v in env.items()}


def execute_plan(plan: RunPlan, workspace: Path, out_dir: Path) -> dict:
    """Run every step; return an aggregate report. Never reports success without fresh test evidence."""
    workspace, out_dir = Path(workspace).resolve(), Path(out_dir).resolve()
    (workspace / REPORT_DIR).mkdir(exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=False)
    report: dict = {"adapter": plan.adapter, "workspace": str(workspace), "steps": [], "cases": [],
                    "status": "no_test_evidence", "isolation": "local-process-not-security-sandbox"}
    if plan.blocked_reason:
        report.update(status="blocked", error=plan.blocked_reason)
        return report
    setup_failed = False
    for index, step in enumerate(plan.steps, 1):
        cwd = (workspace / step.cwd).resolve()
        started = time.time()
        result = run_command(step.argv, cwd, out_dir / f"{index:02d}-{step.name}", timeout=step.timeout,
                             env=_expand_env(step.env, workspace), inherit_env=True)
        result["stdout_path"] = str(out_dir / f"{index:02d}-{step.name}" / "stdout.log")
        entry = {"name": step.name, "kind": step.kind, "argv": step.argv, "status": result["status"],
                 "exit_code": result.get("exit_code"), "duration_seconds": result.get("duration_seconds"),
                 "stdout": result["stdout_path"]}
        if step.kind == "test":
            try:
                cases = _step_cases(step, workspace, result, started)
            except (ET.ParseError, OSError, ValueError, json.JSONDecodeError) as exc:
                cases, entry["parse_error"] = [], str(exc)
            entry["summary"] = summarize(cases)
            report["cases"].extend(cases)
        report["steps"].append(entry)
        if step.kind == "setup" and result["status"] not in {"command_succeeded", "passed"}:
            if step.continue_on_failure:
                report.setdefault("warnings", []).append(f"setup step '{step.name}' failed ({result['status']})")
            else:
                setup_failed = True
                report.update(status="setup_failed", error=f"Setup step '{step.name}' failed: {result['status']}")
                break
    if not setup_failed and not report["cases"]:
        # No per-test evidence (e.g. a compile error): keep the decisive lines so cases can be repaired from them.
        lines = []
        for step in report["steps"]:
            if step["kind"] == "test" and step["status"] not in {"command_succeeded", "passed"}:
                text = Path(step["stdout"]).read_text(encoding="utf-8", errors="replace")
                text += "\n" + Path(step["stdout"]).with_name("stderr.log").read_text(encoding="utf-8", errors="replace")
                lines += [ln.strip() for ln in text.splitlines()
                          if re.search(r"error|cannot find|does not exist|fail|exception|denied", ln, re.I)]
        report["diagnostics"] = lines[:400]
    if not setup_failed:
        counts = summarize(report["cases"])
        report["summary"] = counts
        if counts["tests"] == 0:
            report["status"] = "no_test_evidence"
        elif counts["failed"] or counts["errors"]:
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
    """Attach the approved case ID to each executed test (one file per case, one test per file)."""
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
        case_id = by_name.get(case["name"]) or next(
            (cid for stem, cid in by_stem.items() if stem and stem in (case.get("classname") or "")), None)
        mapped.append({**case, "case_id": case_id})
    return mapped

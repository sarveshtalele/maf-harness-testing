"""Deliverable ZIP: the complete repository with every generated test in place, plus the evidence that produced it.

Layout inside the archive::

    <repo-name>/…                 the repository at the analyzed commit + generated tests at their real paths
    harness-report/qa_report.md   verdict, per-case results, files to update (file:line)
    harness-report/steps/*.md     one validated Markdown report per workflow step (00-summary … 08-qa-verdict)
    harness-report/*.json         requirements, environment, test plan, test design, generation catalog, executions
    harness-report/audit.jsonl    approvals and tool calls
    harness-report/RUN_TESTS.md   the exact commands the harness used, to reproduce the run
"""

from __future__ import annotations

import json
import shlex
import zipfile
from pathlib import Path, PureWindowsPath

from ..envs.execution import EXPORT_SKIP_DIRS, FORWARDED_ENV, _exportable
from ..hooks.builtin import redact
from ..ui.evidence import MAX_FILE_BYTES, ROOTS, clean_content
from .step_reports import build_step_reports

REPORT_DIR = "harness-report"


def _json(data) -> str:
    return redact(json.dumps(data, indent=2, default=str))[0]


def _add_tree(archive: zipfile.ZipFile, repo: Path, prefix: str, skip: set[str]) -> None:
    for path in sorted(repo.rglob("*")):
        relative = path.relative_to(repo)
        if (
            path.is_file()
            and not any(part in EXPORT_SKIP_DIRS for part in relative.parts)
            and _exportable(relative, path)
            and relative.as_posix() not in skip
        ):
            archive.write(path, f"{prefix}/{relative.as_posix()}")


def run_instructions(state: dict) -> str:
    plan = state.get("execution_plan", {}).get("plan", {})
    env = state.get("environment", {}).get("plan", {})
    lines = [
        "# Re-running the generated tests",
        "",
        f"Adapter: `{plan.get('adapter', 'n/a')}`. Each command records its repository-relative working directory.",
        "",
    ]
    for title, steps in (
        ("Install (isolated environment)", env.get("steps", [])),
        (
            "Application services",
            (plan.get("app") or {}).get("services", [plan["app"]] if plan.get("app") else []),
        ),
        ("Run", [s for s in plan.get("steps", []) if s["kind"] == "test"]),
    ):
        for step in steps:
            lines += [
                f"## {title}: {step.get('name', 'service')}",
                "",
                f"Working directory: `{step.get('cwd', '.')}`",
                "",
                "```text",
                shlex.join(step.get("argv", [])),
                "```",
                "",
            ]
            if step.get("env"):
                lines += ["Environment: `" + json.dumps(step["env"]) + "`", ""]
    lines += [
        "These commands record the original run. Use `python harness-report/rerun.py --install` then "
        "`python harness-report/rerun.py` from the extracted archive to resolve local tool and environment paths "
        "on Windows or macOS. Install the recorded toolchain versions first; missing tools are reported.",
        "",
    ]
    if state.get("app_url"):
        lines += [f"UI/E2E tests read the target from `BASE_URL` (used: `{state['app_url']}`).", ""]
    lines += [
        "Generated files are listed in `harness-report/test_generation.json`; the traceability from requirement to "
        "test is in `harness-report/qa_report.md`; one report per workflow step is in `harness-report/steps/`."
    ]
    return redact("\n".join(lines) + "\n")[0]


def portable_plan(state: dict, run_dir: Path) -> dict:
    """Keep cwd/env and replace only host-specific workspace/tool paths."""
    raw = {
        "install": state.get("environment", {}).get("plan", {}).get("steps", []),
        "test": state.get("execution_plan", {}).get("plan", {}).get("steps", []),
        "app": state.get("execution_plan", {}).get("plan", {}).get("app"),
    }

    def replace_paths(value):
        if isinstance(value, str):
            replaced = value.replace(str(run_dir / "workspace"), "{REPO}")
            return replaced.replace("\\", "/") if replaced.startswith("{REPO}") else replaced
        if isinstance(value, list):
            return [replace_paths(item) for item in value]
        if isinstance(value, dict):
            return {key: replace_paths(item) for key, item in value.items()}
        return value

    portable = json.loads(redact(json.dumps(replace_paths(raw), default=str))[0])
    app = portable.get("app") or {}
    if app and not app.get("url"):
        app["url"] = state.get("app_url")
    services = app.get("services", [app] if app else [])
    for step in [*portable["install"], *portable["test"], *services]:
        if step.get("argv"):
            raw_executable = step["argv"][0]
            executable = (
                PureWindowsPath(raw_executable)
                if PureWindowsPath(raw_executable).is_absolute()
                else Path(raw_executable)
            )
            if executable.is_absolute():
                step["argv"][0] = (
                    "{SYSTEM_PYTHON}" if executable.name.startswith("python") else executable.name
                )
        # Resolve installed toolchains on the receiving host, not from the exporting host's cache.
        for key in ("PATH", "JAVA_HOME", "MAVEN_HOME", "GRADLE_HOME", "GOROOT", "DOTNET_ROOT"):
            step.get("env", {}).pop(key, None)
        for key, value in step.get("env", {}).items():
            if "cache" in key.lower() and Path(value).is_absolute():
                step["env"][key] = "{REPO}/.harness-cache/" + key
    return portable


RERUN = r'''"""Run from the extracted archive; installs are explicit with --install."""
import json, os, pathlib, shutil, signal, subprocess, sys, time
root = pathlib.Path(__file__).resolve().parent.parent
plan = json.loads(pathlib.Path(__file__).with_name("rerun-plan.json").read_text(encoding="utf-8"))
repo = root / json.loads(pathlib.Path(__file__).with_name("repo_analysis.json").read_text(encoding="utf-8"))["repo_name"]
def expand(value):
    value = str(value).replace("{SYSTEM_PYTHON}", sys.executable).replace("{REPO}", str(repo)).replace("{workspace}", str(repo)).replace("{ws}", str(repo))
    for suffix in ("/bin/python", "/Scripts/python.exe"):
        value = value.replace(str(repo / ".harness-venv") + suffix, str(repo / ".harness-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")))
    return value
def command(step):
    argv = [expand(a) for a in step["argv"]]
    executable = pathlib.Path(argv[0])
    if ".harness-venv" in executable.parts:
        argv[0] = str(repo / ".harness-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    elif executable.is_absolute():
        name = executable.stem
        argv[0] = sys.executable if name.startswith("python") else shutil.which(name) or name
    if argv[0] in {"npm.cmd", "npx.cmd", "node.exe", "java.exe", "mvn.cmd", "go.exe", "dotnet.exe"}:
        argv[0] = pathlib.Path(argv[0]).stem
    if os.name == "nt" and argv[0] in {"npm", "npx", "mvn"}:
        argv[0] += ".cmd"
    env = {key: value for key, value in os.environ.items() if key in ALLOWED_ENV}
    env.update({key: expand(value) for key, value in step.get("env", {}).items()})
    cwd = (repo / step.get("cwd", ".")).resolve()
    if not cwd.is_relative_to(repo.resolve()):
        raise ValueError("Command cwd escapes extracted repository")
    return argv, cwd, env
if "--install" in sys.argv:
    for step in plan["install"]:
        argv, cwd, env = command(step)
        subprocess.run(argv, cwd=cwd, env=env, check=True)
else:
    processes = []
    try:
        app = plan.get("app") or {}
        for service in app.get("services", [app] if app.get("argv") else []):
            argv, cwd, env = command(service)
            for value in service.get("env", {}).values():
                state_path = pathlib.Path(expand(value))
                if state_path.is_absolute() and state_path.is_relative_to(repo / ".harness-app-state"):
                    (state_path.parent if state_path.suffix == ".db" else state_path).mkdir(parents=True, exist_ok=True)
            group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            processes.append(subprocess.Popen(argv, cwd=cwd, env=env, **group))
            url = service.get("ready_url") or ((service.get("url") or "").rstrip("/") + service.get("ready_path", "/") if service.get("url") else None)
            if url:
                import urllib.request
                deadline = time.monotonic() + 60
                while True:
                    try:
                        with urllib.request.urlopen(url, timeout=2) as response:
                            if 200 <= response.status < 400: break
                    except OSError: pass
                    if time.monotonic() >= deadline: raise RuntimeError("Application not ready: " + url)
                    time.sleep(.5)
        for step in plan["test"]:
            if step.get("kind") != "test": continue
            argv, cwd, env = command(step)
            subprocess.run(argv, cwd=cwd, env=env, check=True)
    finally:
        for process in reversed(processes):
            if process.poll() is not None: continue
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL)
            else:
                os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt": process.kill()
                else: os.killpg(process.pid, signal.SIGKILL)
'''.replace("ALLOWED_ENV", repr(FORWARDED_ENV))


def build_deliverable(run_dir: Path, state: dict, executions_dir: Path | None = None) -> Path:
    """Create ``deliverables/<repo>-with-tests-<run>.zip`` and return its path."""
    run_dir = Path(run_dir).resolve()
    repo_dir = run_dir / "repo"
    arts = state["artifacts"]
    name = arts["repo_analysis"]["repo_name"]
    generated = (arts.get("test_generation") or {}).get("files", [])
    out = run_dir / "deliverables"
    out.mkdir(exist_ok=True)
    target = out / f"{name}-with-tests-{run_dir.name}.zip"
    overwritten = {f["path"] for f in generated}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        _add_tree(archive, repo_dir, name, overwritten)
        for item in generated:  # generated tests at their real paths, exactly as approved and executed
            archive.writestr(f"{name}/{item['path']}", item["content"])
        for stage, artifact in arts.items():
            if stage == "test_execution":
                qa = artifact["qa_report"]
                archive.writestr(f"{REPORT_DIR}/qa_report.md", redact(artifact["markdown"])[0])
                archive.writestr(f"{REPORT_DIR}/qa_report.json", _json(qa))
                archive.writestr(f"{REPORT_DIR}/execution.json", _json(artifact["execution"]))
                for report_name, report in artifact.get("skill_reports", {}).items():
                    file = "coverage-summary" if report_name == "coverage" else "traceability-matrix"
                    archive.writestr(f"{REPORT_DIR}/{file}.json", _json(report))
            else:
                archive.writestr(f"{REPORT_DIR}/{stage}.json", _json(artifact))
        if state.get("environment"):
            archive.writestr(f"{REPORT_DIR}/environment.json", _json(state["environment"]))
        audit = run_dir / "audit.jsonl"
        if audit.is_file():
            archive.writestr(f"{REPORT_DIR}/audit.jsonl", clean_content(audit))
        for report in sorted((executions_dir or run_dir / "executions").glob("attempt-*/report.json")):
            archive.writestr(f"{REPORT_DIR}/executions/{report.parent.name}.json", clean_content(report))
        for step_name, markdown in build_step_reports(run_dir, state).items():
            archive.writestr(f"{REPORT_DIR}/steps/{step_name}.md", redact(markdown)[0])
        archive.writestr(f"{REPORT_DIR}/RUN_TESTS.md", run_instructions(state))
        archive.writestr(f"{REPORT_DIR}/rerun.py", RERUN)
        archive.writestr(f"{REPORT_DIR}/rerun-plan.json", json.dumps(portable_plan(state, run_dir), indent=2))
        for folder in ROOTS:
            directory = run_dir / folder
            for path in sorted(directory.rglob("*")):
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.resolve().is_relative_to(run_dir)
                    and path.stat().st_size <= MAX_FILE_BYTES
                    and not any(part in EXPORT_SKIP_DIRS for part in path.relative_to(directory).parts)
                    and path.suffix.lower()
                    in {
                        ".html",
                        ".png",
                        ".jpg",
                        ".webp",
                        ".xml",
                        ".json",
                        ".css",
                        ".js",
                        ".woff2",
                        ".md",
                        ".txt",
                        ".log",
                    }
                ):
                    archive.writestr(
                        f"{REPORT_DIR}/evidence/{path.relative_to(run_dir).as_posix()}", clean_content(path)
                    )
        prompt_settings = run_dir / "prompts.json"
        if prompt_settings.is_file():
            archive.writestr(f"{REPORT_DIR}/prompts/settings.json", clean_content(prompt_settings))
        for record in sorted((run_dir / "prompts").glob("*.json")):
            if not record.is_symlink() and record.stat().st_size <= MAX_FILE_BYTES:
                archive.writestr(f"{REPORT_DIR}/prompts/{record.name}", clean_content(record))
        workspace = run_dir / "workspace"
        for config in workspace.rglob(".harness-playwright-report.config.ts"):
            if (
                config.is_file()
                and not config.is_symlink()
                and config.resolve().is_relative_to(workspace.resolve())
            ):
                archive.write(config, f"{name}/{config.relative_to(workspace).as_posix()}")
    return target

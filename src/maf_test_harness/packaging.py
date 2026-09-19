"""Deliverable ZIP: the complete repository with every generated test in place, plus the evidence that produced it.

Layout inside the archive::

    <repo-name>/…                 the repository at the analyzed commit + generated tests at their real paths
    harness-report/qa_report.md   verdict, per-case results, files to update (file:line)
    harness-report/*.json         requirements, environment, test plan, test design, generation catalog, executions
    harness-report/audit.jsonl    approvals and tool calls
    harness-report/RUN_TESTS.md   the exact commands the harness used, to reproduce the run
"""

from __future__ import annotations

import json
import shlex
import zipfile
from pathlib import Path

from .execution import EXPORT_SKIP_DIRS, _exportable

REPORT_DIR = "harness-report"


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
        f"Adapter: `{plan.get('adapter', 'n/a')}`. Run these from the repository root.",
        "",
    ]
    for title, steps in (
        ("Install (isolated environment)", env.get("steps", [])),
        ("Run", [s for s in plan.get("steps", []) if s["kind"] == "test"]),
    ):
        if steps:
            lines += [f"## {title}", "", "```bash"] + [shlex.join(s["argv"]) for s in steps] + ["```", ""]
    if state.get("app_url"):
        lines += [f"UI/E2E tests read the target from `BASE_URL` (used: `{state['app_url']}`).", ""]
    lines += [
        "Generated files are listed in `harness-report/test_generation.json`; the traceability from requirement to "
        "test is in `harness-report/qa_report.md`."
    ]
    return "\n".join(lines) + "\n"


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
                archive.writestr(f"{REPORT_DIR}/qa_report.md", artifact["markdown"])
                archive.writestr(f"{REPORT_DIR}/qa_report.json", json.dumps(qa, indent=2, default=str))
                archive.writestr(
                    f"{REPORT_DIR}/execution.json", json.dumps(artifact["execution"], indent=2, default=str)
                )
            else:
                archive.writestr(f"{REPORT_DIR}/{stage}.json", json.dumps(artifact, indent=2, default=str))
        if state.get("environment"):
            archive.writestr(
                f"{REPORT_DIR}/environment.json", json.dumps(state["environment"], indent=2, default=str)
            )
        audit = run_dir / "audit.jsonl"
        if audit.is_file():
            archive.write(audit, f"{REPORT_DIR}/audit.jsonl")
        for report in sorted((executions_dir or run_dir / "executions").glob("attempt-*/report.json")):
            archive.write(report, f"{REPORT_DIR}/executions/{report.parent.name}.json")
        archive.writestr(f"{REPORT_DIR}/RUN_TESTS.md", run_instructions(state))
    return target

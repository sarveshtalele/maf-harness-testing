"""Headless driver for the same checkpointed MAF workflow the Next.js UI uses.

  maf-harness run https://github.com/owner/repo [--auto-approve] [--auto-heal 2] [--app-url http://localhost:8080]
  maf-harness lint-skills [skills_dir]

Without --auto-approve every gate is shown and you answer on the terminal. --auto-approve is an explicit
statement that YOU consent to cloning the repository and running its generated tests on this machine.
Exit codes: 0 PASS / PASS_WITH_SKIPS, 1 FAIL, 2 inconclusive or workflow error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import threading
import uuid
from pathlib import Path

from .agent.runtime import MAFHarnessRuntime
from .envs.platform_compat import RUN_CANCEL
from .llm.usage import DailyUsageLedger
from .monitoring.report import build_report, render_markdown
from .settings import API_KEY, BASE_URL, MODEL, SKILL_MODE, validate_gateway_settings
from .skills.config import SKILL_NAMES
from .skills.registry import lint_all, load_lock, tree_sha256, verify_lock
from .storage import read_json
from .workflow import ENV_STAGE, EXECUTION_STAGE, Harness

ROOT = Path(__file__).resolve().parents[2]


def _say(message: str) -> None:
    print(message, flush=True)


def _prompt(pending: dict) -> dict:
    _say(f"\n== [{pending['stage']}] {pending['title']}  ({pending['kind']})")
    if pending.get("error"):
        _say(f"   error: {pending['error']}")
    if pending["kind"] == "tool":
        for step in pending["details"].get("steps", []):
            _say(f"   {step['kind']:5} {step['command']}")
        _say(f"   {pending['details'].get('effects', '')}")
    options = {
        "review": "[a]pprove / [r]evise / [s]top",
        "tool": "[a]pprove / [d]eny / [k]skip / [s]top",
        "error": "[r]etry / [s]top",
    }[pending["kind"]]
    choice = input(f"{options}: ").strip().lower()[:1]
    table = {
        "a": "approve",
        "r": "revise" if pending["kind"] == "review" else "retry",
        "d": "deny",
        "k": "skip",
        "s": "stop",
    }
    response = {"decision": table.get(choice, "stop")}
    if response["decision"] == "revise":
        response["feedback"] = input("Revision instructions: ").strip()
    return response


def _auto(pending: dict, state: dict, args, retries: dict) -> dict:
    stage, kind = pending["stage"], pending["kind"]
    if kind == "error":
        retries[stage] = retries.get(stage, 0) + 1
        return {"decision": "retry"} if retries[stage] <= args.max_retries else {"decision": "stop"}
    if kind == "review" and stage == EXECUTION_STAGE:
        qa = pending["details"]["artifact"]["qa_report"]
        if qa["failures"] and state.get("heal_rounds", 0) < args.auto_heal:
            return {"decision": "revise", "feedback": "Automatic regeneration of failing tests."}
    if kind == "tool" and stage in {ENV_STAGE, EXECUTION_STAGE} and args.skip_execution:
        return {"decision": "skip"}
    return {"decision": "approve"}


async def run(args) -> int:
    validate_gateway_settings()
    runs = Path(os.getenv("HARNESS_RUNS_DIR") or ROOT / ".maf-harness" / "runs").expanduser()
    runs = (runs if runs.is_absolute() else ROOT / runs).resolve()
    run_dir = Path(args.run_dir).expanduser() if args.run_dir else runs / uuid.uuid4().hex[:12]
    runtime = MAFHarnessRuntime(
        api_key=API_KEY,
        model=args.model or MODEL,
        base_url=BASE_URL or None,
        skills_root=ROOT / "skills",
        skill_mode=args.skill_mode,
    )
    host = Harness(run_dir, skills_root=ROOT / "skills", runtime=runtime, execute=not args.no_execution)
    _say(f"run dir: {run_dir}\nmodel: {args.model or MODEL}  skill mode: {args.skill_mode}")
    retries: dict[str, int] = {}
    cancel = (
        threading.Event()
    )  # child process trees started by run_command watch this and are killed on Ctrl-C
    RUN_CANCEL.set(cancel)
    if hasattr(signal, "SIGBREAK"):  # Windows Ctrl-Break: same as Ctrl-C instead of killing the process
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    stopped = False
    try:
        # An existing run directory resumes at its pending gate; a stopped or finished run is reported as it is.
        view = (
            host.view
            if host.view.get("status") != "new"
            else await host.start(args.repo_url, args.app_url, args.start_app, args.app_component)
        )
        while view["status"] == "waiting":
            pending = view["pending"]
            response = _auto(pending, host.state, args, retries) if args.auto_approve else _prompt(pending)
            _say(
                f"-> [{pending['stage']}/{pending['kind']}] {response['decision']}"
                + (f"  ({pending['error'][:160]})" if pending.get("error") else "")
            )
            view = await host.respond(pending["id"], response)
    except (KeyboardInterrupt, asyncio.CancelledError):
        cancel.set()
        host.view.update(status="stopped", pending=None)
        host.audit("run_stopped_by_user", stage=host.state.get("active_stage"))
        host.save()
        stopped = True
    finally:
        await runtime.close()
    if stopped:
        _say(f"\nrun stopped by user; saved evidence kept in {run_dir}")
        return 130
    state = host.state
    budget = state.get("budget", {})
    _say(
        f"\nstatus: {view['status']}  provider calls: {budget.get('calls')}  reported tokens: "
        f"{budget.get('reported_tokens')}"
        f"  daily cap: {DailyUsageLedger().snapshot()['used']:,}/{DailyUsageLedger().limit:,} used today"
    )
    if state.get("deliverable_zip"):
        _say(f"deliverable ZIP (repository + generated tests + reports): {state['deliverable_zip']}")
    qa = (state.get("artifacts", {}).get(EXECUTION_STAGE) or {}).get("qa_report")
    if qa:
        (run_dir / "qa_report.json").write_text(json.dumps(qa, indent=2), encoding="utf-8")
        (run_dir / "qa_report.md").write_text(
            state["artifacts"][EXECUTION_STAGE]["markdown"], encoding="utf-8"
        )
        _say(
            f"QA verdict: {qa['verdict']}  cases: {qa['case_status_counts']}  report: {run_dir / 'qa_report.md'}"
        )
        return {"PASS": 0, "PASS_WITH_SKIPS": 0, "FAIL": 1}.get(qa["verdict"], 2)
    return 0 if view["status"] == "complete" and args.no_execution else 2


def verify_run(run_dir: Path, skills_root: Path) -> int:
    """Evidence check: did this run really use the MAF harness and the Agent Skills provider?"""
    state = read_json(run_dir / "run.json")["state"]
    runtime = state.get("runtime_provenance", {})
    rows = [
        (
            "agent built by agent_framework.create_harness_agent",
            runtime.get("factory") == "agent_framework.create_harness_agent",
        ),
        (
            "MAF versions recorded",
            bool(runtime.get("agent_framework_core") and runtime.get("agent_framework_openai")),
        ),
    ]
    for stage in SKILL_NAMES:
        prov, trace = (
            state.get("skill_provenance", {}).get(stage),
            state.get("skill_tool_trace", {}).get(stage),
        )
        if not prov:
            rows.append((f"{stage}: stage ran", False))
            continue
        assigned = [prov["name"], *(c["name"] for c in prov.get("companions", []))]
        rows += [
            (
                f"{stage}: skills {assigned} advertised by MAF SkillsProvider",
                bool(prov.get("uses_maf_skills_provider")),
            ),
            (
                f"{stage}: load_skill executed and succeeded for every assigned skill (FunctionMiddleware trace)",
                bool(
                    trace
                    and trace.get("skill_loaded_via_maf")
                    and set(trace.get("loaded_skills", [])) >= set(assigned)
                ),
            ),
            (
                f"{stage}: skill packages unchanged since the run",
                all(
                    entry["package_sha256"] == tree_sha256(skills_root / entry["name"])
                    for entry in [prov, *prov.get("companions", [])]
                ),
            ),
        ]
    rows.append(("vendored upstream skills match SKILLS.lock.json", not verify_lock(skills_root)))
    for run in state.get("skill_script_runs", []):
        rows.append(
            (f"script {run['skill']}/{run['script']} ran from the reviewed allow-list", run["exit_code"] == 0)
        )
    for label, ok in rows:
        _say(f"{'PASS' if ok else 'FAIL'}  {label}")
    invocations = state.get("invocations", [])
    _say(
        f"\nmodel invocations: {len(invocations)}  failed: {sum(i['status'] == 'failed' for i in invocations)}  "
        f"model: {runtime.get('model')}  skill mode: {runtime.get('skill_mode')}"
    )
    return 0 if all(ok for _, ok in rows) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="maf-harness", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    runp = sub.add_parser("run", help="Run the full workflow for a public GitHub repository")
    runp.add_argument("repo_url", help="https://github.com/owner/repo[.git] or .../tree/<ref>")
    runp.add_argument(
        "--app-url", help="Local URL of the running application (enables ui/e2e/accessibility cases)"
    )
    runp.add_argument("--app-component", help="Explicit repository application directory, e.g. frontend")
    runp.add_argument(
        "--start-app",
        action="store_true",
        help="Let the harness start the application itself for ui/e2e (Playwright) cases; shown in the run approval",
    )
    runp.add_argument("--run-dir", help="Run directory (default <repo-root>/.maf-harness/runs/<id>)")
    runp.add_argument("--model", help=f"Model name (default {MODEL})")
    runp.add_argument("--skill-mode", choices=["progressive", "preloaded"], default=SKILL_MODE)
    runp.add_argument(
        "--auto-approve",
        action="store_true",
        help="Approve every gate (consents to cloning and running generated tests locally)",
    )
    runp.add_argument(
        "--auto-heal",
        type=int,
        default=0,
        metavar="N",
        help="With --auto-approve: regenerate failing tests up to N times",
    )
    runp.add_argument(
        "--max-retries", type=int, default=2, help="With --auto-approve: retries per failing stage"
    )
    runp.add_argument(
        "--skip-execution",
        action="store_true",
        help="Approve everything but install nothing and run no tests",
    )
    runp.add_argument("--no-execution", action="store_true", help="Workflow without the execution stage")
    verify = sub.add_parser("verify-run", help="Check a saved run's MAF harness and Agent Skills evidence")
    verify.add_argument("run_dir", type=Path)
    verify.add_argument("--skills-dir", type=Path, default=ROOT / "skills")
    trace = sub.add_parser(
        "trace",
        help="Observability and traceability report for a saved run (skills, tools, cost, requirement chain)",
    )
    trace.add_argument("run_dir", type=Path)
    trace.add_argument("--format", choices=["md", "json"], default="md")
    trace.add_argument("--out", type=Path, help="Write the report to this file instead of stdout")
    trace.add_argument("--skills-dir", type=Path, default=ROOT / "skills")
    lint = sub.add_parser(
        "lint-skills", help="Validate bundled skills against the Agent Skills specification"
    )
    lint.add_argument("skills_dir", nargs="?", type=Path, default=ROOT / "skills")
    args = parser.parse_args(argv)
    if args.command == "verify-run":
        return verify_run(args.run_dir, args.skills_dir)
    if args.command == "trace":
        try:
            report = build_report(args.run_dir, args.skills_dir)
        except FileNotFoundError as exc:
            _say(str(exc))
            return 2
        text = json.dumps(report, indent=2, default=str) if args.format == "json" else render_markdown(report)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
            _say(f"wrote {args.out}")
        else:
            sys.stdout.write(text)
        return 0 if all(c["ok"] for c in report["checks"]) else 1
    if args.command == "lint-skills":
        reports = lint_all(args.skills_dir)
        for r in reports:
            _say(f"{'OK  ' if r.ok else 'FAIL'} {r.name}  {'; '.join(r.errors + r.warnings)}")
        lock = load_lock(args.skills_dir)
        problems = verify_lock(args.skills_dir)
        _say(
            f"{'OK  ' if not problems else 'FAIL'} SKILLS.lock.json  upstream {lock.get('upstream', {}).get('commit', 'n/a')[:12]}"
            f" · {len(lock.get('skills', {}))} locked packages · {len(lock.get('reviewed_scripts', {}))} skills with reviewed scripts"
        )
        for problem in problems:
            _say(f"FAIL {problem}")
        return 0 if all(r.ok for r in reports) and not problems else 1
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

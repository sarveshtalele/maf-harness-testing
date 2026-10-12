"""Loopback-only HTTP adapter. The existing Harness owns every workflow decision."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import re
import shutil
import traceback
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, RLock
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from filelock import FileLock, Timeout

from .. import paths, settings
from ..agent import prompt_settings
from ..agent.runtime import MAFHarnessRuntime
from ..hooks.builtin import register_secret
from ..monitoring.report import build_report
from ..repo.analyzer import validate_github_url
from ..repo.git_transport import read_operation
from ..repo.preview import preview_repository
from ..run_options import parse_run_options
from ..settings import current as current_settings
from ..settings import is_local_app_url, validate_gateway_settings
from ..storage import read_json
from ..workflow import Harness
from ..workflow_support import atomic_json
from .confidence_api import ConfidenceAPI
from .errors import run_errors
from .eta import case_budget_view, run_eta
from .evidence_api import EvidenceAPI
from .mcp_api import McpAPI
from .presentation import public
from .progress import progress
from .runtime_service import RuntimeService
from .settings_api import SettingsAPI

ROOT = paths.resource_root()
PUBLIC_STATE = (
    "run_name",
    "repo_url",
    "repo_ref",
    "run_mode",
    "test_types",
    "spec_sources",
    "spec_intake_pending",
    "repo_knowledge",
    "workflow_version",
    "requirements_mode",
    "app_url",
    "launch_app",
    "active_stage",
    "stages",
    "artifacts",
    "approved",
    "revisions",
    "budget",
    "case_budget",
    "confidence_thresholds",
    "environment",
    "completed_reason",
    "runtime_provenance",
    "skill_provenance",
    "skill_tool_trace",
    "invocations",
    "telemetry",
    "heal_rounds",
    "test_reviews",
)


def _confidence_thresholds(value) -> dict[str, int]:
    """Per-run review gates (plan, design, test). Each must be a whole number from the hard gate range 95 to 100."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(settings.REVIEW_KINDS):
        raise ValueError(f"confidence_thresholds maps {', '.join(settings.REVIEW_KINDS)} to whole numbers.")
    out = {}
    for kind, number in value.items():
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError(f"confidence_thresholds.{kind} must be a whole number.")
        if not settings.REVIEW_GATE_FLOOR <= number <= settings.REVIEW_GATE_CEILING:
            raise ValueError(
                f"confidence_thresholds.{kind} must be from {settings.REVIEW_GATE_FLOOR} to "
                f"{settings.REVIEW_GATE_CEILING}: review gates are hard gates."
            )
        out[kind] = number
    return out


def _apply_run_name(state: dict, body: dict) -> tuple:
    """Set the run name in ``state``; returns (old name, new name)."""
    old = state.get("run_name")
    name = _run_name(body.get("run_name"), state.get("repo_url") or "")
    state["run_name"] = name
    return old, name


def _run_name(value, url: str) -> str:
    """Validated user run name; empty means '<owner/repo> · <YYYY-MM-DD HH:MM>' computed here."""
    if value is not None and not isinstance(value, str):
        raise ValueError("Run name must be text.")
    name = (value or "").strip()
    if len(name) > 80:
        raise ValueError("Run name must be at most 80 characters.")
    if not name.isprintable():
        raise ValueError("Run name must contain printable characters only.")
    if name:
        return name
    match = re.search(r"github\.com[/:]([^/\s]+/[^/\s#?]+?)(?:\.git)?(?:[/#?\s]|$)", url)
    repo = match.group(1) if match else "run"
    return f"{repo} · {datetime.now().strftime('%Y-%m-%d %H:%M')}"[:80]


def decisions(pending: dict | None, status: str, consumed: list) -> list[str]:
    if not pending or status in {"complete", "stopped", "interrupted"} or pending["id"] in consumed:
        return []
    kind = pending["kind"]
    if kind == "review":
        return ["approve", "revise", "stop"]
    if kind == "error":
        return ["retry", "stop"]
    if kind == "tool":
        return [
            "approve",
            "deny",
            *(["skip"] if pending["stage"] in {"environment_setup", "test_execution"} else []),
            "stop",
        ]
    return []


def _budget_value(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
        raise ValueError("Cases per requirement must be an integer from 1 to 10.")
    return value


class LocalAPI(EvidenceAPI, SettingsAPI, McpAPI, ConfidenceAPI):
    def __init__(
        self,
        runs: Path,
        legacy: Path | tuple[Path, ...] | None = None,
        runtime_factory=None,
        requirements_mode="deterministic",
    ):
        self.runs = runs.resolve()
        self.runs.mkdir(parents=True, exist_ok=True, mode=0o700)
        roots = () if legacy is None else (legacy,) if isinstance(legacy, Path) else legacy
        self.legacy_roots = tuple(dict.fromkeys(p.resolve() for p in roots if p.resolve() != self.runs))
        self.runtime_factory = runtime_factory
        self.requirements_mode = requirements_mode
        self.lock = RLock()
        self.service = RuntimeService()
        self.host: Harness | None = None
        self.job = None
        self.job_run: str | None = None
        self.errors: dict[str, str] = {}

    def run_path(self, key: str, *, create=False) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key):
            raise ValueError("Invalid run ID.")
        path = self.runs / key
        if not create and not (path / "run.json").is_file():
            for root in self.legacy_roots:
                candidate = root / key
                if (candidate / "run.json").is_file():
                    path = candidate
                    break
        root = path.parent
        if path.resolve().parent != root or (not create and not (path / "run.json").is_file()):
            raise FileNotFoundError("Run not found.")
        return path

    def harvest(self):
        if self.job and self.job.done():
            completed = self.job
            self.job = None
            try:
                completed.result()
            except Exception as exc:
                self.errors[self.job_run] = public(str(exc))
                self.operation_log(self.job_run, "failed", public(traceback.format_exc()))
                path = self.run_path(self.job_run) / "run.json"
                try:
                    view = read_json(path)
                    if not view.get("pending"):
                        view.update(status="interrupted", error=public(str(exc)))
                        atomic_json(path, view)
                except PermissionError:
                    # The operation is finished even when its state directory is persistently inaccessible.
                    # Do not report it as an indefinitely running job or replay the approved action.
                    self.errors[self.job_run] += (
                        " State file is inaccessible; check directory permissions and retry the connection."
                    )

    def operation_log(self, key, status, error=""):
        path = self.run_path(key) / "workflow.log"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now(UTC).isoformat()} operation {status} {public(error)}\n")

    def busy(self) -> bool:
        self.harvest()
        return self.job is not None

    def _eta(self, key: str, view: dict) -> dict:
        try:
            live = bool(self.job and self.job_run == key)
            stale = view.get("status") == "running" and not live  # API restarted mid-run
            return run_eta(self.run_path(key), view, live, interrupted=stale)
        except Exception:  # progress hints must never break the run read
            logging.getLogger(__name__).warning("ETA unavailable for run %s", key, exc_info=True)
            return {"stages": []}

    def _case_budget(self, key: str, view: dict) -> dict | None:
        try:
            return case_budget_view(self.run_path(key), view)
        except Exception:
            logging.getLogger(__name__).warning("Case budget unavailable for run %s", key, exc_info=True)
            return None

    def set_case_budget(self, key: str, body: dict) -> dict:
        with self.lock:
            if self.busy():
                raise RuntimeError("Wait for the active operation before changing the case budget.")
            value = _budget_value(body.get("per_requirement"))
            path = self.run_path(key) / "run.json"
            view = read_json(path)
            state = view.get("state", {})
            if "test_design" in state.get("approved", {}):
                raise RuntimeError("Test design is already approved; the case budget can no longer change.")
            state["case_budget"] = {"per_requirement": value}
            view["state"] = state
            atomic_json(path, view)
            with (self.run_path(key) / "audit.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "event": "case_budget_changed",
                            "at": datetime.now(UTC).isoformat(),
                            "per_requirement": value,
                        }
                    )
                    + "\n"
                )
            return {"id": key, "per_requirement": value}

    def read(self, key: str) -> dict:
        with self.lock:
            self.harvest()
        return self._view(key)

    def _view(self, key: str) -> dict:  # file reads happen outside the lock (see list_runs)
        view = read_json(self.run_path(key) / "run.json")
        state = view.get("state", {})
        pending = view.get("pending")
        actions = decisions(pending, view.get("status", "new"), view.get("consumed", []))
        status = view.get("status", "new")
        orphaned = not (self.job and self.job_run == key)
        if orphaned and (
            status in {"running", "stopping"}  # the process that ran it ended (API restart or crash)
            or (
                pending
                and status not in {"stopped", "complete"}
                and pending["id"] in view.get("consumed", [])
            )
        ):
            status = "interrupted"
        try:
            errors = run_errors(self.run_path(key), {**view, "operation_error": self.errors.get(key)})
        except Exception:
            errors = {"items": [], "counts": {}}
        return public(
            {
                "id": key,
                "errors": errors,
                "progress": {
                    **progress(view),
                    "eta": self._eta(key, view),
                    "case_budget": self._case_budget(key, view),
                },
                "status": status,
                "pending": {k: v for k, v in pending.items() if k != "state"} if pending else None,
                "state": {k: state[k] for k in PUBLIC_STATE if k in state},
                "actions": [] if self.job else actions,
                "busy": bool(self.job and self.job_run == key),
                "pending_consumed": bool(pending and pending["id"] in view.get("consumed", [])),
                "stop_note": view.get("stop_note"),
                "run_directory": str(self.run_path(key)),
                "operation": read_operation(self.run_path(key)) if self.job and self.job_run == key else {},
                "service_busy": bool(self.job),
                "operation_error": self.errors.get(key),
                "error": view.get("error"),
                "has_zip": bool(state.get("deliverable_zip")),
            }
        )

    def list_runs(self) -> dict:
        with self.lock:
            self.harvest()
            files = [file for root in (self.runs, *self.legacy_roots) for file in root.glob("*/run.json")]
        rows, seen = [], set()
        for file in files:
            key = file.parent.name
            if key in seen:
                continue
            seen.add(key)
            try:
                run = self._view(key)
                rows.append(
                    {
                        "id": key,
                        "progress": run["progress"],
                        "repo_url": run["state"].get("repo_url"),
                        "run_name": run["state"].get("run_name"),
                        "status": run["status"],
                        "stage": (run["pending"] or {}).get("stage") or run["state"].get("active_stage"),
                        "verdict": run["state"]
                        .get("artifacts", {})
                        .get("test_execution", {})
                        .get("qa_report", {})
                        .get("verdict"),
                        "busy": run["busy"],
                        "pending": bool(run["actions"]),
                        "updated": file.stat().st_mtime,
                    }
                )
            except (ValueError, OSError):
                try:
                    updated = file.stat().st_mtime
                except OSError:  # deleted while the listing was running
                    continue
                rows.append({"id": key, "status": "unreadable", "updated": updated})
        return {
            "runs": sorted(rows, key=lambda row: row["updated"], reverse=True),
            "busy": bool(self.job),
        }

    async def replace_host(self, key: str):
        previous = self.host
        self.host = None
        if previous and getattr(previous.runtime, "close", None):
            await previous.runtime.close()
        if self.runtime_factory:
            runtime = self.runtime_factory()
        else:
            validate_gateway_settings()
            cur = current_settings()
            runtime = MAFHarnessRuntime(
                api_key=cur["api_key"],
                model=cur["model"],
                base_url=cur["base_url"] or None,
                skills_root=paths.skills_dir(),
            )
        self.host = Harness(
            self.run_path(key),
            skills_root=paths.skills_dir(),
            runtime=runtime,
            requirements_mode=self.requirements_mode,
        )

    def start(self, body: dict) -> dict:
        with self.lock:
            if self.busy():
                raise RuntimeError("Another operation is running. Wait for its approval gate.")
            knowledge = body.get("repo_knowledge") or ""
            if not isinstance(knowledge, str) or len(knowledge) > 32000:
                raise ValueError("Repository knowledge must be text of at most 32,000 characters.")
            opts = parse_run_options(body)
            url = body.get("repo_url")
            if url is None and opts["run_mode"] == "greenfield":
                url = ""
            app_url = body.get("app_url") or None
            launch = body.get("start_app", False)
            component = body.get("app_component") or None
            if component is not None and (
                not isinstance(component, str)
                or ".." in component.split("/")
                or "\\" in component
                or component.startswith("/")
                or ":" in component
            ):
                raise ValueError("Application component must be a relative repository directory")
            if (
                not isinstance(url, str)
                or (app_url is not None and not isinstance(app_url, str))
                or not isinstance(launch, bool)
            ):
                raise ValueError("Repository and application URLs must be text; start_app must be boolean.")
            thresholds = _confidence_thresholds(body.get("confidence_thresholds"))
            budget = body.get("case_budget")
            if budget is not None:
                budget = _budget_value(
                    (budget or {}).get("per_requirement") if isinstance(budget, dict) else None
                )
            if url.strip() or opts["run_mode"] == "brownfield":
                validate_github_url(url)
            run_name = _run_name(body.get("run_name"), url)
            if url.strip().startswith("git clone "):
                url = validate_github_url(url)[0]
            if app_url and not is_local_app_url(app_url):
                raise ValueError("Application URL must be local to the execution host.")
            if not self.runtime_factory:
                validate_gateway_settings()
            key = uuid4().hex[:12]
            path = self.run_path(key, create=True)
            path.mkdir(mode=0o700)
            try:
                prompt_settings.save(
                    path / "prompts.json", prompt_settings.load(self.runs.parent / "prompts.json")
                )
                # Persist a visible new run before dispatch; Harness.start will populate the real checkpoint.
                atomic_json(
                    path / "run.json",
                    {
                        "status": "new",
                        "state": {
                            "repo_url": url,
                            "run_name": run_name,
                            "run_mode": opts["run_mode"],
                            "test_types": opts["test_types"],
                            **({"confidence_thresholds": thresholds} if thresholds else {}),
                        },
                        "consumed": [],
                    },
                )

                async def begin():
                    await self.replace_host(key)
                    await self.host.start(
                        url,
                        app_url,
                        launch,
                        component,
                        knowledge,
                        budget,
                        run_name,
                        run_mode=opts["run_mode"],
                        confidence_thresholds=thresholds or None,
                        test_types=opts["test_types"],
                        spec_sources=opts["spec_sources"],
                        documents=opts["documents"],
                        jira_token=opts[
                            "jira_token"
                        ],  # in memory only; the desktop keychain will provide it later
                    )

                self.job_run = key
                self.operation_log(key, "starting repository workflow; waiting for clone approval")
                self.job = self.service.submit(begin())
            except BaseException:  # nothing was dispatched: do not leave a half-created run behind
                shutil.rmtree(path, ignore_errors=True)
                raise
            return {"id": key}

    def respond(self, key: str, body: dict) -> dict:
        with self.lock:
            if self.busy():
                raise RuntimeError("Another operation is running.")
            run = self.read(key)
            pending = run["pending"]
            decision = body.get("decision")
            feedback = body.get("feedback", "")
            if not pending or body.get("request_id") != pending["id"] or decision not in run["actions"]:
                raise RuntimeError("Stale, consumed or unavailable approval. Refresh the run.")
            if not isinstance(feedback, str) or len(feedback) > 20000:
                raise ValueError("Feedback must be text of at most 20,000 characters.")
            if (
                decision == "revise"
                and pending["stage"] not in {"environment_setup", "test_execution"}
                and not feedback.strip()
            ):
                raise ValueError("Revision instructions are required.")

            async def answer():
                if not self.host or self.host.run_dir != self.run_path(key):
                    await self.replace_host(key)
                await self.host.respond(pending["id"], {"decision": decision, "feedback": feedback})

            self.errors.pop(key, None)
            self.job_run = key
            self.operation_log(key, f"responding to {pending['stage']} {pending['kind']} gate: {decision}")
            self.job = self.service.submit(answer())
            return {"id": key}

    def reconnect(self, key: str):
        with self.lock:
            if self.busy():
                raise RuntimeError("Wait for the active operation before reconnecting.")
            self.run_path(key)
            self.job_run = key
            self.errors.pop(key, None)
            self.job = self.service.submit(self.replace_host(key))
            return {"id": key}

    def cancel_clone(self, key: str):
        with self.lock:
            self.harvest()
            path = self.run_path(key)
            operation = read_operation(path)
            if not self.job or self.job_run != key or operation.get("status") != "running":
                raise RuntimeError("No active repository clone to cancel.")
            atomic_json(path / "clone_cancel.json", {"operation_id": operation["id"]})
            self.operation_log(key, "clone cancellation requested")
            return {"id": key}

    def stop_run(self, key: str):
        with self.lock:
            self.harvest()
            self.run_path(key)
            if not self.job or self.job_run != key:
                raise RuntimeError("No active operation for this run. Use its pending Stop decision.")
            path = self.run_path(key) / "run.json"
            already = read_json(path).get("status") == "stopping"
            live = bool(self.host and self.host.run_dir == self.run_path(key))
        if already:
            # The first Stop's wait is still running: a repeated Stop only reaches the task again.
            self.service.cancel_again()
            return {"id": key, "status": "stopping"}

        def mark(status, **extra):
            if self.host and self.host.run_dir == self.run_path(key):
                self.host.view.update(status=status, **extra)
                if status == "stopped":
                    self.host.audit("run_stopped_by_user", stage=self.host.state.get("active_stage"))
                self.host.save()
            else:
                view = read_json(path)
                view.update(status=status, **extra)
                atomic_json(path, view)

        def stopped():  # only once the task really ended
            mark("stopped", pending=None, stop_note=None)
            self.operation_log(key, "stopped by user; retained saved evidence")

        def still_finishing():  # the task ignored cancellation past the wait: the run stays "stopping"
            source = self.host.state if live and self.host else read_json(path).get("state", {})
            note = f"still finishing: {source.get('active_stage') or 'the current step'}"
            mark("stopping", stop_note=note)
            self.operation_log(key, note)

        # Cancel first: the loop stops at its next cancel point even if the status write is still queued.
        self.service.cancel_event.set()
        if live:
            # The live host's view is mutated on the runtime loop; writing it from this thread could raise
            # "dictionary changed size during iteration" inside json.dumps. The wait is outside self.lock.
            applied = Event()

            def mark_on_loop():
                try:
                    mark("stopping")
                finally:
                    applied.set()

            self.service.loop.call_soon_threadsafe(mark_on_loop)
            applied.wait(5)  # the loop is free within this window; the status is then visible to the UI
        else:
            mark("stopping")
        with self.lock:
            self.job = self.service.stop_active(stopped, still_finishing)
        return {"id": key}

    def rerun(self, key: str, body: dict):
        with self.lock:
            if self.busy():
                raise RuntimeError("Wait for the active operation before rerunning a step.")
            run = self.read(key)
            stage = body.get("stage")
            saved_stages = run["state"].get("stages", [])
            if stage not in saved_stages:
                raise ValueError("Unknown workflow step.")
            if any(
                s not in run["state"].get("approved", {}) for s in saved_stages[: saved_stages.index(stage)]
            ):
                raise ValueError("Approve preceding steps before rerunning this step.")
            if body.get("request_id") != (run["pending"] or {}).get("id"):
                raise RuntimeError("Run changed. Refresh before rerunning a step.")

            async def replay():
                await self.replace_host(key)
                await self.host.rerun(stage, body.get("request_id"))

            self.errors.pop(key, None)
            self.job_run = key
            self.operation_log(key, f"rerunning from {stage}")
            self.job = self.service.submit(replay())
            return {"id": key}

    def delete_run(self, key: str, body: dict) -> dict:
        with self.lock:
            if self.busy():
                raise RuntimeError("Wait for the active operation before deleting a run.")
            if body.get("confirm_run_id") != key:
                raise ValueError("Confirm the exact run ID before deleting.")
            path = self.run_path(key)
            try:
                with FileLock(str(path / "run.lock"), timeout=0):
                    pass
            except Timeout as exc:
                raise RuntimeError("This run is active in another process.") from exc
            if self.host and self.host.run_dir == path:

                async def release():
                    if getattr(self.host.runtime, "close", None):
                        await self.host.runtime.close()

                self.service.submit(release()).result(15)
                self.host = None
            trash = self.runs.parent / ".deleted-runs"  # inside this instance's root, also for legacy runs
            trash.mkdir(exist_ok=True, mode=0o700)
            destination = trash / f"{key}-{uuid4().hex}"
            shutil.move(path, destination)  # Path.rename fails when a legacy run lives on another volume
            self.errors.pop(key, None)
            return {"id": key, "deleted": True, "recoverable": True}

    def rename_run(self, key: str, body: dict) -> dict:
        with self.lock:
            if self.busy():
                raise RuntimeError("Wait for the active operation before renaming a run.")
            live = bool(self.host and self.host.run_dir == self.run_path(key))
            if not live:
                path = self.run_path(key) / "run.json"
                view = read_json(path)
                old, name = _apply_run_name(view.setdefault("state", {}), body)
                atomic_json(path, view)
        if live:

            def apply_on_loop():  # the live host's state is changed and saved on its loop, as stop marks are
                old, name = _apply_run_name(self.host.state, body)
                self.host.save()
                return old, name

            old, name = self._on_loop(apply_on_loop)  # waited for outside self.lock
        with (self.run_path(key) / "audit.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "event": "run_renamed",
                        "at": datetime.now(UTC).isoformat(),
                        "run_id": key,
                        "old_name": old,
                        "run_name": name,
                    }
                )
                + "\n"
            )
        return {"id": key, "run_name": name}

    def _on_loop(self, action, timeout: float = 30):
        """Run ``action`` on the runtime loop and return its result. Call without self.lock held."""
        outcome: Future = Future()

        def run():
            try:
                outcome.set_result(action())
            except Exception as exc:
                outcome.set_exception(exc)

        self.service.loop.call_soon_threadsafe(run)
        try:
            return outcome.result(timeout=timeout)
        except FutureTimeout as exc:
            raise RuntimeError("The runtime is busy; try again in a moment.") from exc

    def audit(self, key):
        path = self.run_path(key) / "audit.jsonl"
        events, malformed = [], 0
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        events.append(public(item))
                    else:
                        malformed += 1
                except ValueError:
                    malformed += 1
        return {"events": events, "malformed_lines": malformed}

    def close(self):
        async def shutdown():
            if self.host and getattr(self.host.runtime, "close", None):
                await self.host.runtime.close()

        if not self.busy():
            self.service.submit(shutdown()).result(15)
        self.service.close()


API_SOCKET_TIMEOUT_SECONDS = 30  # a silent or slow-loris client must not hold a server thread forever


def make_server(api: LocalAPI, token: str, port: int = 8765):
    class Handler(BaseHTTPRequestHandler):
        timeout = (
            API_SOCKET_TIMEOUT_SECONDS  # applied to each connection's socket by StreamRequestHandler.setup
        )

        def log_message(self, *_):
            pass  # URLs and feedback may contain sensitive repository information.

        def reply(self, status, data, mime="application/json", filename=None, report_asset=False):
            payload = (
                json.dumps(data).encode()
                if mime == "application/json" and not isinstance(data, bytes)
                else data
            )
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if mime == "text/html":
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'unsafe-inline' blob:; style-src 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' data: blob:; font-src data:; worker-src blob:; sandbox allow-scripts",
                )
            if report_asset and isinstance(data, bytes):
                # A report's sibling assets are fetched by its sandboxed, opaque-origin iframe, which is cross-origin.
                # Only those replies get the header; downloads, exports and API replies never do.
                self.send_header("Access-Control-Allow-Origin", "*")
            if filename:
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
                self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
            self.end_headers()
            self.wfile.write(payload)

        def handle_request(self):
            try:
                host = self.headers.get("Host", "")
                allowed_hosts = {
                    f"127.0.0.1:{self.server.server_port}",
                    f"localhost:{self.server.server_port}",
                }
                if host not in allowed_hosts or not hmac.compare_digest(
                    self.headers.get("Authorization", ""), f"Bearer {token}"
                ):
                    self.reply(403, {"error": "Local API authorization required."})
                    return
                # Browser access is through the same-origin Next.js proxy only.
                if self.headers.get("Origin"):
                    self.reply(403, {"error": "Use the local UI proxy."})
                    return
                url = urlsplit(self.path)
                parts = [unquote(part) for part in url.path.strip("/").split("/")]
                query = parse_qs(url.query)
                if self.command == "GET":
                    if parts == ["config"]:
                        self.reply(200, api.config())
                    elif parts == ["settings", "model"]:
                        self.reply(200, api.model_settings())
                    elif parts == ["mcp-servers"]:
                        self.reply(200, api.mcp_list())
                    elif parts == ["prompts"]:
                        self.reply(200, api.prompts())
                    elif parts == ["skills"]:
                        self.reply(200, api.skills())
                    elif parts == ["runs"]:
                        self.reply(200, api.list_runs())
                    elif len(parts) >= 2 and parts[0] == "runs":
                        key = parts[1]
                        if len(parts) == 2:
                            self.reply(200, api.read(key))
                        elif parts[2:] == ["prompts"]:
                            self.reply(200, api.prompts(key))
                        elif len(parts) == 4 and parts[2] == "prompts":
                            self.reply(200, api.prompts(key, parts[3]))
                        elif parts[2:] == ["audit"]:
                            self.reply(200, api.audit(key))
                        elif parts[2:] == ["observability"]:
                            self.reply(200, public(build_report(api.run_path(key), paths.skills_dir())))
                        elif parts[2:] == ["confidence"]:
                            self.reply(200, api.confidence(key, query))
                        elif parts[2:] == ["evidence"]:
                            self.reply(200, api.evidence(key))
                        elif len(parts) >= 4 and parts[2] == "evidence":
                            data, mime, name = api.evidence_file(
                                key,
                                parts[3],
                                "/".join(parts[4:]),
                                download=query.get("download") == ["1"],
                                theme=query.get("theme", [None])[0],
                            )
                            self.reply(200, data, mime, name, report_asset=bool(parts[4:]))
                        elif parts[2:] == ["download"]:
                            data, mime, name = api.download(key, query)
                            self.reply(200, data, mime, name)
                        else:
                            raise FileNotFoundError("Route not found.")
                    else:
                        raise FileNotFoundError("Route not found.")
                elif self.command == "DELETE":
                    if len(parts) == 2 and parts[0] == "mcp-servers":
                        self.reply(200, api.mcp_delete(parts[1]))
                    else:
                        raise FileNotFoundError("Route not found.")
                elif self.command == "POST":
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise ValueError("JSON body required.")
                    length = int(self.headers.get("Content-Length", "0"))
                    # 20 MB zip as base64; a greenfield run carries up to 10 MB of spec documents as base64
                    limit = 28_000_000 if parts in (["skills", "import"], ["runs"]) else 2097152
                    if not 0 < length <= limit:
                        raise ValueError(f"Body must be between 1 and {limit:,} bytes.")
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("JSON object required.")
                    if parts == ["repo-preview"]:
                        self.reply(200, public(preview_repository(str(body.get("repo_url", "")))))
                    elif parts == ["settings", "model"]:
                        self.reply(200, api.save_model_settings(body))
                    elif parts == ["settings", "model", "test"]:
                        self.reply(200, api.test_model_settings(body))
                    elif parts == ["mcp-servers"]:
                        self.reply(200, api.mcp_save(body))
                    elif parts == ["mcp-servers", "import"]:
                        self.reply(200, api.mcp_import(body))
                    elif len(parts) == 3 and parts[0] == "mcp-servers" and parts[2] == "test":
                        self.reply(200, api.mcp_test(parts[1]))
                    elif len(parts) == 3 and parts[0] == "mcp-servers" and parts[2] == "secrets":
                        self.reply(200, api.mcp_set_secrets(parts[1], body))
                    elif parts == ["prompts"]:
                        self.reply(200, api.save_prompts(body))
                    elif parts == ["skills", "import"]:
                        self.reply(200, api.import_skill(body))
                    elif len(parts) == 3 and parts[0] == "skills" and parts[2] == "approve":
                        self.reply(200, api.approve_skill(parts[1], str(body.get("tree_sha256") or "")))
                    elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "prompts":
                        self.reply(200, api.save_prompts(body, parts[1]))
                    elif parts == ["runs"]:
                        self.reply(202, api.start(body))
                    elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "delete":
                        self.reply(200, api.delete_run(parts[1], body))
                    elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel-clone":
                        self.reply(202, api.cancel_clone(parts[1]))
                    elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "case-budget":
                        self.reply(200, api.set_case_budget(parts[1], body))
                    elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "rename":
                        self.reply(200, api.rename_run(parts[1], body))
                    elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "stop":
                        self.reply(202, api.stop_run(parts[1]))
                    elif (
                        len(parts) == 3
                        and parts[0] == "runs"
                        and parts[2] in {"respond", "reconnect", "rerun"}
                    ):
                        result = (
                            api.respond(parts[1], body)
                            if parts[2] == "respond"
                            else api.rerun(parts[1], body)
                            if parts[2] == "rerun"
                            else api.reconnect(parts[1])
                        )
                        self.reply(202, result)
                    else:
                        raise FileNotFoundError("Route not found.")
            except FileNotFoundError as exc:
                self.reply(404, {"error": public(str(exc))})
            except (ValueError, TypeError) as exc:
                self.reply(400, {"error": public(str(exc))})
            except (RuntimeError, PermissionError) as exc:
                self.reply(409, {"error": public(str(exc))})
            except Exception:
                logging.getLogger(__name__).error(
                    "Local API request failed: %s", public(traceback.format_exc())
                )
                self.reply(
                    500, {"error": "Unable to read this run. Inspect local state and reconnect if needed."}
                )

        do_GET = handle_request
        do_POST = handle_request
        do_DELETE = handle_request

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def take_api_token() -> str | None:
    """Read HARNESS_API_TOKEN once, then remove it from the environment so no child process inherits it."""
    token = os.environ.pop("HARNESS_API_TOKEN", None)
    if token:
        register_secret(token)
    return token


def main():
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = take_api_token()
    if not token or len(token) < 32:
        parser.error(
            "Set HARNESS_API_TOKEN to a random value of at least 32 characters (the launcher does this)."
        )
    runs = paths.runs_dir()
    runs = (runs if runs.is_absolute() else ROOT / runs).resolve()
    runs.mkdir(parents=True, exist_ok=True, mode=0o700)
    # ponytail: one local API owns this user's run store; a shared service needs explicit job ownership.
    with FileLock(str(runs.parent / f".{runs.name}-api.lock"), timeout=0):
        api = LocalAPI(runs, (ROOT / ".harness_runs", Path.home() / ".maf-harness" / "runs"))
        server = make_server(api, token, args.port)
        print(f"Local harness API: http://127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            api.close()


if __name__ == "__main__":
    main()

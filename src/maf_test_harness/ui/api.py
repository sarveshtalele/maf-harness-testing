"""Loopback-only HTTP adapter. The existing Harness owns every workflow decision."""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import re
import traceback
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from filelock import FileLock, Timeout

from ..agent import prompt_settings
from ..agent.runtime import MAFHarnessRuntime
from ..monitoring.report import build_report
from ..repo.analyzer import validate_github_url
from ..repo.git_transport import read_operation
from ..settings import API_KEY, BASE_URL, MODEL, validate_gateway_settings
from ..storage import read_json
from ..workflow import Harness
from ..workflow_support import atomic_json
from .evidence_api import EvidenceAPI
from .presentation import public
from .progress import progress
from .runtime_service import RuntimeService
from .settings_api import SettingsAPI

ROOT = Path(__file__).resolve().parents[3]
PUBLIC_STATE = (
    "repo_url",
    "repo_ref",
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
    "environment",
    "completed_reason",
    "runtime_provenance",
    "skill_provenance",
    "skill_tool_trace",
    "invocations",
    "telemetry",
    "heal_rounds",
    "prompt_records",
)


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


class LocalAPI(EvidenceAPI, SettingsAPI):
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

    def read(self, key: str) -> dict:
        with self.lock:
            self.harvest()
            view = read_json(self.run_path(key) / "run.json")
            state = view.get("state", {})
            pending = view.get("pending")
            actions = decisions(pending, view.get("status", "new"), view.get("consumed", []))
            status = view.get("status", "new")
            if (
                pending
                and pending["id"] in view.get("consumed", [])
                and not (self.job and self.job_run == key)
            ):
                status = "interrupted"
            return public(
                {
                    "id": key,
                    "progress": progress(view),
                    "status": status,
                    "pending": {k: v for k, v in pending.items() if k != "state"} if pending else None,
                    "state": {k: state[k] for k in PUBLIC_STATE if k in state},
                    "actions": [] if self.job else actions,
                    "busy": bool(self.job and self.job_run == key),
                    "pending_consumed": bool(pending and pending["id"] in view.get("consumed", [])),
                    "run_directory": str(self.run_path(key)),
                    "operation": read_operation(self.run_path(key))
                    if self.job and self.job_run == key
                    else {},
                    "service_busy": bool(self.job),
                    "operation_error": self.errors.get(key),
                    "error": view.get("error"),
                    "has_zip": bool(state.get("deliverable_zip")),
                }
            )

    def list_runs(self) -> dict:
        with self.lock:
            self.harvest()
            rows, seen = [], set()
            for root in (self.runs, *self.legacy_roots):
                if not root:
                    continue
                for file in root.glob("*/run.json"):
                    key = file.parent.name
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        run = self.read(key)
                        rows.append(
                            {
                                "id": key,
                                "progress": run["progress"],
                                "repo_url": run["state"].get("repo_url"),
                                "status": run["status"],
                                "stage": (run["pending"] or {}).get("stage")
                                or run["state"].get("active_stage"),
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
                        rows.append({"id": key, "status": "unreadable", "updated": file.stat().st_mtime})
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
            runtime = MAFHarnessRuntime(
                api_key=API_KEY, model=MODEL, base_url=BASE_URL, skills_root=ROOT / "skills"
            )
        self.host = Harness(
            self.run_path(key),
            skills_root=ROOT / "skills",
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
            url = body.get("repo_url")
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
            validate_github_url(url)
            if url.strip().startswith("git clone "):
                url = validate_github_url(url)[0]
            if app_url and not re.fullmatch(
                r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/\S*)?", app_url.strip()
            ):
                raise ValueError("Application URL must be local to the execution host.")
            if not self.runtime_factory:
                validate_gateway_settings()
            key = uuid4().hex[:12]
            path = self.run_path(key, create=True)
            path.mkdir(mode=0o700)
            prompt_settings.save(
                path / "prompts.json", prompt_settings.load(self.runs.parent / "prompts.json")
            )
            # Persist a visible new run before dispatch; Harness.start will populate the real checkpoint.
            atomic_json(path / "run.json", {"status": "new", "state": {"repo_url": url}, "consumed": []})

            async def begin():
                await self.replace_host(key)
                await self.host.start(url, app_url, launch, component, knowledge)

            self.job_run = key
            self.operation_log(key, "starting repository workflow; waiting for clone approval")
            self.job = self.service.submit(begin())
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
            trash = path.parent.parent / ".deleted-runs"
            trash.mkdir(exist_ok=True, mode=0o700)
            destination = trash / f"{key}-{uuid4().hex}"
            path.rename(destination)
            self.errors.pop(key, None)
            return {"id": key, "deleted": True, "recoverable": True}

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


def make_server(api: LocalAPI, token: str, port: int = 8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # URLs and feedback may contain sensitive repository information.

        def reply(self, status, data, mime="application/json", filename=None):
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
                            self.reply(200, public(build_report(api.run_path(key), ROOT / "skills")))
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
                            self.reply(200, data, mime, name)
                        elif parts[2:] == ["download"]:
                            data, mime, name = api.download(key, query)
                            self.reply(200, data, mime, name)
                        else:
                            raise FileNotFoundError("Route not found.")
                    else:
                        raise FileNotFoundError("Route not found.")
                elif self.command == "POST":
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise ValueError("JSON body required.")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 2097152:
                        raise ValueError("Body must be between 1 and 2,097,152 bytes.")
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("JSON object required.")
                    if parts == ["prompts"]:
                        self.reply(200, api.save_prompts(body))
                    elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "prompts":
                        self.reply(200, api.save_prompts(body, parts[1]))
                    elif parts == ["runs"]:
                        self.reply(202, api.start(body))
                    elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "delete":
                        self.reply(200, api.delete_run(parts[1], body))
                    elif len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel-clone":
                        self.reply(202, api.cancel_clone(parts[1]))
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

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = os.getenv("HARNESS_API_TOKEN")
    if not token or len(token) < 32:
        parser.error(
            "Set HARNESS_API_TOKEN to a random value of at least 32 characters (the launcher does this)."
        )
    runs = Path(os.getenv("HARNESS_RUNS_DIR") or ROOT / ".maf-harness" / "runs").expanduser()
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

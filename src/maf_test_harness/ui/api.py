"""Loopback-only HTTP adapter. The existing Harness owns every workflow decision."""

from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

import yaml
from filelock import FileLock, Timeout

from ..agent import prompt_settings
from ..agent.personas import PERSONAS
from ..agent.runtime import MAFHarnessRuntime
from ..hooks.builtin import redact
from ..llm.usage import DailyUsageLedger
from ..monitoring.report import build_report, render_markdown
from ..repo.analyzer import validate_github_url
from ..settings import API_KEY, BASE_URL, MODEL, validate_gateway_settings
from ..skills.config import COMPANION_PURPOSE, SKILL_NAMES
from ..skills.registry import FRONTMATTER_RE, lint_all, load_lock, verify_lock
from ..workflow import STAGES, Harness
from .artifact_presenter import json_to_markdown
from .evidence import MIMES, file_path, inventory
from .runtime_service import RuntimeService

ROOT = Path(__file__).resolve().parents[3]
PUBLIC_STATE = (
    "repo_url",
    "repo_ref",
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


def public(value):
    """Redact secrets on the presentation boundary without changing checkpoint hashes."""
    if isinstance(value, str):
        return redact(value)[0]
    if isinstance(value, list):
        return [public(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.search(r"(?i)^(api_key|password|secret|access_token)$", key)
            else public(item)
            for key, item in value.items()
        }
    return value


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


class LocalAPI:
    def __init__(self, runs: Path, legacy: Path | None = None, runtime_factory=None):
        self.runs = runs.resolve()
        self.runs.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.legacy = legacy.resolve() if legacy and legacy.resolve() != self.runs else None
        self.runtime_factory = runtime_factory
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
        if not create and not (path / "run.json").is_file() and self.legacy:
            path = self.legacy / key
        root = path.parent
        if path.resolve().parent != root or (not create and not (path / "run.json").is_file()):
            raise FileNotFoundError("Run not found.")
        return path

    def harvest(self):
        if self.job and self.job.done():
            try:
                self.job.result()
            except Exception as exc:
                self.errors[self.job_run] = public(str(exc))
            self.job = None

    def busy(self) -> bool:
        self.harvest()
        return self.job is not None

    def read(self, key: str) -> dict:
        with self.lock:
            self.harvest()
            view = json.loads((self.run_path(key) / "run.json").read_text(encoding="utf-8"))
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
                    "status": status,
                    "pending": {k: v for k, v in pending.items() if k != "state"} if pending else None,
                    "state": {k: state[k] for k in PUBLIC_STATE if k in state},
                    "actions": [] if self.job else actions,
                    "busy": bool(self.job and self.job_run == key),
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
            for root in (self.runs, self.legacy):
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
        self.host = Harness(self.run_path(key), skills_root=ROOT / "skills", runtime=runtime)

    def prompts(self, key: str | None = None, record_id: str | None = None) -> dict:
        with self.lock:
            folder = self.run_path(key) if key else self.runs.parent
            if record_id:
                if not re.fullmatch(r"[a-f0-9]{32}", record_id):
                    raise ValueError("Invalid prompt record ID")
                path = folder / "prompts" / f"{record_id}.json"
                if not path.is_file():
                    raise FileNotFoundError("Prompt record unavailable")
                return public(json.loads(path.read_text()))
            settings = prompt_settings.load(folder / "prompts.json")
            records = self.read(key)["state"].get("prompt_records", []) if key else []
            return public(
                {
                    "settings": settings,
                    "defaults": prompt_settings.defaults(),
                    "records": records,
                    "host_stages": ["repo_analysis", "environment_setup", "test_execution"],
                }
            )

    def save_prompts(self, body: dict, key: str | None = None) -> dict:
        with self.lock:
            if key and self.busy():
                raise RuntimeError(
                    "Wait for the current operation to finish before changing this run's prompts."
                )
            folder = self.run_path(key) if key else self.runs.parent
            current = prompt_settings.load(folder / "prompts.json")
            if set(body) == {"override_key", "override_prompt"}:
                key_name, text = body["override_key"], body["override_prompt"]
                if not isinstance(key_name, str) or key_name.split(":")[0] not in PERSONAS:
                    raise ValueError("Unknown prompt override stage")
                overrides = dict(current["overrides"])
                if text is None:
                    overrides.pop(key_name, None)
                else:
                    overrides[key_name] = text
                body = {"overrides": overrides}
            settings = prompt_settings.save(folder / "prompts.json", {**current, **body})
            if key:
                event = {
                    "event": "prompts_saved",
                    "at": __import__("datetime")
                    .datetime.now(__import__("datetime").timezone.utc)
                    .isoformat(),
                    "stage": "prompt_settings",
                    "sha256": __import__("hashlib")
                    .sha256(json.dumps(settings, sort_keys=True).encode())
                    .hexdigest(),
                }
                with (folder / "audit.jsonl").open("a") as stream:
                    stream.write(json.dumps(event) + "\n")
            return public({"settings": settings})

    def start(self, body: dict) -> dict:
        with self.lock:
            if self.busy():
                raise RuntimeError("Another operation is running. Wait for its approval gate.")
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
            (path / "run.json").write_text(
                json.dumps({"status": "new", "state": {"repo_url": url}, "consumed": []}), encoding="utf-8"
            )

            async def begin():
                await self.replace_host(key)
                await self.host.start(url, app_url, launch, component)

            self.job_run = key
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

    def skills(self) -> dict:
        root = ROOT / "skills"
        lock = load_lock(root)
        problems = verify_lock(root)
        cards = []
        for report in lint_all(root):
            directory = Path(report.path)
            source = (
                (directory / "SKILL.md").read_text(encoding="utf-8")
                if (directory / "SKILL.md").is_file()
                else ""
            )
            match = FRONTMATTER_RE.match(source)
            try:
                meta = yaml.safe_load(match.group(1)) if match else {}
            except yaml.YAMLError:
                meta = {}
            meta = meta if isinstance(meta, dict) else {}
            entry = lock.get("skills", {}).get(report.name)
            issues = [p for p in problems if p.startswith(report.name + "/")]
            cards.append(
                {
                    "name": report.name,
                    "description": meta.get("description", "No description provided."),
                    "role": "Primary"
                    if report.name in SKILL_NAMES.values()
                    else "Companion"
                    if report.name in COMPANION_PURPOSE
                    else "Host report validator",
                    "primary_stages": [stage for stage, name in SKILL_NAMES.items() if name == report.name],
                    "purpose": COMPANION_PURPOSE.get(report.name),
                    "metadata": meta,
                    "instructions": source[match.end() :] if match else source,
                    "resources": [
                        p.relative_to(directory).as_posix()
                        for p in sorted(directory.rglob("*"))
                        if p.is_file() and "__pycache__" not in p.parts
                    ],
                    "reviewed_scripts": lock.get("reviewed_scripts", {}).get(report.name, {}),
                    "sha256": report.sha256,
                    "valid": report.ok and not issues,
                    "errors": report.errors + issues,
                    "warnings": report.warnings,
                    "provenance": entry or {"source": "Project-owned skill"},
                    "locked": entry is not None,
                }
            )
        return public({"skills": cards, "upstream": lock.get("upstream", {})})

    def download(self, key: str, query: dict) -> tuple[bytes, str, str]:
        run = self.read(key)
        kind = query.get("kind", [""])[0]
        fmt = query.get("format", ["json"])[0]
        if kind == "zip":
            state = json.loads((self.run_path(key) / "run.json").read_text(encoding="utf-8")).get("state", {})
            raw = state.get("deliverable_zip")
            if not raw:
                raise FileNotFoundError("Deliverable is not available.")
            path = Path(raw).resolve()
            if (
                not path.is_relative_to(self.run_path(key).resolve())
                or path.suffix != ".zip"
                or not path.is_file()
            ):
                raise ValueError("Deliverable must be a ZIP inside the selected run.")
            return path.read_bytes(), "application/zip", path.name
        if kind == "audit":
            return (
                ("\n".join(json.dumps(e) for e in self.audit(key)["events"]) + "\n").encode(),
                "application/x-ndjson",
                "audit.jsonl",
            )
        if kind == "source":
            files = run["state"].get("artifacts", {}).get("test_generation", {}).get("files", [])
            index = int(query.get("index", ["-1"])[0])
            if index < 0 or index >= len(files):
                raise FileNotFoundError("Generated file not found.")
            item = files[index]
            return item.get("content", "").encode(), "text/plain", re.split(r"[/\\]", item["path"])[-1]
        if kind == "observability":
            data = public(build_report(self.run_path(key), ROOT / "skills"))
            md = render_markdown(data)
        else:
            artifacts = run["state"].get("artifacts", {})
            if kind == "qa":
                execution = artifacts.get("test_execution", {})
                data, md = execution.get("qa_report"), execution.get("markdown", "")
            else:
                data = artifacts.get(kind)
                md = json_to_markdown(data, title=kind)
            if data is None:
                raise FileNotFoundError("Artifact not available.")
        if fmt not in {"json", "md"}:
            raise ValueError("Unsupported export format.")
        content = md if fmt == "md" else json.dumps(data, indent=2, ensure_ascii=False)
        return (
            public(content).encode(),
            "text/markdown" if fmt == "md" else "application/json",
            f"{kind}.{fmt}",
        )

    def evidence(self, key: str):
        return public(inventory(self.run_path(key)))

    def evidence_file(self, key: str, file_id: str, asset="", *, download=False, theme=None):
        path = file_path(self.run_path(key), file_id, "" if asset == "view.html" else asset)
        mime = MIMES.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "text/plain")
        if path.suffix.lower() in {".log", ".md", ".xml"} and not download and not asset:
            with path.open("rb") as stream:
                content = stream.read(512 * 1024)
            return (
                public(
                    {
                        "text": content.decode("utf-8", errors="replace"),
                        "truncated": path.stat().st_size > len(content),
                    }
                ),
                "application/json",
                None,
            )
        from .evidence import clean_content

        content = clean_content(path)
        if mime == "text/html" and not download:
            # Playwright reads theme settings from storage. An opaque sandbox cannot access real storage;
            # provide report-local memory instead of granting it access to the UI's origin.
            shim = b"""<script>for(const name of ['localStorage','sessionStorage']){const values=new Map();Object.defineProperty(window,name,{value:{getItem:k=>values.get(String(k))??null,setItem:(k,v)=>values.set(String(k),String(v)),removeItem:k=>values.delete(String(k)),clear:()=>values.clear(),key:i=>[...values.keys()][i]??null,get length(){return values.size}}});}</script>"""
            if theme in {"light", "dark"}:
                shim += (
                    f"<script>localStorage.theme='{theme}-mode';</script>"
                    "<style>html,body{background:var(--color-canvas-default)}</style>"
                ).encode()
            match = re.search(rb"<head[^>]*>", content, re.I)
            at = match.end() if match else 0
            content = content[:at] + shim + content[at:]
        return content, mime, path.name if download or path.suffix.lower() == ".zip" else None

    def config(self):
        return public(
            {
                "model": MODEL,
                "base_url": BASE_URL,
                "key_configured": bool(API_KEY),
                "daily": DailyUsageLedger().snapshot(),
                "stages": STAGES,
                "personas": {
                    stage: {
                        "name": PERSONAS[stage].name,
                        "role": PERSONAS[stage].role,
                        "goal": PERSONAS[stage].goal,
                    }
                    for stage in STAGES
                },
            }
        )

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
                    elif len(parts) == 3 and parts[0] == "runs" and parts[2] in {"respond", "reconnect"}:
                        result = (
                            api.respond(parts[1], body) if parts[2] == "respond" else api.reconnect(parts[1])
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
                self.reply(
                    500, {"error": "Unable to read this run. Inspect local state and reconnect if needed."}
                )

        do_GET = handle_request
        do_POST = handle_request

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = os.getenv("HARNESS_API_TOKEN")
    if not token or len(token) < 32:
        parser.error(
            "Set HARNESS_API_TOKEN to a random value of at least 32 characters (the launcher does this)."
        )
    runs = Path(os.getenv("HARNESS_RUNS_DIR") or Path.home() / ".maf-harness" / "runs")
    runs.mkdir(parents=True, exist_ok=True, mode=0o700)
    # ponytail: one local API owns this user's run store; a shared service needs explicit job ownership.
    with FileLock(str(runs.parent / f".{runs.name}-api.lock"), timeout=0):
        api = LocalAPI(runs, ROOT / ".harness_runs")
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

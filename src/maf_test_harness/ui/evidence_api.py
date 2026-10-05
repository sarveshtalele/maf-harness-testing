"""Focused evidence api operations."""

from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path

import yaml

from ..monitoring.report import build_report, render_markdown
from ..skills.config import COMPANION_PURPOSE, HOST_SKILL_NAMES, SKILL_NAMES
from ..skills.registry import FRONTMATTER_RE, lint_all, load_lock, verify_lock
from .artifact_presenter import json_to_markdown
from .evidence import MIMES, file_path, inventory
from .presentation import public

ROOT = Path(__file__).resolve().parents[3]


class EvidenceAPI:
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
                    "role": "Host-static primary"
                    if report.name in HOST_SKILL_NAMES.values()
                    else "Primary"
                    if report.name in SKILL_NAMES.values()
                    else "Companion"
                    if report.name in COMPANION_PURPOSE
                    else "Host report validator",
                    "primary_stages": [
                        stage
                        for stage, name in [*SKILL_NAMES.items(), *HOST_SKILL_NAMES.items()]
                        if name == report.name
                    ],
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
        executions = []
        for run in self.list_runs()["runs"][:100]:
            try:
                state = json.loads((self.run_path(run["id"]) / "run.json").read_text(encoding="utf-8")).get(
                    "state", {}
                )
            except (OSError, ValueError):
                continue
            for stage, info in state.get("skill_tool_trace", {}).items():
                names = info.get("loaded_skills", []) or info.get("skills", [])
                for name in names:
                    scripts = [
                        r
                        for r in state.get("skill_script_runs", [])
                        if r.get("skill") == name and r.get("stage") == stage
                    ]
                    executions.append(
                        {
                            "run_id": run["id"],
                            "skill": name,
                            "stage": stage,
                            "mode": info.get("execution_mode", "model-instructions"),
                            "status": "failed"
                            if any(r.get("exit_code") != 0 for r in scripts)
                            else "script-executed"
                            if scripts
                            else "package-loaded; no script recorded",
                            "scripts": [
                                {k: r.get(k) for k in ("script", "sha256", "exit_code", "duration_seconds")}
                                for r in scripts
                            ],
                        }
                    )
        for card in cards:
            card["executions"] = [r for r in executions if r["skill"] == card["name"]]
        return public(
            {
                "skills": cards,
                "upstream": lock.get("upstream", {}),
                "executions": executions,
                "execution_history_limit": 100,
            }
        )

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
        if path.suffix.lower() in {".log", ".jsonl", ".md", ".xml"} and not download and not asset:
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

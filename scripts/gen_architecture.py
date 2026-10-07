#!/usr/bin/env python3
"""Generate docs/ARCHITECTURE_MAP.md: project flow, import graphs and a per-file table.

Stdlib only and deterministic (sorted, LF newlines). ``--check`` exits 1 when the committed file is stale.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "ARCHITECTURE_MAP.md"
PKG = "maf_test_harness"
SRC = "src/maf_test_harness/"
MAX_NODES = 40
TS_EXT = (".ts", ".tsx")

FLOW = """flowchart TD
  L["launchers: start-windows.cmd / start-macos.command / start-linux.sh"] --> SU["scripts/start_ui.py<br/>checks, build, serve"]
  SU --> NX["Next.js UI (frontend/app, components)"]
  SU --> API
  NX --> PX["API proxy route<br/>frontend/app/api/harness/[...path]/route.ts"]
  PX -->|HTTP, loopback| API["ui/api.py<br/>+ runtime_service, progress, eta, evidence_api, settings_api"]
  CLI["cli.py<br/>maf-harness run / trace / verify-run"] --> H
  API --> H["workflow.Harness<br/>checkpointed MAF workflow"]
  H --> S1
  subgraph PIPE["Stages (workflow_steps/*) with human gates"]
    S1["1 repo_analysis<br/>repo/analyzer, reverse_engineering"] -->|gate: clone + review| S2["2 requirements<br/>repo/requirements, deterministic"]
    S2 -->|gate| S3["3 test_planning<br/>MAF agent"]
    S3 -->|gate| S4["4 test_design<br/>batched by component"]
    S4 --> DR["design review<br/>Chief Principal Architect persona"]
    DR -->|gate| S5["5 test_generation<br/>batched, fail-soft"]
    S5 --> TR["independent test review<br/>Repository Test Reviewer"]
    TR -->|gate: source review| S6["6 environment_setup<br/>install approval"]
    S6 -->|gate| S7["7 test_execution<br/>run approval"]
    S7 --> QAR["QA report + deliverable ZIP"]
  end
  subgraph RT["Agent runtime"]
    AR["agent/runtime.py<br/>create_harness_agent + SkillsProvider"]
    MW["middleware: budget, trace, hooks"]
    RC["response cache + stage models"]
    SK[("skills/*/SKILL.md")]
  end
  S3 --> AR
  S4 --> AR
  S5 --> AR
  AR --> MW
  AR --> RC
  AR -.-> SK
  AR --> MAF[("Microsoft Agent Framework")]
  MAF --> GW[("OpenAI-compatible gateway")]
  HK["hooks/: verdict engine, redaction, write scope"] -.-> MW
  S1 --> RI["repo intake: testability, witnesses, preview, routes, stack"]
  RI -->|existing-test witnesses, retargeting| S4
  S6 --> ENV["envs/: runners, toolchains, platform_compat, recovery"]
  S7 --> ENV
  ENV --> ERR["errors/: taxonomy, classifiers, playbook"]
  S7 --> QAP["qa/: report, triage, quality, packaging"]
  TOOLS["tools/: code_intel, autofix, build_gate"] -.-> S5
  MON["monitoring/: trace, report, telemetry"] -.-> H"""


def short(path: str) -> str:
    return path[len(SRC) :] if path.startswith(SRC) else path


def group_of(path: str) -> str:
    if path.startswith(SRC):
        rest = path[len(SRC) :]
        return rest.split("/")[0] if "/" in rest else "core"
    if path.startswith("scripts/"):
        return "scripts"
    if path.startswith("skills/"):
        return "skill-packages"
    if path.startswith("frontend/"):
        return "/".join(path.split("/")[:2])
    return "launchers"


def py_files() -> list[str]:
    base = ROOT / "src" / PKG
    return sorted(p.relative_to(ROOT).as_posix() for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


def clean(value: str, limit: int = 150) -> str:
    value = " ".join(value.replace("|", "/").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def py_info(path: str, modmap: dict[str, str]) -> tuple[str, set[str]]:
    tree = ast.parse(text(path))
    doc = (ast.get_docstring(tree) or "").strip().splitlines()
    desc = clean(doc[0]) if doc else "—"
    parts = path[len("src/") : -len(".py")].split("/")
    is_init = parts[-1] == "__init__"
    pkg_parts = parts[:-1]
    if is_init:
        parts = parts[:-1]
    edges: set[str] = set()

    def add(dotted: str) -> bool:
        target = modmap.get(dotted)
        if target and target != path:
            edges.add(target)
        return target is not None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PKG):
                    add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(pkg_parts) - (node.level - 1)
                if keep < 1:
                    continue
                base = pkg_parts[:keep] + (node.module.split(".") if node.module else [])
            elif node.module and node.module.split(".")[0] == PKG:
                base = node.module.split(".")
            else:
                continue
            for alias in node.names:
                if not add(".".join([*base, alias.name])):
                    add(".".join(base))
    return desc, edges


def ts_resolve(src: str, spec: str, known: set[str]) -> str | None:
    if spec.startswith("@/"):
        stem = "frontend/" + spec[2:]
    elif spec.startswith("."):
        stem = (Path(src).parent / spec).as_posix()
        parts: list[str] = []
        for seg in stem.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg != ".":
                parts.append(seg)
        stem = "/".join(parts)
    else:
        return None
    for cand in (stem, *(stem + e for e in TS_EXT), *(f"{stem}/index{e}" for e in TS_EXT)):
        if cand in known:
            return cand
    return None


def ts_desc(src: str) -> str:
    for line in src.splitlines():
        s = line.strip()
        if not s or s.startswith(("'use ", '"use ')):
            continue
        if s.startswith("//"):
            return clean(s.lstrip("/ "))
        if s.startswith("/*"):
            return clean(s.strip("/* ").rstrip("*/ "))
        break
    names = re.findall(
        r"^export\s+(?:default\s+)?(?:async\s+)?(?:function|const|type|class|interface)\s+(\w+)", src, re.M
    )
    if not names and re.search(r"^export\s+default", src, re.M):
        names = ["default"]
    if not names:
        names = re.findall(r"^export\s+(?:async\s+)?function\s+(\w+)", src, re.M)
    return clean("Exports: " + ", ".join(names[:6]) + (", …" if len(names) > 6 else "")) if names else "—"


def collect() -> tuple[dict[str, str], dict[str, set[str]]]:
    desc: dict[str, str] = {}
    edges: dict[str, set[str]] = {}
    srcs = py_files()
    modmap: dict[str, str] = {}
    for p in srcs:
        parts = p[len("src/") : -len(".py")].split("/")
        modmap[".".join(parts[:-1] if parts[-1] == "__init__" else parts)] = p
    for p in srcs:
        desc[p], edges[p] = py_info(p, modmap)
    for p in sorted(x.relative_to(ROOT).as_posix() for x in (ROOT / "scripts").glob("*.py")):
        d, e = py_info_script(p, modmap)
        desc[p], edges[p] = d, e
    launchers = sorted(
        x.name for pat in ("start-*.cmd", "start-*.command", "start-*.sh") for x in ROOT.glob(pat)
    )
    for name in launchers:
        body = text(name)
        desc[name] = (
            "Thin launcher; delegates to scripts/start_ui.py" if "start_ui.py" in body else "Launcher"
        )
        edges[name] = (
            {"scripts/start_ui.py"}
            if "start_ui.py" in body and (ROOT / "scripts/start_ui.py").exists()
            else set()
        )
        if "start-macos.command" in body and name != "start-macos.command":
            edges[name].add("start-macos.command")
    for sk in sorted((ROOT / "skills").glob("*/SKILL.md")):
        p = sk.relative_to(ROOT).as_posix()
        m = re.search(r"^description:\s*(.+)$", text(p), re.M)
        desc[p] = clean(m.group(1).strip("\"' ")) if m else "—"
        edges[p] = {SRC + "skills/config.py"}
    fe = sorted(
        {
            *(x for x in (ROOT / "frontend/app").rglob("*") if x.suffix in TS_EXT),
            *(ROOT / "frontend/components").glob("*.tsx"),
            *(ROOT / "frontend/lib").glob("*.ts"),
        }
    )
    fe_paths = [x.relative_to(ROOT).as_posix() for x in fe]
    known = set(fe_paths)
    for p in fe_paths:
        body = text(p)
        desc[p] = ts_desc(body)
        specs = re.findall(r"""(?:from|import)\s*\(?\s*['"]([^'"]+)['"]""", body)
        edges[p] = {t for s in specs if (t := ts_resolve(p, s, known)) and t != p}
    return desc, edges


def py_info_script(path: str, modmap: dict[str, str]) -> tuple[str, set[str]]:
    tree = ast.parse(text(path))
    doc = (ast.get_docstring(tree) or "").strip().splitlines()
    out: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        out.update(modmap[n] for n in names if n in modmap)
    return (clean(doc[0]) if doc else "—"), out


def mermaid(lines: list[str] | str) -> str:
    if isinstance(lines, str):
        lines = lines.splitlines()
    return "```mermaid\n" + "\n".join(lines) + "\n```\n"


def render() -> tuple[str, int, int]:
    desc, edges = collect()
    files = sorted(desc)
    for p in files:
        edges[p] = {t for t in edges[p] if t in desc}
    rev: dict[str, set[str]] = {p: set() for p in files}
    for p in files:
        for t in edges[p]:
            rev[t].add(p)
    n_edges = sum(len(v) for v in edges.values())
    groups: dict[str, list[str]] = {}
    for p in files:
        groups.setdefault(group_of(p), []).append(p)

    o = [
        "# Architecture map",
        "",
        "<!-- Generated by scripts/gen_architecture.py; do not edit. Regenerate: python scripts/gen_architecture.py -->",
        "",
        "Generated from the code: Python imports (`ast`), TypeScript imports, launchers and skill packages. "
        "Companion to [ARCHITECTURE.md](ARCHITECTURE.md) (design, guarantees).",
        f"{len(files)} files, {n_edges} import/delegation edges.",
        "",
        "## 1. How the project works",
        "",
        mermaid(FLOW),
        "## 2. Package graph",
        "",
        "Nodes are packages; edge labels count file-level import edges.",
        "",
    ]
    gids = {g: f"g{i}" for i, g in enumerate(sorted(groups))}
    pl = ["flowchart LR"] + [f'  {gids[g]}["{g} ({len(groups[g])})"]' for g in sorted(groups)]
    counts: dict[tuple[str, str], int] = {}
    for p in files:
        for t in edges[p]:
            a, b = group_of(p), group_of(t)
            if a != b:
                counts[(a, b)] = counts.get((a, b), 0) + 1
    pl += [f"  {gids[a]} -->|{n}| {gids[b]}" for (a, b), n in sorted(counts.items())]
    o += [
        mermaid(pl),
        "## 3. Per-package file graphs",
        "",
        "Edges between files of the same package; cross-package links are in the table.",
        "",
    ]
    for g in sorted(groups):
        members = groups[g]
        chunks = [members[i : i + MAX_NODES] for i in range(0, len(members), MAX_NODES)]
        for ci, chunk in enumerate(chunks):
            suffix = f" (part {ci + 1}/{len(chunks)})" if len(chunks) > 1 else ""
            o += [f"### {g}{suffix}", ""]
            ids = {p: f"f{i}" for i, p in enumerate(chunk)}
            gl = ["flowchart LR"] + [f'  {ids[p]}["{short(p).replace(chr(34), "")}"]' for p in chunk]
            gl += [f"  {ids[p]} --> {ids[t]}" for p in chunk for t in sorted(edges[p]) if t in ids]
            o += [mermaid(gl)]
    o += [
        "## 4. File table",
        "",
        "| File | Description | Imports (internal) | Imported by |",
        "|---|---|---|---|",
    ]
    for p in files:
        imp = ", ".join(f"`{short(t)}`" for t in sorted(edges[p])) or "—"
        by = ", ".join(f"`{short(t)}`" for t in sorted(rev[p])) or "—"
        o.append(f"| `{p}` | {desc[p]} | {imp} | {by} |")
    return "\n".join(o) + "\n", len(files), n_edges


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 if docs/ARCHITECTURE_MAP.md is stale")
    args = ap.parse_args()
    content, nodes, n_edges = render()
    data = content.encode("utf-8")
    if args.check:
        if not OUT.exists() or OUT.read_bytes() != data:
            print(
                "docs/ARCHITECTURE_MAP.md is stale: run python scripts/gen_architecture.py", file=sys.stderr
            )
            return 1
        return 0
    OUT.write_bytes(data)
    print(f"wrote {OUT.relative_to(ROOT)}: {nodes} nodes, {n_edges} edges")
    return 0


if __name__ == "__main__":
    sys.exit(main())

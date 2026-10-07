"""Read-only repository preview from the public GitHub REST API (no clone, no install, no execution).

Used by the New run form to suggest optional fields. Everything it returns is an unverified hint.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from urllib.parse import quote
from urllib.request import Request, urlopen

from .analyzer import validate_github_url

MAX_FILES = 12
MAX_BYTES = 200_000
FILE_LIMIT = 64_000
TIMEOUT = 10
KNOWLEDGE_LIMIT = 3000
API = "https://api.github.com"
PREFERRED = ("frontend", "web", "ui", "client", "app")
MANIFESTS = (
    "package.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "requirements.txt",
    "pyproject.toml",
)
SIDE_FILES = ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".env.example", "Makefile")
VERSION_FILES = {".nvmrc": "node", ".node-version": "node", ".python-version": "python"}
LANGUAGES = (
    (".py", "Python"),
    (".ts", "TypeScript"),
    (".js", "JavaScript"),
    (".java", "Java"),
    (".cs", "C#"),
    (".go", "Go"),
)
NODE_FRAMEWORKS = (  # dependency, label, default port
    ("next", "Next.js", 3000),
    ("@angular/core", "Angular", 4200),
    ("@remix-run/react", "Remix", 3000),
    ("@sveltejs/kit", "SvelteKit", 5173),
    ("react-scripts", "Create React App", 3000),
    ("vite", "Vite", 5173),
    ("nuxt", "Nuxt", 3000),
    ("@vue/cli-service", "Vue CLI", 8080),
    ("vue", "Vue", 5173),
    ("svelte", "Svelte", 5173),
    ("react", "React", None),
)
PY_FRAMEWORKS = (
    ("fastapi", "FastAPI", 8000),
    ("flask", "Flask", 5000),
    ("streamlit", "Streamlit", 8501),
    ("django", "Django", 8000),
)
# fetch(op, owner, repo, ref, path) -> "info": default branch | "tree": [file paths] | "file": text or None.
Fetcher = Callable[..., object]


def github_fetch(op: str, owner: str, repo: str, ref: str | None, path: str = ""):
    """Bounded GitHub REST reads. The token (if any) goes only to api.github.com and is never logged."""
    base = f"{API}/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "maf-test-harness-preview"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    if op == "info":
        url = base
    elif op == "tree":
        url = f"{base}/git/trees/{quote(ref or 'HEAD', safe='')}?recursive=1"
    else:
        url = f"{base}/contents/{quote(path)}?ref={quote(ref or '', safe='')}"
        headers["Accept"] = "application/vnd.github.raw+json"
    with urlopen(Request(url, headers=headers), timeout=TIMEOUT) as response:
        raw = response.read(2_000_000 if op == "tree" else FILE_LIMIT)
    if op == "file":
        return raw.decode("utf-8", "replace")
    data = json.loads(raw)
    if op == "info":
        return data.get("default_branch") or "HEAD"
    return [item["path"] for item in data.get("tree", []) if item.get("type") == "blob"]


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _rank(directory: str) -> tuple[int, int, str]:
    """Lower is better: preferred folder names, then shallowest."""
    base = directory.rsplit("/", 1)[-1].lower()
    return (
        0 if base in PREFERRED else 1 if directory else 0,
        directory.count("/") + bool(directory),
        directory,
    )


def _first(pattern: str, text: str, flags: int = 0) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1) if match else None


def _node(text: str, directory: str, tree: set, versions: list) -> dict | None:
    try:
        pkg = json.loads(text)
    except ValueError:
        return None
    if not isinstance(pkg, dict):
        return None
    deps = {**(pkg.get("devDependencies") or {}), **(pkg.get("dependencies") or {})}
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    hit = next(((label, port) for dep, label, port in NODE_FRAMEWORKS if dep in deps), None)
    runnable = [name for name in ("dev", "start") if name in scripts]
    if not hit and not runnable:
        return None
    port = next(
        (p for n in runnable if (p := _first(r"(?:--port[= ]|-p |PORT=)(\d{2,5})", str(scripts[n])))), None
    )
    engines = pkg.get("engines")
    if isinstance(engines, dict) and engines.get("node"):
        versions.append(f"node {engines['node']} (package.json engines)")
    pre = f"{directory}/" if directory else ""
    manager = "pnpm" if f"{pre}pnpm-lock.yaml" in tree else "yarn" if f"{pre}yarn.lock" in tree else "npm"
    run = "npm run" if manager == "npm" else manager
    commands = [
        f"{manager} install",
        *(f"{run} {n}" for n in ("dev", "start", "build", "test") if n in scripts),
    ]
    return {
        "stack": hit[0] if hit else "Node.js",
        "port": int(port) if port else (hit[1] if hit else None),
        "explicit": bool(port),
        "commands": commands,
    }


def _python(text: str, directory: str, name: str, tree: set, versions: list) -> dict | None:
    low = text.lower()
    hit = next(
        ((label, port) for dep, label, port in PY_FRAMEWORKS if re.search(rf"(?<![\w-]){dep}(?![\w-])", low)),
        None,
    )
    if f"{directory}/manage.py".lstrip("/") in tree:
        hit = ("Django", 8000)
    if not hit:
        return None
    if requires := _first(r"requires-python\s*=\s*[\"']([^\"']+)", text):
        versions.append(f"python {requires} (pyproject)")
    install = "pip install -r requirements.txt" if name == "requirements.txt" else "pip install -e ."
    run = {
        "Django": "python manage.py runserver",
        "Flask": "flask run",
        "Streamlit": "streamlit run <app>.py",
    }
    return {
        "stack": hit[0],
        "port": hit[1],
        "explicit": False,
        # Only known start commands; an unknown module path is never guessed (see README or Dockerfile CMD).
        "commands": [install, *([run[hit[0]]] if hit[0] in run and "<" not in run[hit[0]] else [])],
    }


def _java(text: str, name: str, versions: list) -> dict | None:
    if "spring-boot" not in text:
        return None
    version = (
        _first(r"<(?:java\.version|maven\.compiler\.release)>\s*([\d.]+)", text)
        or _first(r"JavaLanguageVersion\.of\((\d+)\)", text)
        or _first(r"sourceCompatibility\s*=\s*['\"]?([\d.]+)", text)
    )
    if version:
        versions.append(f"java {version} ({name})")
    command = "mvn spring-boot:run" if name == "pom.xml" else "./gradlew bootRun"
    return {"stack": "Spring Boot", "port": 8080, "explicit": False, "commands": [command]}


def _dotnet(text: str, versions: list) -> dict | None:
    if "Microsoft.NET.Sdk.Web" not in text:
        return None
    if target := _first(r"<TargetFramework>\s*([^<\s]+)", text):
        versions.append(f"dotnet {target} (csproj)")
    return {"stack": "ASP.NET Core", "port": 5000, "explicit": False, "commands": ["dotnet run"]}


def _port_hint(files: dict[str, str], directory: str) -> int | None:
    """Port hints in the app folder, then the repository root: .env.example, compose host port, Dockerfile."""
    for scope in dict.fromkeys((directory, "")):
        pre = f"{scope}/" if scope else ""
        compose = files.get(f"{pre}docker-compose.yml") or files.get(f"{pre}docker-compose.yaml") or ""
        found = (
            _first(r"^PORT\s*=\s*[\"']?(\d{2,5})", files.get(f"{pre}.env.example", ""), re.M)
            or _first(r"[\"']?(\d{2,5}):\d{2,5}[\"']?\s*$", compose, re.M)
            or _first(r"^EXPOSE\s+(\d{2,5})", files.get(f"{pre}Dockerfile", ""), re.M)
        )
        if found:
            return int(found)
    return None


def _readme_sections(text: str) -> str:
    heads = list(re.finditer(r"^(#{1,4})\s+(.+)$", text, re.M))
    out = []
    for i, head in enumerate(heads):
        if re.search(r"install|set ?up|getting started|quick ?start|run|usage", head.group(2), re.I):
            end = next(
                (h.start() for h in heads[i + 1 :] if len(h.group(1)) <= len(head.group(1))), len(text)
            )
            out.append(f"README - {head.group(2).strip()}:\n{text[head.end() : end].strip()[:700]}")
        if len(out) == 2:
            break
    return "\n\n".join(out)


def preview_repository(url: str, fetch: Fetcher = github_fetch) -> dict:
    """Suggest New-run fields for a public GitHub repo. Raises RepoInputError (a ValueError) for a bad URL."""
    _, owner, repo, ref = validate_github_url(url, with_ref=True)
    result: dict = {
        "app_component": None,
        "app_url": None,
        "start_app_suggested": False,
        "repo_knowledge": "",
        "detected": {"files": [], "frameworks": [], "languages": []},
        "warnings": [],
    }
    warnings = result["warnings"]
    try:
        ref = ref or str(fetch("info", owner, repo, None))
        tree = list(fetch("tree", owner, repo, ref))  # type: ignore[call-overload]
    except Exception as exc:  # network, rate limit, 404, bad JSON: never fatal
        warnings.append(
            f"Could not inspect {owner}/{repo} on GitHub ({type(exc).__name__}); fill the fields manually."
        )
        return result
    treeset = set(tree)
    files: dict[str, str] = {}
    spent = 0

    def get(path: str) -> str | None:
        nonlocal spent
        if path in files:
            return files[path]
        if path not in treeset or len(files) >= MAX_FILES or spent >= MAX_BYTES:
            return None
        try:
            text = fetch("file", owner, repo, ref, path)
        except Exception as exc:
            warnings.append(f"Could not read {path} ({type(exc).__name__}).")
            return None
        if text is None:
            return None
        files[path] = str(text)
        spent += len(files[path].encode())
        return files[path]

    versions: list[str] = []
    readme = next((p for p in tree if p.lower() in {"readme.md", "readme.markdown", "readme"}), "")
    candidates = sorted(
        (
            p
            for p in tree
            if p.count("/") <= 2
            and "node_modules/" not in p
            and (p.rsplit("/", 1)[-1] in MANIFESTS or p.endswith(".csproj"))
        ),
        key=lambda p: (_rank(_dirname(p)), p),
    )
    chosen: tuple[str, dict] | None = None
    for path in candidates[:6]:
        if (text := get(path)) is None:
            continue
        directory, name = _dirname(path), path.rsplit("/", 1)[-1]
        if name == "package.json":
            info = _node(text, directory, treeset, versions)
        elif name in {"pom.xml", "build.gradle", "build.gradle.kts"}:
            info = _java(text, name, versions)
        elif name.endswith(".csproj"):
            info = _dotnet(text, versions)
        else:
            info = _python(text, directory, name, treeset, versions)
        if info and (chosen is None or _rank(directory) < _rank(chosen[0])):
            chosen = (directory, info)
    directory = chosen[0] if chosen else ""
    for scope in dict.fromkeys((directory, "")):
        pre = f"{scope}/" if scope else ""
        for name in (*SIDE_FILES, *VERSION_FILES, "global.json"):
            get(pre + name)
        if chosen and chosen[1]["stack"] == "ASP.NET Core":
            for path in tree:
                if path.startswith(pre) and path.endswith("Properties/launchSettings.json"):
                    get(path)
    for path, text in files.items():
        name = path.rsplit("/", 1)[-1]
        if name in VERSION_FILES and text.strip():
            versions.append(f"{VERSION_FILES[name]} {text.strip()[:40]} ({name})")
        elif name == "global.json" and (sdk := _first(r'"version"\s*:\s*"([^"]+)"', text)):
            versions.append(f"dotnet sdk {sdk} (global.json)")
    if readme:
        get(readme)
    result["detected"]["languages"] = [lang for ext, lang in LANGUAGES if any(p.endswith(ext) for p in tree)]
    result["detected"]["files"] = list(files)
    port = None
    if chosen:
        info = chosen[1]
        launch = "".join(t for p, t in files.items() if p.endswith("launchSettings.json"))
        hint = _first(r"https?://localhost:(\d+)", launch) or _port_hint(files, directory)
        port = info["port"] if info["explicit"] else int(hint) if hint else info["port"]
        result["app_component"] = directory
        result["app_url"] = f"http://localhost:{port}" if port else None
        result["start_app_suggested"] = True
        result["detected"]["frameworks"] = [info["stack"]]
    lines = ["Auto-detected from GitHub (unverified)", f"Repository: {owner}/{repo} @ {ref}"]
    if chosen:
        where = chosen[0] or "repository root"
        lines.append(
            f"Web app: {chosen[1]['stack']} in {where}" + (f", expected on port {port}" if port else "")
        )
        lines.append("Commands: " + "; ".join(chosen[1]["commands"]))
    if result["detected"]["languages"]:
        lines.append("Languages: " + ", ".join(result["detected"]["languages"]))
    if versions:
        lines.append("Tool versions: " + "; ".join(dict.fromkeys(versions)))
    makefile = files.get("Makefile", "")
    targets = re.findall(r"^(install|setup|run|dev|start|build|test):", makefile, re.M)
    if targets:
        lines.append("Makefile targets: " + ", ".join(dict.fromkeys(targets)))
    if readme and files.get(readme) and (section := _readme_sections(files[readme])):
        lines.append(section)
    result["repo_knowledge"] = "\n".join(lines)[:KNOWLEDGE_LIMIT]
    return result

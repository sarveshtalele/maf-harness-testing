"""Route inventory: the HTTP routes the code actually implements, extracted from source.

Live-application API tests fail most often on a wrong URL (a guessed context path, a missing ``/rest`` prefix). The
routes are facts in the code, so the host extracts them and (a) shows them to the model, (b) rejects API tests whose
URL literal matches none of them. Best effort per framework; an empty inventory disables the URL gate.
"""

from __future__ import annotations

import re
from pathlib import Path

_JAXRS_APP = re.compile(r'@ApplicationPath\(\s*"([^"]*)"\s*\)')
_JAXRS_CLASS = re.compile(
    r'@Path\(\s*"([^"]*)"\s*\)\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:public\s+)?(?:abstract\s+)?class\s+\w+'
)
_JAXRS_METHOD = re.compile(
    r'((?:@(?:GET|POST|PUT|DELETE|PATCH|HEAD)\b\s*|@Path\(\s*"[^"]*"\s*\)\s*|@\w+(?:\([^)]*\))?\s*)+)'
    r"(?:public\s+)?[\w<>\[\], ?]+\s+\w+\s*\("
)
_SPRING = re.compile(r'@(Get|Post|Put|Delete|Patch|Request)Mapping\(\s*(?:value\s*=\s*)?\{?\s*"([^"]*)"')
_SPRING_CLASS = re.compile(
    r'@RequestMapping\(\s*(?:value\s*=\s*)?"([^"]*)"\s*\)\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:public\s+)?class'
)
_PY = re.compile(r"""@(?:\w+)\.(get|post|put|delete|patch|route)\(\s*['"]([^'"]+)['"]""")
_EXPRESS = re.compile(r"""\b(?:app|router)\.(get|post|put|delete|patch)\(\s*['"`]([^'"`]+)['"`]""")


def _join(*parts: str) -> str:
    joined = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return "/" + joined


def extract_routes(root: Path, source_files: list[str], limit: int = 80) -> list[str]:
    """['GET /rest/categories', 'POST /rest/categories/{id}', ...] from JAX-RS, Spring, Flask/FastAPI and Express."""
    root = Path(root)
    texts = {}
    for rel in source_files:
        if rel.endswith((".java", ".kt", ".py", ".js", ".ts", ".mjs")):
            try:
                texts[rel] = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    app_path = next((m.group(1) for t in texts.values() for m in [_JAXRS_APP.search(t)] if m), "")
    routes: dict[str, None] = {}
    for rel, text in texts.items():
        if rel.endswith((".java", ".kt")):
            base_match = _JAXRS_CLASS.search(text)
            spring_base = _SPRING_CLASS.search(text)
            base = base_match.group(1) if base_match else (spring_base.group(1) if spring_base else "")
            for m in _JAXRS_METHOD.finditer(text):
                annotations = m.group(1)
                verb = re.search(r"@(GET|POST|PUT|DELETE|PATCH|HEAD)\b", annotations)
                if not verb or not base_match:
                    continue
                sub = re.search(r'@Path\(\s*"([^"]*)"', annotations)
                path = _join(app_path, base, re.sub(r"\s*:[^}]*", "", sub.group(1)) if sub else "")
                routes[f"{verb.group(1)} {path}"] = None
            for m in _SPRING.finditer(text):
                verb = m.group(1).upper()
                if verb != "REQUEST":
                    routes[f"{verb} {_join(base, m.group(2))}"] = None
        elif rel.endswith(".py"):
            for m in _PY.finditer(text):
                routes[f"{'GET' if m.group(1) == 'route' else m.group(1).upper()} {_join(m.group(2))}"] = None
        else:
            for m in _EXPRESS.finditer(text):
                routes[f"{m.group(1).upper()} {_join(m.group(2))}"] = None
    return sorted(routes)[:limit]


def route_prefixes(routes: list[str]) -> list[str]:
    """Static path prefixes (before the first {param} or :param) of every route."""
    return sorted({re.split(r"[{:]", r.split(" ", 1)[1])[0].rstrip("/") or "/" for r in routes})

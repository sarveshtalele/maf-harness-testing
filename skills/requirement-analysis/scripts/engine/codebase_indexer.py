"""
Codebase Indexer
Walks the repository once and builds a per-file index: detected language,
module type (api_endpoint / service / database / config / ui_component /
test / library / module), extracted symbols (functions, classes, routes)
with line numbers, and searchable path tokens. This index is the input to
the LocationResolver and PatternFinder — no LLM involved, fully
deterministic AST/regex parsing.
"""
import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", "bin", "obj", ".tox", "coverage",
    ".pytest_cache", ".mypy_cache", "migrations", ".cache",
    "vendor", "target", "out", "change-impact-output",
    "requirement-analysis-output",
}

LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".java": "java",
    ".cs": "csharp",
    ".go": "go",
}

# Module-type detection patterns (mirrors change-impact-analysis-skill's
# impact_analyzer classification so both skills agree on file roles).
_TEST_PATH_PATTERNS = [
    re.compile(r'(^|/)test(s)?/', re.IGNORECASE),
    re.compile(r'(^|/)__tests__/'),
    re.compile(r'\.test\.(js|ts|jsx|tsx|py)$'),
    re.compile(r'\.spec\.(js|ts|jsx|tsx|py)$'),
    re.compile(r'(^|/)test_[\w]+\.py$'),
    re.compile(r'[\w]+_test\.(go|py)$'),
]

_DB_PATH_PATTERNS = [
    re.compile(r'migration', re.IGNORECASE),
    re.compile(r'\.(sql)$', re.IGNORECASE),
    re.compile(r'(^|/)models?/', re.IGNORECASE),
    re.compile(r'(^|/)entities?/', re.IGNORECASE),
    re.compile(r'(^|/)schema\.', re.IGNORECASE),
    re.compile(r'(^|/)repository/', re.IGNORECASE),
]

_CONFIG_PATH_PATTERNS = [
    re.compile(r'(config|settings|appsettings|application)\.(py|js|ts|json|yaml|yml|xml|ini|env)$', re.IGNORECASE),
    re.compile(r'(^|/)config(s)?/', re.IGNORECASE),
    re.compile(r'\.env(\..*)?$'),
    re.compile(r'Dockerfile', re.IGNORECASE),
    re.compile(r'docker-compose', re.IGNORECASE),
    re.compile(r'(^|/)k8s/', re.IGNORECASE),
    re.compile(r'helm/', re.IGNORECASE),
]

_UI_PATH_PATTERNS = [
    re.compile(r'\.(jsx|tsx|vue|svelte)$'),
    re.compile(r'(^|/)components?/', re.IGNORECASE),
    re.compile(r'(^|/)pages?/', re.IGNORECASE),
    re.compile(r'(^|/)views?/', re.IGNORECASE),
]

_API_CONTENT_PATTERNS = [
    re.compile(r'@(Get|Post|Put|Delete|Patch)Mapping', re.IGNORECASE),   # Spring
    re.compile(r'@app\.(get|post|put|delete|patch)\s*\('),               # Flask / FastAPI
    re.compile(r'router\.(get|post|put|delete|patch)\s*\('),             # Express
    re.compile(r'\[(HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch)\]'),   # ASP.NET
    re.compile(r'func\s+\w+.*http\.ResponseWriter'),                     # Go net/http
    re.compile(r'@(RestController|Controller|RequestMapping)', re.IGNORECASE),
]

# Symbol-level route extraction (captures the route path string too)
_ROUTE_PATTERNS = [
    re.compile(r'@app\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)', re.IGNORECASE),       # Flask/FastAPI
    re.compile(r'router\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)', re.IGNORECASE),     # Express
    re.compile(r'@(Get|Post|Put|Delete|Patch)Mapping\s*\(\s*["\']([^"\']+)', re.IGNORECASE),       # Spring
    re.compile(r'\[Http(Get|Post|Put|Delete|Patch)\s*\(\s*"([^"]+)"\)\]', re.IGNORECASE),          # ASP.NET
]


@dataclass
class Symbol:
    name: str
    kind: str          # function | class | method | route
    line: int
    detail: str = ""   # e.g. HTTP method + route path for "route"


@dataclass
class FileIndex:
    path: str
    language: str
    module_type: str
    symbols: List[Symbol] = field(default_factory=list)
    path_tokens: Set[str] = field(default_factory=set)
    line_count: int = 0


class CodebaseIndexer:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def build(self) -> List[FileIndex]:
        results: List[FileIndex] = []
        for path in self._walk():
            language = LANGUAGE_MAP.get(path.suffix.lower())
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            rel = self._rel(path)
            module_type = self._detect_module_type(rel, content, language)
            symbols = self._extract_symbols(content, language) if language else []

            results.append(FileIndex(
                path=rel,
                language=language or "other",
                module_type=module_type,
                symbols=symbols,
                path_tokens=self._tokenize_path(rel),
                line_count=content.count("\n") + 1,
            ))
        return results

    # ------------------------------------------------------------------
    # Walking
    # ------------------------------------------------------------------

    def _walk(self):
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
            for name in files:
                full = Path(root) / name
                if full.suffix.lower() in LANGUAGE_MAP or full.suffix.lower() in (
                    ".yaml", ".yml", ".json", ".env", ".sql", ".md"
                ) or name in ("Dockerfile",):
                    yield full

    # ------------------------------------------------------------------
    # Module-type classification
    # ------------------------------------------------------------------

    def _detect_module_type(self, rel_path: str, content: str, language: Optional[str]) -> str:
        if any(p.search(rel_path) for p in _TEST_PATH_PATTERNS):
            return "test"
        if any(p.search(rel_path) for p in _DB_PATH_PATTERNS):
            return "database"
        if any(p.search(rel_path) for p in _CONFIG_PATH_PATTERNS):
            return "config"
        if any(p.search(rel_path) for p in _UI_PATH_PATTERNS):
            return "ui_component"

        if language and any(p.search(content) for p in _API_CONTENT_PATTERNS):
            return "api_endpoint"

        lower = rel_path.lower()
        if any(x in lower for x in ("controller", "handler", "endpoint", "route", "resource")):
            return "api_endpoint"
        if any(x in lower for x in ("service", "usecase", "interactor", "business")):
            return "service"
        if any(x in lower for x in ("util", "helper", "common", "shared", "lib", "core")):
            return "library"
        if language:
            return "module"
        return "other"

    # ------------------------------------------------------------------
    # Symbol extraction
    # ------------------------------------------------------------------

    def _extract_symbols(self, content: str, language: str) -> List[Symbol]:
        if language == "python":
            return self._extract_python_symbols(content)
        if language in ("javascript", "typescript"):
            return self._extract_js_symbols(content)
        if language == "java":
            return self._extract_java_symbols(content)
        if language == "csharp":
            return self._extract_csharp_symbols(content)
        if language == "go":
            return self._extract_go_symbols(content)
        return []

    @staticmethod
    def _extract_python_symbols(content: str) -> List[Symbol]:
        symbols: List[Symbol] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return symbols

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                route_detail = ""
                for dec in node.decorator_list:
                    src = ast.get_source_segment(content, dec) or ""
                    for pattern in _ROUTE_PATTERNS:
                        m = pattern.search(src)
                        if m:
                            route_detail = f"{m.group(1).upper()} {m.group(2)}"
                if route_detail:
                    symbols.append(Symbol(node.name, "route", node.lineno, route_detail))
                else:
                    symbols.append(Symbol(node.name, "function", node.lineno))
            elif isinstance(node, ast.ClassDef):
                symbols.append(Symbol(node.name, "class", node.lineno))
        return symbols

    @staticmethod
    def _extract_js_symbols(content: str) -> List[Symbol]:
        symbols: List[Symbol] = []
        for i, line in enumerate(content.splitlines(), start=1):
            for pattern in _ROUTE_PATTERNS:
                m = pattern.search(line)
                if m:
                    symbols.append(Symbol(f"{m.group(1).upper()} {m.group(2)}", "route", i, f"{m.group(1).upper()} {m.group(2)}"))

            m = re.search(r'\bfunction\s+([A-Za-z_$][\w$]*)\s*\(', line)
            if m:
                symbols.append(Symbol(m.group(1), "function", i))
                continue

            m = re.search(r'\bclass\s+([A-Za-z_$][\w$]*)', line)
            if m:
                symbols.append(Symbol(m.group(1), "class", i))
                continue

            m = re.search(r'\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(', line)
            if m:
                symbols.append(Symbol(m.group(1), "function", i))
        return symbols

    @staticmethod
    def _extract_java_symbols(content: str) -> List[Symbol]:
        symbols: List[Symbol] = []
        for i, line in enumerate(content.splitlines(), start=1):
            m = re.search(r'\b(?:public|private|protected)?\s*(?:static\s+)?class\s+([A-Za-z_]\w*)', line)
            if m:
                symbols.append(Symbol(m.group(1), "class", i))
                continue

            for pattern in _ROUTE_PATTERNS:
                m = pattern.search(line)
                if m:
                    symbols.append(Symbol(f"{m.group(1).upper()} {m.group(2)}", "route", i, f"{m.group(1).upper()} {m.group(2)}"))

            m = re.search(
                r'\b(?:public|private|protected)\s+(?:static\s+)?[\w<>\[\]]+\s+([A-Za-z_]\w*)\s*\(',
                line,
            )
            if m and m.group(1) not in ("if", "for", "while", "switch", "return"):
                symbols.append(Symbol(m.group(1), "method", i))
        return symbols

    @staticmethod
    def _extract_csharp_symbols(content: str) -> List[Symbol]:
        symbols: List[Symbol] = []
        for i, line in enumerate(content.splitlines(), start=1):
            m = re.search(r'\b(?:public|private|protected|internal)?\s*(?:static\s+)?(?:partial\s+)?class\s+([A-Za-z_]\w*)', line)
            if m:
                symbols.append(Symbol(m.group(1), "class", i))
                continue

            for pattern in _ROUTE_PATTERNS:
                m = pattern.search(line)
                if m:
                    symbols.append(Symbol(f"{m.group(1).upper()} {m.group(2)}", "route", i, f"{m.group(1).upper()} {m.group(2)}"))

            m = re.search(
                r'\b(?:public|private|protected|internal)\s+(?:static\s+|async\s+|override\s+)*[\w<>\[\],?]+\s+([A-Za-z_]\w*)\s*\(',
                line,
            )
            if m:
                symbols.append(Symbol(m.group(1), "method", i))
        return symbols

    @staticmethod
    def _extract_go_symbols(content: str) -> List[Symbol]:
        symbols: List[Symbol] = []
        for i, line in enumerate(content.splitlines(), start=1):
            m = re.search(r'\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(', line)
            if m:
                kind = "route" if "http.ResponseWriter" in line else "function"
                symbols.append(Symbol(m.group(1), kind, i))
                continue
            m = re.search(r'\btype\s+([A-Za-z_]\w*)\s+struct\b', line)
            if m:
                symbols.append(Symbol(m.group(1), "class", i))
        return symbols

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize_path(rel_path: str) -> Set[str]:
        """Split a path into lowercase searchable tokens, including
        camelCase / PascalCase / snake_case / kebab-case sub-words."""
        tokens: Set[str] = set()
        stem = re.sub(r'\.\w+$', '', rel_path)  # drop extension
        for part in re.split(r'[\\/]', stem):
            tokens.add(part.lower())
            for sub in re.split(r'[_\-.]', part):
                if sub:
                    tokens.add(sub.lower())
            for sub in re.findall(r'[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z]|$)', part):
                if sub:
                    tokens.add(sub.lower())
        return tokens

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repo_path)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

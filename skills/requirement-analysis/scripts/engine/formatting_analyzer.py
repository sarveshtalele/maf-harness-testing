"""
Formatting Analyzer
Detects which formatter/linter configs exist in the repository and, for
each candidate file, samples its existing indentation and quote style so
new code can match the surrounding file even when no formatter config is
present. Detection is a fixed lookup table over well-known config
filenames — deterministic and offline.
"""
import re
from pathlib import Path
from typing import Dict, List

# (glob pattern, tool name, fix command)
_CONFIG_PATTERNS = [
    ("pyproject.toml", "Black / isort / Ruff (Python)", "black . && isort . && ruff check --fix ."),
    ("setup.cfg", "flake8 / Python packaging", "flake8"),
    (".flake8", "flake8 (Python)", "flake8"),
    (".pylintrc", "Pylint (Python)", "pylint <changed files>"),
    (".prettierrc*", "Prettier (JS/TS)", "npx prettier --write ."),
    (".eslintrc*", "ESLint (JS/TS)", "npx eslint --fix ."),
    ("checkstyle.xml", "Checkstyle (Java)", "mvn checkstyle:check (or gradle checkstyleMain)"),
    (".editorconfig", "EditorConfig (repo-wide indent/charset rules)", "(enforced by IDE; no single CLI command)"),
    (".golangci.yml", "golangci-lint (Go)", "golangci-lint run && gofmt -w ."),
    ("*.csproj", ".NET / dotnet-format (C#)", "dotnet format"),
    ("rustfmt.toml", "rustfmt (Rust)", "cargo fmt"),
    (".rubocop.yml", "RuboCop (Ruby)", "rubocop -a"),
]


class FormattingAnalyzer:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def analyze(self, candidate_paths: List[str]) -> Dict:
        detected_tools = self._detect_tools()
        candidate_styles = {p: self._sample_style(p) for p in candidate_paths}
        return {
            "detected_tools": detected_tools,
            "candidate_file_styles": candidate_styles,
        }

    # ------------------------------------------------------------------

    def _detect_tools(self) -> List[Dict[str, str]]:
        found: List[Dict[str, str]] = []
        for pattern, tool, command in _CONFIG_PATTERNS:
            matches = sorted(self.repo_path.glob(pattern))
            if matches:
                found.append({
                    "config_file": self._rel(matches[0]),
                    "tool": tool,
                    "command": command,
                })
        return found

    def _sample_style(self, rel_path: str) -> Dict[str, str]:
        full = self.repo_path / rel_path
        try:
            content = full.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return {"indent": "unknown", "quote_style": "unknown", "line_ending": "unknown"}

        lines = content.splitlines()
        indents = [len(re.match(r'^(\s*)', line).group(1)) for line in lines if line.strip()]
        tabs = sum(1 for line in lines if line.startswith("\t"))
        spaces = sum(1 for line in lines if line.startswith("  ") and not line.startswith("\t"))
        if tabs > spaces:
            indent = "tabs"
        else:
            two_indents = sum(1 for i in indents if i and i % 2 == 0 and i % 4 != 0)
            indent = "spaces:2" if two_indents > len(indents) * 0.3 else "spaces:4"

        single_q = content.count("'")
        double_q = content.count('"')
        if single_q == 0 and double_q == 0:
            quote_style = "n/a"
        else:
            quote_style = "single" if single_q > double_q else "double"

        line_ending = "CRLF" if "\r\n" in content else "LF"

        return {"indent": indent, "quote_style": quote_style, "line_ending": line_ending}

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repo_path)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

"""Product requirements (tier ``product``) stated by the repository itself, found without a model.

Sources: README, SKILL.md, INSTALL/DOCUMENTATION files, docs/ and references/ markdown, and CLI options declared in
Python source (argparse ``add_argument``, click ``option``, ``ArgumentParser(description=...)``).

Rules:
- a table row ``| property | description |`` -> "The documentation states <property>: <description>". File-index
  tables (first column a path or link), licence, changelog, support and compatibility tables are skipped; at most
  MAX_ROWS_PER_TABLE rows per table;
- a bullet under a feature, capability, highlight or support heading -> "The documentation lists this feature: ...";
- a sentence with should/must/will (outside code, not in roadmap/planned/future sections) -> the sentence;
- a CLI option (long flag preferred) -> "CLI option --x defaults to D." or "CLI accepts option --x.", plus help;
  positional arguments -> "CLI positional argument NAME ...";
- ArgumentParser(description=...) -> the tool description.

Each kind has its own cap (CAP_PER_KIND); at most MAX_TOTAL items. Lines longer than MAX_LINE are skipped, so the work
is linear in the input. Every item carries its verbatim quote (file, 1-based line, the physical line).
``verify_quotes`` demotes a product requirement to status ``gap`` when a quote is not verbatim in its file.
Read-only and bounded: no repository code runs.
"""

import re
from pathlib import Path

from ..qa.oracles import file_reader, normalize_path, quote_in_text, tier_of

ACTOR = "System"
MAX_LINE = 2000
MAX_DOC_FILES = 40
MAX_CLI_FILES = 300
MAX_ROWS_PER_TABLE = 8
CAP_PER_KIND = {"table": 20, "bullet": 20, "sentence": 20, "cli": 20}
MAX_TOTAL = 80
MAX_QUOTE = 500
DOC_NAME = re.compile(r"(?i)(^|/)(README|INSTALL|DOCUMENTATION|SKILL)(\.[^/]*)?$")
DOC_DIR = re.compile(r"(?i)(^|/)(docs?|documentation|references?)/[^/]+\.(md|rst|txt)$")
EXCLUDED_PARTS = {"node_modules", "vendor", "third_party", ".venv", "venv", ".git"}
EXCLUDED_NAME = re.compile(r"(?i)(^|/)(CHANGELOG|LICENSE|LICENCE|COPYING)|_REVIEW_|(^|/)docs/audit/")
FEATURE_HEADING = re.compile(
    r"(?i)feature|capabilit|highlight|support|key (points|benefits)|what .* (does|do)"
)
SKIP_HEADING = re.compile(r"(?i)roadmap|planned|future|upcoming|backlog|wishlist|not yet|to ?do\b")
DEVELOPER_HEADING = re.compile(
    r"(?i)contribut|developer|maintain|merge|pull request|how to change|development"
)
TABLE_SKIP_HEADING = re.compile(r"(?i)licen|changelog|release|version|support|compatib|platform|matrix")
TABLE_SKIP_COLUMNS = re.compile(r"(?i)licen|changelog|support|compatib|platform|matrix|release|version")
FILE_CELL = re.compile(r"^[\w./\\-]*[\w-]\.[A-Za-z0-9]{1,8}$")
LINK_CELL = re.compile(r"\[[^\]]*\]\([^)]*\)")
TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}")
BULLET = re.compile(r"^\s*[-*+]\s+(.+)$")
HEADING = re.compile(r"^\s*#{1,6}\s+(.+)$")
MODAL = re.compile(r"\b(?:should|must|will)\b")
ABBREVIATIONS = {"e.g", "i.e", "etc", "vs", "cf", "approx", "no", "fig", "resp"}
CLI_CALL = re.compile(r"\b(?:add_argument|click\.option|option)\(")
CLI_FLAG = re.compile(r"""["'](--?[A-Za-z][\w-]*)["']""")
CLI_POSITIONAL = re.compile(r"""add_argument\(\s*["']([A-Za-z][\w-]*)["']""")
CLI_DEFAULT = re.compile(r"\bdefault\s*=\s*([^,)\n]+)")
CLI_HELP = re.compile(r"""\bhelp\s*=\s*(['"])(.*?)\1""")
PARSER_DESCRIPTION = re.compile(r"""ArgumentParser\(\s*(?:description\s*=\s*)?(['"])([^'"\n]{1,300})\1""")
_MARKUP = re.compile(r"[*_`]+")


def _clean(text: str) -> str:
    return " ".join(_MARKUP.sub("", text).split())


def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    return any(part in EXCLUDED_PARTS for part in parts) or bool(EXCLUDED_NAME.search(rel))


def _requirement(text: str, rel: str, line: int, quote: str, category: str = "functional") -> dict:
    quote = quote.strip()[:MAX_QUOTE]
    return {
        "text": text[:700],
        "evidence": f"{rel}:{line}: {quote}"[:900],
        "source_files": [rel],
        "category": category,
        "actor": ACTOR,
        "tier": "product",
        "evidence_quotes": [{"file": rel, "line": line, "quote": quote}],
        "source_line": line,
    }


def sentences(line: str) -> list[str]:
    """Sentences of one line in one linear pass. A dot splits only when followed by whitespace, and not after a known
    abbreviation, so ``README.md`` and ``e.g. x`` stay whole."""
    out: list[str] = []
    start = 0
    for index, char in enumerate(line):
        if char in ".!?" and (index + 1 == len(line) or line[index + 1].isspace()):
            last = line[start : index + 1].rsplit(None, 1)
            if char == "." and last and last[-1].rstrip(".").lower() in ABBREVIATIONS:
                continue
            out.append(line[start : index + 1])
            start = index + 1
    out.append(line[start:])
    return [s.strip() for s in out if s.strip()]


def _is_file_cell(cell: str) -> bool:
    """A cell naming a file: a markdown link, or a bare or backticked path with an extension."""
    if LINK_CELL.search(cell):
        return True
    bare = _MARKUP.sub("", cell).strip()
    return bool(FILE_CELL.match(bare)) or ("/" in bare and " " not in bare)


def _doc_items(rel: str, lines: list[str], budget: dict[str, list[dict]]) -> None:
    in_fence = False
    fence_marker = ""
    in_feature = in_skip = skip_heading_table = in_developer = False
    index = 0
    while index < len(lines):
        raw = lines[index]
        number = index + 1
        index += 1
        stripped = raw.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            continue
        if in_fence or len(raw) > MAX_LINE or raw[:1] == "\t" or raw.startswith("    "):
            continue  # code, over-long lines and indented code are never product statements
        heading = HEADING.match(raw)
        if heading:
            heading_text = heading.group(1)
            in_skip = bool(SKIP_HEADING.search(heading_text))
            in_feature = bool(FEATURE_HEADING.search(heading_text)) and not in_skip
            skip_heading_table = bool(TABLE_SKIP_HEADING.search(heading_text))
            in_developer = bool(DEVELOPER_HEADING.search(heading_text))
            continue
        if stripped.startswith("|"):
            if index < len(lines) and TABLE_SEPARATOR.match(lines[index].strip()):
                header = [_MARKUP.sub("", c).strip() for c in stripped.strip("|").split("|")]
                skip_table = skip_heading_table or any(TABLE_SKIP_COLUMNS.search(h) for h in header)
                index += 1  # header and separator
                rows = 0
                while index < len(lines) and lines[index].strip().startswith("|"):
                    row_raw = lines[index]
                    row_number = index + 1
                    index += 1
                    if len(row_raw) > MAX_LINE or skip_table or in_skip:
                        continue
                    cells = [c.strip() for c in row_raw.strip().strip("|").split("|")]
                    if len(cells) < 2 or not cells[0] or not cells[1] or _is_file_cell(cells[0]):
                        continue
                    if rows >= MAX_ROWS_PER_TABLE or len(budget["table"]) >= CAP_PER_KIND["table"]:
                        break
                    prop, value = _clean(cells[0]), _clean("; ".join(c for c in cells[1:] if c))
                    if prop and value:
                        rows += 1
                        budget["table"].append(
                            _requirement(
                                f"The documentation states {prop}: {value}", rel, row_number, row_raw
                            )
                        )
            continue
        if in_skip:
            continue
        bullet = BULLET.match(raw)
        if bullet and in_feature:
            text = _clean(bullet.group(1))
            if 8 <= len(text) <= 300 and len(budget["bullet"]) < CAP_PER_KIND["bullet"]:
                budget["bullet"].append(
                    _requirement(f"The documentation lists this feature: {text}", rel, number, raw)
                )
            continue
        if MODAL.search(raw) and not in_developer:
            for sentence in sentences(raw):
                if (
                    25 <= len(sentence) <= 300
                    and len(sentence.split()) >= 4
                    and MODAL.search(sentence)
                    and len(budget["sentence"]) < CAP_PER_KIND["sentence"]
                ):
                    budget["sentence"].append(_requirement(sentence, rel, number, sentence))


def _call_text(lines: list[str], index: int, start: int, max_lines: int = 10) -> str:
    """The source of one call from its opening name to the balanced closing parenthesis (bounded)."""
    text, depth, opened = "", 0, False
    for offset, line in enumerate(lines[index : index + max_lines]):
        chunk = (line[start:] if offset == 0 else line)[:MAX_LINE]
        for char in chunk:
            text += char
            opened = opened or char == "("
            depth += (char == "(") - (char == ")")
            if opened and depth == 0:
                return text
        text += " "
    return text


def _cli_items(rel: str, lines: list[str], budget: list[dict]) -> None:
    for index, raw in enumerate(lines):
        if len(raw) > MAX_LINE or len(budget) >= CAP_PER_KIND["cli"]:
            return
        description = PARSER_DESCRIPTION.search(raw)
        if description and len(budget) < CAP_PER_KIND["cli"]:
            budget.append(
                _requirement(
                    f"The command-line tool is described as: {_clean(description.group(2))}",
                    rel,
                    index + 1,
                    raw,
                    category="cli",
                )
            )
        match = CLI_CALL.search(raw)
        if not match or len(budget) >= CAP_PER_KIND["cli"]:
            continue
        call = _call_text(lines, index, match.start())
        positional = CLI_POSITIONAL.search(call)
        flags = CLI_FLAG.findall(call)
        longs = [f for f in flags if f.startswith("--")]
        help_text = CLI_HELP.search(call)
        if positional and not flags:
            name = positional.group(1)
            text = f"CLI positional argument {name} is required."
        elif flags:
            flag = (longs or flags)[0]
            default = CLI_DEFAULT.search(call)
            text = (
                f"CLI option {flag} defaults to {default.group(1).strip()}."
                if default
                else f"CLI accepts option {flag}."
            )
        else:
            continue
        if help_text:
            text += f" {_clean(help_text.group(2))}"
        budget.append(_requirement(text, rel, index + 1, raw, category="cli"))


def _doc_order(path: str) -> tuple:
    """README and SKILL.md at the root and first-level directories come first, then shallower paths."""
    depth = path.count("/")
    name = path.rsplit("/", 1)[-1].upper()
    primary = name.startswith(("README", "SKILL")) and depth <= 2
    return (0 if primary else 1, depth, path)


def extract_product_requirements(root: Path, tracked_files: list[str]) -> list[dict]:
    """Deterministic product requirements with verbatim quotes, in source-kind order; duplicates dropped."""
    read = file_reader(root)
    tracked = [normalize_path(p) for p in tracked_files]
    docs = sorted(
        (p for p in tracked if (DOC_NAME.search(p) or DOC_DIR.search(p)) and not _excluded(p)),
        key=_doc_order,
    )[:MAX_DOC_FILES]
    cli = [
        p
        for p in tracked
        if p.endswith(".py") and not _excluded(p) and not p.startswith("tests/") and "/tests/" not in p
    ][:MAX_CLI_FILES]
    budget: dict[str, list[dict]] = {"table": [], "bullet": [], "sentence": [], "cli": []}
    for rel in docs:
        text = read(rel)
        if text:
            _doc_items(rel, text.splitlines(), budget)
    cli_items = budget["cli"]
    for rel in cli:
        text = read(rel)
        if text and ("add_argument(" in text or "option(" in text or "ArgumentParser(" in text):
            _cli_items(rel, text.splitlines(), cli_items)
    unique: list[dict] = []
    seen: set[str] = set()
    for kind in ("table", "bullet", "sentence", "cli"):
        for item in budget[kind]:
            if item["text"] not in seen:
                seen.add(item["text"])
                unique.append(item)
    return unique[:MAX_TOTAL]


def verify_quotes(requirements: list[dict], root: Path) -> list[str]:
    """Demote product requirements whose quote is not verbatim in its file (status ``gap``); returns the notes.

    A product requirement with no quote is demoted too. Component requirements are not changed. The host decides;
    a status supplied by the model is overwritten.
    """
    read = file_reader(root)
    notes: list[str] = []
    for requirement in requirements:
        if tier_of(requirement) != "product":
            requirement.setdefault("status", "verified")
            continue
        quotes = requirement.get("evidence_quotes") or []
        bad = [q for q in quotes if not quote_in_text(q.get("quote"), read(str(q.get("file") or "")))]
        if quotes and not bad:
            requirement["status"] = "verified"
            continue
        requirement["status"] = "gap"
        reason = (
            "has no evidence quote"
            if not quotes
            else "quote not verbatim in " + ", ".join(sorted({str(q.get("file")) for q in bad}))
        )
        notes.append(
            f"Product requirement {requirement.get('id', requirement.get('text'))} demoted to gap: {reason}."
        )
    return notes


def product_doc_evidence(root: Path, tracked_files: list[str], max_chars: int = 40_000) -> str:
    """Line-numbered excerpts of the product documents (same shape as repository evidence) for the model path."""
    read = file_reader(root)
    chunks: list[str] = []
    used = 0
    docs = sorted(
        (
            p
            for p in (normalize_path(t) for t in tracked_files)
            if (DOC_NAME.search(p) or DOC_DIR.search(p)) and not _excluded(p)
        ),
        key=_doc_order,
    )
    for rel in docs:
        text = read(rel)
        if not text:
            continue
        room = max_chars - used
        if room < 200:
            break
        body = "\n".join(
            f"{n}: {line}" for n, line in enumerate(text.splitlines(), 1) if len(line) <= MAX_LINE
        )
        chunk = f"\n--- FILE: {rel} ---\n{body[:room]}\n"
        chunks.append(chunk)
        used += len(chunk)
    return "".join(chunks)

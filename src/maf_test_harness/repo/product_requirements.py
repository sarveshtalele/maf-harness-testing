"""Product requirements (tier ``product``) stated by the repository itself, found without a model.

Sources: README, SKILL.md, INSTALL/DOCUMENTATION files, docs/ and references/ markdown, and CLI options declared in
Python source (argparse ``add_argument``, click ``option``, ``ArgumentParser(description=...)``). Rules:

- a markdown table row ``| property | description |`` under any heading -> "The documentation states <property>: <description>";
- a bullet under a heading about features, capabilities, highlights or support -> "The documentation lists this feature: ...";
- a sentence containing should/must/will in a doc (outside code fences) -> the sentence itself;
- a CLI option -> "CLI option --x defaults to D." (or "CLI accepts option --x.") plus its help text.

Every extracted requirement carries its own verbatim quote (file, 1-based line, the physical line). ``verify_quotes``
demotes a product requirement to status ``gap`` when any quote is not a whitespace-normalised substring of its file.
Read-only and bounded: no repository code runs.
"""

import re
from pathlib import Path

from ..qa.oracles import file_reader, quote_in_text, tier_of

ACTOR = "System"
MAX_DOC_FILES = 40
MAX_CLI_FILES = 300
MAX_REQUIREMENTS = 80
MAX_QUOTE = 500
DOC_NAME = re.compile(r"(?i)(^|/)(README|INSTALL|DOCUMENTATION|SKILL)(\.[^/]*)?$")
DOC_DIR = re.compile(r"(?i)(^|/)(docs?|documentation|references?)/[^/]+\.(md|rst|txt)$")
FEATURE_HEADING = re.compile(
    r"(?i)feature|capabilit|highlight|support|key (points|benefits)|what .* (does|do)"
)
TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}")
BULLET = re.compile(r"^\s*[-*+]\s+(.+)$")
HEADING = re.compile(r"^\s*#{1,6}\s+(.+)$")
MODAL_SENTENCE = re.compile(r"[^.!?]*\b(?:should|must|will)\b[^.!?]*[.!?]?")
CLI_CALL = re.compile(r"\b(?:add_argument|click\.option|option)\(")
CLI_FLAG = re.compile(r"""["'](--?[A-Za-z][\w-]*)["']""")
CLI_DEFAULT = re.compile(r"\bdefault\s*=\s*([^,)\n]+)")
CLI_HELP = re.compile(r"""\bhelp\s*=\s*(['"])(.*?)\1""")
PARSER_DESCRIPTION = re.compile(r"""ArgumentParser\(\s*(?:description\s*=\s*)?(['"])(.+?)\1""")
_MARKUP = re.compile(r"[*_`]+")


def _clean(text: str) -> str:
    return " ".join(_MARKUP.sub("", text).split())


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


def _doc_items(rel: str, lines: list[str]) -> list[dict]:
    items: list[dict] = []
    in_fence = in_feature = False
    for index, raw in enumerate(lines):
        number = index + 1
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = HEADING.match(raw)
        if heading:
            in_feature = bool(FEATURE_HEADING.search(heading.group(1)))
            continue
        if raw.lstrip().startswith("|"):
            header = index + 1 < len(lines) and TABLE_SEPARATOR.match(lines[index + 1].strip())
            if header or TABLE_SEPARATOR.match(raw.strip()):
                continue
            cells = [c.strip() for c in raw.strip().strip("|").split("|")]
            prop, value = _clean(cells[0]), _clean("; ".join(c for c in cells[1:] if c))
            if prop and value:
                items.append(_requirement(f"The documentation states {prop}: {value}", rel, number, raw))
            continue
        bullet = BULLET.match(raw)
        if bullet and in_feature:
            text = _clean(bullet.group(1))
            if 8 <= len(text) <= 300:
                items.append(_requirement(f"The documentation lists this feature: {text}", rel, number, raw))
            continue
        for match in MODAL_SENTENCE.finditer(raw):
            sentence = match.group(0).strip()
            if 25 <= len(sentence) <= 300 and len(sentence.split()) >= 4:
                items.append(_requirement(sentence, rel, number, sentence))
    return items


def _call_text(lines: list[str], index: int, start: int, max_lines: int = 10) -> str:
    """The source of one call from its opening name to the balanced closing parenthesis (bounded)."""
    text, depth, opened = "", 0, False
    for offset, line in enumerate(lines[index : index + max_lines]):
        chunk = line[start:] if offset == 0 else line
        for char in chunk:
            text += char
            opened = opened or char == "("
            depth += (char == "(") - (char == ")")
            if opened and depth == 0:
                return text
        text += " "
    return text


def _cli_items(rel: str, lines: list[str]) -> list[dict]:
    items: list[dict] = []
    for index, raw in enumerate(lines):
        description = PARSER_DESCRIPTION.search(raw)
        if description:
            items.append(
                _requirement(
                    f"The command-line tool is described as: {_clean(description.group(2))}",
                    rel,
                    index + 1,
                    raw,
                    category="cli",
                )
            )
        match = CLI_CALL.search(raw)
        if not match:
            continue
        call = _call_text(lines, index, match.start())
        flags = CLI_FLAG.findall(call)[:1]
        if not flags:
            continue
        default = CLI_DEFAULT.search(call)
        help_text = CLI_HELP.search(call)
        text = (
            f"CLI option {flags[0]} defaults to {default.group(1).strip()}."
            if default
            else f"CLI accepts option {flags[0]}."
        )
        if help_text:
            text += f" {_clean(help_text.group(2))}"
        items.append(_requirement(text, rel, index + 1, raw, category="cli"))
    return items


def extract_product_requirements(root: Path, tracked_files: list[str]) -> list[dict]:
    """Deterministic product requirements with verbatim quotes, in file order; duplicates and overflow dropped."""
    read = file_reader(root)
    tracked = sorted(str(p) for p in tracked_files)
    docs = [p for p in tracked if DOC_NAME.search(p) or DOC_DIR.search(p)][:MAX_DOC_FILES]
    cli = [p for p in tracked if p.endswith(".py")][:MAX_CLI_FILES]
    items: list[dict] = []
    for rel in docs:
        text = read(rel)
        if text:
            items += _doc_items(rel, text.splitlines())
    for rel in cli:
        text = read(rel)
        if text and ("add_argument(" in text or "option(" in text or "ArgumentParser(" in text):
            items += _cli_items(rel, text.splitlines())
    unique: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if item["text"] not in seen:
            seen.add(item["text"])
            unique.append(item)
    return unique[:MAX_REQUIREMENTS]


def verify_quotes(requirements: list[dict], root: Path) -> list[str]:
    """Demote product requirements whose quote is not verbatim in its file (status ``gap``); returns the notes.

    A product requirement with no quote at all is demoted too. Component requirements are not changed.
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
    for rel in sorted(str(p) for p in tracked_files):
        if not (DOC_NAME.search(rel) or DOC_DIR.search(rel)):
            continue
        text = read(rel)
        if not text:
            continue
        room = max_chars - used
        if room < 200:
            break
        body = "\n".join(f"{n}: {line}" for n, line in enumerate(text.splitlines(), 1))
        chunk = f"\n--- FILE: {rel} ---\n{body[:room]}\n"
        chunks.append(chunk)
        used += len(chunk)
    return "".join(chunks)

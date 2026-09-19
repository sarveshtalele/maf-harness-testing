"""Deterministic adapters around imported marketplace skill contracts."""

from __future__ import annotations

import ast
import difflib
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import jsonschema
import yaml

from .skills_config import SKILL_NAMES

_NAMESPACE = uuid.NAMESPACE_URL
_SECTIONS = ("positive", "negative", "boundary", "validation", "contract")
_LINK_COLLECTION = {
    "requirement": "requirements",
    "acceptance_criterion": "acceptance_criteria",
    "validation": "validation_rules",
    "api_contract": "api_contracts",
}
# Placeholder *work*, not the word "placeholder" (a legitimate HTML attribute/selector in UI tests).
_PLACEHOLDER = re.compile(
    r"(?:\bTODO\b|\bFIXME\b|\bplaceholder\s+(?:test|implementation|assertion|body)\b|"
    r"raise\s+NotImplementedError|throw new Error\([\"']TODO)",
    re.IGNORECASE,
)
# A test that never runs proves nothing; skipped-only files are rejected as placeholders.
_SKIP_MARKERS = re.compile(
    r"(?:pytest\.mark\.skip|pytest\.skip\(|@unittest\.skip|\b(?:test|it|describe)\.(?:skip|fixme)\b|\bxit\(|"
    r"\bxdescribe\(|@Ignore\b|@Disabled\b|\bt\.Skip\(|\[Fact\(Skip|\[Ignore\])"
)
_FORMAT_CHECKER = jsonschema.FormatChecker()
_VOLATILE_CASE_FIELDS = {"id", "metadata", "generated_at"}


def _slug(text: str, max_length: int = 52) -> str:
    """Return a compact human-readable identifier fragment."""
    value = re.sub(r"[^A-Za-z0-9]+", "-", str(text).strip()).strip("-").upper()
    value = re.sub(r"-+", "-", value)
    return value[:max_length].rstrip("-") or "ITEM"


def _stable_suffix(seed: str, length: int = 8) -> str:
    return uuid.uuid5(_NAMESPACE, seed).hex[:length].upper()


def _descriptive_id(prefix: str, label: str, seed: str, *, max_label: int = 52) -> str:
    """Create stable IDs that explain what the item represents."""
    return f"{prefix}-{_slug(label, max_label)}-{_stable_suffix(seed)}"


def design_schema(skills_root: Path) -> dict[str, Any]:
    """Load authoritative test-design JSON Schema from marketplace package."""
    path = Path(skills_root) / SKILL_NAMES["test_design"] / "assets" / "testcase_templates.yaml"
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        schema = payload["output_contract"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ValueError(f"unable to load test-design output_contract from {path}") from exc
    if not isinstance(schema, dict):
        raise TypeError("test-design output_contract must be an object")
    try:
        jsonschema.validators.validator_for(schema).check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ValueError(f"invalid test-design output_contract: {exc.message}") from exc
    return schema


_HEX_SUFFIX = re.compile(r"-[0-9A-F]{8}$")


def resolve_id(candidate: Any, known: Any) -> str | None:
    """Map a model-written ID to the one canonical ID it means, or None when it is unknown or ambiguous.

    The host assigns IDs like ``TC-POS-ADD-SUMS-DBF3F6CE``. Models copy the descriptive part faithfully but routinely
    garble the 8-hex suffix (``...50B1F2`` written as ``...50F1B2``). Exact match wins; otherwise a unique known ID
    with the same descriptive label (or a unique very close spelling) is accepted and the link is canonicalized.
    """
    ids = list(known)
    text = str(candidate).strip()
    if text in ids:
        return text
    same_case = [k for k in ids if k.upper() == text.upper()]
    if len(same_case) == 1:
        return same_case[0]
    label = _HEX_SUFFIX.sub("", text.upper())
    same_label = [k for k in ids if _HEX_SUFFIX.sub("", k.upper()) == label]
    if len(same_label) == 1:
        return same_label[0]
    close = difflib.get_close_matches(text, ids, n=2, cutoff=0.92)
    return close[0] if len(close) == 1 else None


def _clean_paths(paths: Any, what: str) -> list[str]:
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p.strip() for p in paths):
        raise ValueError(f"each {what} needs at least one source file")
    return list(dict.fromkeys(p.strip() for p in paths))[:8]


def finalize_discovered_requirements(document: dict[str, Any], repo_url: str) -> dict[str, Any]:
    """Turn an autonomous requirements draft into the stable, ID-addressed model used downstream.

    Model output refers to requirements by 1-based index; the host assigns stable descriptive IDs and rewires
    acceptance criteria to them. Actors, validation rules and explicit API contracts are preserved so the
    design stage can trace to every source type the test-design skill understands.
    """
    if not isinstance(document, dict):
        raise TypeError("requirements draft must be an object")
    app_name = str(document.get("app_name", "")).strip()
    if not app_name:
        raise ValueError("requirements draft needs app_name")
    items = document.get("requirements")
    if not isinstance(items, list) or not items:
        raise ValueError("requirements draft must contain at least one requirement")
    repo = repo_url.strip()
    actors = []
    for actor in document.get("actors") or []:
        name = str(actor.get("name", "")).strip()
        if name and name.lower() not in {a["name"].lower() for a in actors}:
            actors.append(
                {
                    "name": name,
                    "description": str(actor.get("description", "")).strip(),
                    "source_files": _clean_paths(actor.get("source_files"), "actor"),
                }
            )
    actor_names = {a["name"].lower() for a in actors} | {"system"}
    requirements: list[dict[str, Any]] = []
    ref_to_id: dict[int, str] = {}
    seen_text: set[str] = set()
    for position, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise TypeError("requirements entries must be objects")
        text, evidence = str(item.get("text", "")).strip(), str(item.get("evidence", "")).strip()
        if not text or not evidence:
            raise ValueError("each inferred requirement needs text and evidence")
        actor = str(item.get("actor", "system")).strip() or "system"
        if actor.lower() not in actor_names:
            raise ValueError(f"requirement cites unknown actor {actor!r}; declare it in actors")
        if text in seen_text:
            ref_to_id[position] = next(r["id"] for r in requirements if r["text"] == text)
            continue
        seen_text.add(text)
        stable_id = _descriptive_id("REQ", text, f"requirement:{repo}:{text}")
        ref_to_id[position] = stable_id
        requirements.append(
            {
                "id": stable_id,
                "text": text,
                "evidence": evidence,
                "source_files": _clean_paths(item.get("source_files"), "inferred requirement"),
                "category": item.get("category", "functional"),
                "actor": actor,
            }
        )
    criteria = []
    for item in document.get("acceptance_criteria") or []:
        parent = ref_to_id.get(item.get("requirement_ref"))
        text = str(item.get("text", "")).strip()
        if parent is None:
            raise ValueError(
                f"acceptance criterion references unknown requirement_ref {item.get('requirement_ref')!r}"
            )
        if text:
            criteria.append(
                {
                    "id": _descriptive_id("AC", text, f"criterion:{repo}:{parent}:{text}"),
                    "text": text,
                    "requirement_id": parent,
                    "evidence": text,
                    "source_files": _clean_paths(item.get("source_files"), "acceptance criterion"),
                }
            )
    rules = [
        {
            "id": _descriptive_id("VAL", str(r["text"]).strip(), f"validation:{repo}:{r['text']}"),
            "text": str(r["text"]).strip(),
            "source_files": _clean_paths(r.get("source_files"), "validation rule"),
        }
        for r in document.get("validation_rules") or []
        if str(r.get("text", "")).strip()
    ]
    contracts = []
    for c in document.get("api_contracts") or []:
        method, path = str(c.get("method", "")).upper().strip(), str(c.get("path", "")).strip()
        codes = [str(code) for code in c.get("status_codes") or []]
        if not (method and path and codes):
            continue
        contracts.append(
            {
                "id": _descriptive_id("API", f"{method} {path}", f"api:{repo}:{method}:{path}"),
                "method": method,
                "path": path,
                "status_codes": codes,
                "source_files": _clean_paths(c.get("source_files"), "api contract"),
            }
        )
    return {
        "app_name": app_name,
        "summary": str(document.get("summary", "")).strip(),
        "actors": actors,
        "requirement_model": {
            "requirements": requirements,
            "validation_rules": rules,
            "acceptance_criteria": criteria,
        },
        "api_contracts": contracts,
        "environment_needs": [dict(n) for n in document.get("environment_needs") or []],
        "exclusions": list(document.get("exclusions") or []),
        "assumptions": list(document.get("assumptions") or []),
        "gaps": list(document.get("gaps") or []),
        "metadata": {
            "generator": "repository-requirements",
            "repository_url": repo,
            "generated_at": datetime.now(UTC).isoformat(),
        },
    }


def _source_index(source: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    if not isinstance(source, dict) or not isinstance(source.get("requirement_model"), dict):
        raise TypeError("source.requirement_model must be an object")
    model = source["requirement_model"]
    collections: dict[str, list[Any]] = {
        "requirements": model.get("requirements", []),
        "validation_rules": model.get("validation_rules", []),
        "acceptance_criteria": model.get("acceptance_criteria", []),
        "api_contracts": source.get("api_contracts", []),
    }
    required_fields = {
        "requirements": ("id", "text", "evidence"),
        "validation_rules": ("id", "text"),
        "acceptance_criteria": ("id", "text", "requirement_id"),
        "api_contracts": ("id", "path", "method", "status_codes"),
    }
    by_id: dict[str, dict[str, Any]] = {}
    ids: dict[str, set[str]] = {}
    for collection, values in collections.items():
        if not isinstance(values, list):
            raise TypeError(f"source {collection} must be an array")
        ids[collection] = set()
        for item in values:
            if not isinstance(item, dict):
                raise TypeError(f"source {collection} entries must be objects")
            for field in required_fields[collection]:
                value = item.get(field)
                if value is None or (isinstance(value, str) and not value.strip()):
                    raise ValueError(f"source {collection} entry missing {field}")
            item_id = item["id"]
            if not isinstance(item_id, str) or item_id in by_id:
                raise ValueError(f"source ID must be unique text: {item_id!r}")
            if collection == "api_contracts" and not isinstance(item["status_codes"], list):
                raise ValueError("api contract status_codes must be an array")
            ids[collection].add(item_id)
            by_id[item_id] = item
    requirement_ids = ids["requirements"]
    if not requirement_ids:
        raise ValueError("source must contain at least one requirement")
    for criterion in collections["acceptance_criteria"]:
        if criterion["requirement_id"] not in requirement_ids:
            raise ValueError(
                f"acceptance criterion references unknown requirement: {criterion['requirement_id']}"
            )
    return by_id, ids


def _case_id(section: str, case: dict[str, Any], repo_url: str) -> str:
    identity = {key: value for key, value in case.items() if key not in _VOLATILE_CASE_FIELDS}
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    section_code = {
        "positive": "POS",
        "negative": "NEG",
        "boundary": "BND",
        "validation": "VAL",
        "contract": "CON",
    }.get(section, _slug(section, 3))
    title = str(case.get("title", "test case"))
    return _descriptive_id(
        f"TC-{section_code}",
        title,
        f"test-design:{repo_url}:{section}:{encoded}",
        max_label=56,
    )


def _validate_design_schema(result: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        jsonschema.validate(result, schema, format_checker=_FORMAT_CHECKER)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "document"
        raise ValueError(f"schema validation failed at {location}: {exc.message}") from exc


def finalize_design(
    document: dict[str, Any],
    source: dict[str, Any],
    repo_url: str,
    skills_root: Path,
    allowed_levels: set[str] | None = None,
) -> dict[str, Any]:
    """Validate evidence links, assign IDs, and rebuild all derived design sections."""
    if not isinstance(document, dict):
        raise TypeError("document must be an object")
    if not isinstance(repo_url, str) or not repo_url.strip():
        raise ValueError("repo_url must be nonempty")
    by_id, source_ids = _source_index(source)
    schema = design_schema(skills_root)
    normalized_repo_url = repo_url.strip()
    result = deepcopy(document)
    draft_gaps = result.pop("gaps", [])
    if not isinstance(draft_gaps, list):
        raise TypeError("gaps must be an array")

    # Contract tests are meaningful only when the requirements artifact contains
    # explicit API contracts. Models occasionally place API-like behavior into
    # contract_tests even when no formal contract was discovered. Normalize those
    # cases before strict validation so the autonomous workflow does not interrupt
    # after human approval. The evidence links are preserved and the cases become
    # validation tests instead of pretending a formal API contract exists.
    contract_cases = result.get("contract_tests")
    validation_cases = result.get("validation_tests")
    if isinstance(contract_cases, list) and isinstance(validation_cases, list):
        api_contract_ids = source_ids["api_contracts"]
        retained_contracts: list[dict[str, Any]] = []
        reclassified_contracts: list[dict[str, Any]] = []
        for case in contract_cases:
            if not isinstance(case, dict):
                retained_contracts.append(case)
                continue
            links = case.get("traceability_links")
            linked_api_ids = (
                {
                    str(link.get("id"))
                    for link in links
                    if isinstance(link, dict) and link.get("type") == "api_contract"
                }
                if isinstance(links, list)
                else set()
            )
            if api_contract_ids and linked_api_ids & api_contract_ids:
                retained_contracts.append(case)
            else:
                reclassified_contracts.append(case)

        if reclassified_contracts:
            result["contract_tests"] = retained_contracts
            result["validation_tests"] = validation_cases + reclassified_contracts
            draft_gaps.append(
                f"Host reclassified {len(reclassified_contracts)} contract test(s) as validation tests "
                "because no matching explicit api_contract evidence was supplied."
            )

    # The model emits a compact authoring draft. Build deterministic marketplace
    # metadata on the host so the LLM does not waste output tokens reproducing
    # derived fields.
    result["metadata"] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "generator_version": "maf-host-finalizer-1",
        "input_sources": ["approved_requirements", normalized_repo_url],
        "tool": "test-design-generator",
        "repository_url": normalized_repo_url,
    }
    seen_test_ids: set[str] = set()
    tests: list[dict[str, Any]] = []
    for section in _SECTIONS:
        key = f"{section}_tests"
        cases = result.get(key)
        if not isinstance(cases, list):
            raise TypeError(f"{key} must be an array")
        for case in cases:
            if not isinstance(case, dict):
                raise TypeError(f"{key} entries must be objects")
            # Category is determined by the host collection, not by model text.
            case["type"] = section
            if allowed_levels is not None and case.get("level") not in allowed_levels:
                raise ValueError(
                    f"test '{case.get('title')}' uses level {case.get('level')!r}, which is unavailable "
                    f"(allowed: {sorted(allowed_levels)}). UI/E2E/accessibility levels need a running application."
                )
            known_actors = {a["name"].lower() for a in source.get("actors", [])} | {"system"}
            if source.get("actors") and str(case.get("actor", "")).lower() not in known_actors:
                raise ValueError(f"test '{case.get('title')}' cites unknown actor {case.get('actor')!r}")
            links = case.get("traceability_links")
            if not isinstance(links, list) or not links:
                raise ValueError("each test needs source links")
            for link in links:
                if not isinstance(link, dict) or link.get("type") not in _LINK_COLLECTION:
                    raise ValueError("invalid traceability link")
                collection = _LINK_COLLECTION[link["type"]]
                if link.get("id") not in source_ids[collection]:
                    resolved = resolve_id(link.get("id"), source_ids[collection])
                    if resolved is None:
                        raise ValueError(f"unknown {link['type']} source ID: {link.get('id')!r}")
                    link["id"] = resolved  # canonicalized (garbled hash suffix, letter case)
            case["traceability_links"] = sorted(
                links, key=lambda link: (str(link.get("type", "")), str(link.get("id", "")))
            )
            links = case["traceability_links"]
            if section == "contract":
                contracts = [by_id[link["id"]] for link in links if link["type"] == "api_contract"]
                if not contracts:
                    raise ValueError("contract test must link an api_contract")
                if "endpoint" in case and case["endpoint"] not in {item["path"] for item in contracts}:
                    raise ValueError(f"unsupported contract endpoint: {case['endpoint']!r}")
                if "method" in case and case["method"].upper() not in {
                    str(item["method"]).upper() for item in contracts
                }:
                    raise ValueError(f"unsupported contract method: {case['method']!r}")
                if "expected_status_codes" in case:
                    documented = {str(code) for item in contracts for code in item["status_codes"]}
                    unsupported = set(map(str, case["expected_status_codes"])) - documented
                    if unsupported:
                        raise ValueError(f"unsupported contract status codes: {sorted(unsupported)}")
            case["id"] = _case_id(section, case, normalized_repo_url)
            if case["id"] in seen_test_ids:
                raise ValueError("duplicate test content creates duplicate stable ID")
            seen_test_ids.add(case["id"])
            tests.append(case)
    if not tests:
        raise ValueError("design must contain at least one actionable test case")

    requirement_links: dict[str, set[str]] = {item_id: set() for item_id in source_ids["requirements"]}
    criterion_links: dict[str, set[str]] = {item_id: set() for item_id in source_ids["acceptance_criteria"]}
    validation_links: dict[str, set[str]] = {item_id: set() for item_id in source_ids["validation_rules"]}
    api_links: dict[str, set[str]] = {item_id: set() for item_id in source_ids["api_contracts"]}
    for case in tests:
        for link in case["traceability_links"]:
            target = {
                "requirement": requirement_links,
                "acceptance_criterion": criterion_links,
                "validation": validation_links,
                "api_contract": api_links,
            }[link["type"]]
            target[link["id"]].add(case["id"])
            if link["type"] == "acceptance_criterion":
                parent = by_id[link["id"]]["requirement_id"]
                requirement_links[parent].add(case["id"])

    def sorted_map(mapping: dict[str, set[str]]) -> dict[str, list[str]]:
        return {key: sorted(value) for key, value in sorted(mapping.items())}

    result["traceability_matrix"] = {
        "requirement_to_tests": {**sorted_map(requirement_links), **sorted_map(criterion_links)},
        "validation_to_tests": sorted_map(validation_links),
        "api_to_tests": sorted_map(api_links),
    }

    def coverage(mapping: dict[str, set[str]]) -> dict[str, dict[str, Any]]:
        return {
            key: {"covered": bool(value), "test_count": len(value), "test_ids": sorted(value)}
            for key, value in sorted(mapping.items())
        }

    result["coverage_analysis"] = {
        "requirement_coverage": coverage(requirement_links),
        "validation_coverage": coverage(validation_links),
        "api_coverage": coverage(api_links),
    }
    counts = {section: len(result[f"{section}_tests"]) for section in _SECTIONS}
    covered_requirements = sum(bool(value) for value in requirement_links.values())
    total_requirements = len(requirement_links)
    result["summary"] = {
        "total_tests_generated": sum(counts.values()),
        **{f"{section}_tests_count": counts[section] for section in _SECTIONS},
        "coverage_percentage": round(100 * covered_requirements / total_requirements, 2)
        if total_requirements
        else 0.0,
        "requirements_covered": covered_requirements,
        "total_requirements": total_requirements,
        "test_distribution": counts,
    }
    gaps = result.get("gaps_report")
    if not isinstance(gaps, dict):
        gaps = {}
    gaps.setdefault("missing_validations", [])
    gaps.setdefault("incomplete_requirements", [])
    gaps["uncovered_requirements"] = sorted(key for key, value in requirement_links.items() if not value)
    gaps["uncovered_validations"] = sorted(key for key, value in validation_links.items() if not value)
    gaps["coverage_gaps"] = sorted(
        [f"requirement:{key}" for key, value in requirement_links.items() if not value]
        + [f"validation:{key}" for key, value in validation_links.items() if not value]
        + [f"api_contract:{key}" for key, value in api_links.items() if not value]
    )
    gaps["notes"] = "; ".join(str(item).strip() for item in draft_gaps if str(item).strip())
    result["gaps_report"] = gaps
    _validate_design_schema(result, schema)
    return result


class _FlattenWithChains(ast.NodeTransformer):
    """Rewrite a pure chain of nested ``with`` statements into one ``with ExitStack()``.

    CPython refuses more than 20 statically nested blocks, and a multi-item ``with a, b:`` counts the same way, so a
    test that patches two dozen collaborators (a common way to unit-test an orchestration function) cannot be
    written with nested ``with``. ``ExitStack`` is the semantics-preserving form: contexts are entered in order and
    exited in reverse order, exactly like the nested statements.
    """

    def __init__(self, min_depth: int = 8):
        self.min_depth, self.changed = min_depth, False

    def visit_With(self, node: ast.With):
        chain, current = [node], node
        while len(current.body) == 1 and isinstance(current.body[0], ast.With):
            current = current.body[0]
            chain.append(current)
        items = [item for with_ in chain for item in with_.items]
        if len(items) < self.min_depth:
            return self.generic_visit(node)
        self.changed = True
        stack = ast.Name(id="_exit_stack", ctx=ast.Load())
        entered: list[ast.stmt] = []
        for item in items:
            call = ast.Call(
                func=ast.Attribute(value=stack, attr="enter_context", ctx=ast.Load()),
                args=[item.context_expr],
                keywords=[],
            )
            entered.append(
                ast.Assign(targets=[item.optional_vars], value=call)
                if item.optional_vars is not None
                else ast.Expr(value=call)
            )
        body = [self.visit(stmt) for stmt in current.body]
        new = ast.With(
            items=[
                ast.withitem(
                    context_expr=ast.Call(
                        func=ast.Name(id="ExitStack", ctx=ast.Load()), args=[], keywords=[]
                    ),
                    optional_vars=ast.Name(id="_exit_stack", ctx=ast.Store()),
                )
            ],
            body=entered + body,
        )
        return ast.copy_location(new, node)


def flatten_nested_with(content: str) -> str | None:
    """Source with deep ``with`` chains rewritten to ExitStack, or None when nothing needed changing."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None
    transformer = _FlattenWithChains()
    tree = transformer.visit(tree)
    if not transformer.changed:
        return None
    ast.fix_missing_locations(tree)
    imports = "from contextlib import ExitStack\n" if "ExitStack" not in content else ""
    return imports + ast.unparse(tree) + "\n"


def _python_tests(content: str) -> list:
    tree = ast.parse(content)
    function_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    tests = [node for node in tree.body if isinstance(node, function_types) and node.name.startswith("test_")]
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        unittest_class = any(ast.unparse(base).endswith("TestCase") for base in node.bases)
        if node.name.startswith("Test") or unittest_class:
            tests.extend(
                child
                for child in node.body
                if isinstance(child, function_types)
                and child.name.startswith("test" if unittest_class else "test_")
            )
    return tests


# A JS/TS declaration starts a line (`it(` / `test(`); `re.test(x)` calls and prose never do.
_JS_DECL = r"(?m)^[ \t]*(?:it|test)(?:\.(?:only|each\([^)]*\)))?\s*\("


_JAVA_SYNTAX_ERROR = re.compile(
    r"reached end of file|illegal start|unclosed|not a statement|class, interface|invalid method declaration|"
    r"orphaned|malformed|<identifier> expected|'[^']{1,3}' expected|expected$",
    re.I,
)


def _strip_strings_and_comments(text: str) -> str:
    return re.sub(
        r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`",
        "",
        text,
        flags=re.S,
    )


def _unbalanced_delimiters(content: str) -> str | None:
    """Cheap structural check for brace languages: catches truncated files and missing closers."""
    stack, pairs = [], {")": "(", "]": "[", "}": "{"}
    for ch in _strip_strings_and_comments(content):
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs and (not stack or stack.pop() != pairs[ch]):
            return f"unexpected '{ch}'"
    return f"unclosed '{stack[-1]}' (file looks truncated)" if stack else None


def _native_syntax_check(path: str, content: str) -> str | None:
    """Syntax check with a host tool when installed (node, gofmt, javac parse errors); brace balance otherwise."""
    suffix = PurePosixPath(path).suffix.lower()
    tool = {
        ".js": ["node", "--check"],
        ".mjs": ["node", "--check"],
        ".go": ["gofmt", "-e"],
        ".java": ["javac", "-proc:none", "-d"],
    }.get(suffix)
    if tool and shutil.which(tool[0]):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / (PurePosixPath(path).name if suffix == ".java" else f"candidate{suffix}")
            target.write_text(content, encoding="utf-8")
            args = [*tool, tmp, str(target)] if suffix == ".java" else [*tool, str(target)]
            try:
                proc = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
            except (OSError, subprocess.TimeoutExpired):
                proc = None
        if proc is not None and proc.returncode:
            lines = [
                ln for ln in (proc.stderr or proc.stdout).splitlines() if "error:" in ln or suffix != ".java"
            ]
            if suffix != ".java":
                return (lines or ["syntax error"])[0].strip()
            # javac also reports unresolved symbols (dependencies are not on this path); only syntax errors count.
            syntax = [ln for ln in lines if _JAVA_SYNTAX_ERROR.search(ln)]
            if syntax:
                return syntax[0].strip()
    if suffix in {
        ".java",
        ".kt",
        ".cs",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".go",
        ".rs",
        ".php",
        ".c",
        ".cpp",
    }:
        return _unbalanced_delimiters(content)
    return None


def _test_declaration_names(path: str, content: str) -> list[str]:
    """Names of the test methods a file declares (Java/Kotlin/Python), for actionable feedback."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".java", ".kt"}:
        return re.findall(r"@(?:[\w.]+\.)?Test\b[^\n]*\s+(?:public\s+)?(?:void|fun)\s+(\w+)", content)
    if suffix == ".py":
        return [t.name for t in _python_tests(content)]
    return []


def _test_declaration_count(path: str, content: str) -> int:
    """Best-effort cross-language count of concrete test declarations."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".py":
        return len(_python_tests(content))
    patterns = {
        ".py": r"(?m)^\s*(?:async\s+)?def\s+test_[A-Za-z0-9_]+\s*\(",
        ".js": _JS_DECL,
        ".jsx": _JS_DECL,
        ".ts": _JS_DECL,
        ".tsx": _JS_DECL,
        ".mjs": _JS_DECL,
        ".java": r"@(?:[\w.]+\.)?(?:Test|ParameterizedTest|RepeatedTest)\b",
        ".kt": r"@(?:[\w.]+\.)?(?:Test|ParameterizedTest)\b",
        ".cs": r"\[(?:Fact|Theory|Test|TestMethod)\b",
        ".go": r"(?m)^\s*func\s+Test[A-Za-z0-9_]+\s*\(",
        ".rb": r"(?m)^\s*(?:it|test)\s+[\"']",
        ".php": r"(?mi)(?:function\s+test[A-Za-z0-9_]+\s*\(|#\[Test\])",
        ".rs": r"#\[test\]",
        ".swift": r"(?m)^\s*func\s+test[A-Za-z0-9_]+\s*\(",
        ".cpp": r"\bTEST(?:_F|_P)?\s*\(",
        ".cc": r"\bTEST(?:_F|_P)?\s*\(",
        ".cxx": r"\bTEST(?:_F|_P)?\s*\(",
        ".c": r"(?m)^\s*(?:void|int)\s+test_[A-Za-z0-9_]+\s*\(",
    }
    pattern = patterns.get(suffix)
    return len(re.findall(pattern, content)) if pattern else 0


def _case_marker_comment(path: str, case_id: str) -> str:
    prefix = "#" if PurePosixPath(path).suffix.lower() in {".py", ".rb"} else "//"
    return f"{prefix} CASE_ID: {case_id}"


def expand_generated_lines(payload: dict[str, Any]) -> dict[str, Any]:
    """Join model-supplied ``lines`` into ``content`` (the form validation, export and execution use)."""
    result = deepcopy(payload)
    for item in result.get("files", []) if isinstance(result, dict) else []:
        if isinstance(item, dict) and "lines" in item:
            lines = [str(line).rstrip("\r\n") for line in item.pop("lines")]
            item["content"] = "\n".join(lines).strip("\n") + "\n"
    return result


def ensure_generated_case_markers(payload: dict[str, Any]) -> dict[str, Any]:
    """Inject canonical CASE_ID comments on the host instead of trusting the model.

    Generation uses one file per approved design case. This makes the mapping
    deterministic and prevents a valid test from being rejected only because the
    model omitted a comment marker.
    """
    result = deepcopy(payload)
    files = result.get("files") if isinstance(result, dict) else None
    if not isinstance(files, list):
        return result
    for item in files:
        if not isinstance(item, dict):
            continue
        linked = item.get("case_ids")
        path = item.get("path")
        content = item.get("content")
        if not (
            isinstance(linked, list)
            and len(linked) == 1
            and isinstance(path, str)
            and isinstance(content, str)
        ):
            continue
        case_id = linked[0]
        marker = f"CASE_ID: {case_id}"
        if marker in content:
            continue
        comment = _case_marker_comment(path, case_id)
        if PurePosixPath(path).suffix.lower() == ".php" and content.lstrip().startswith("<?php"):
            start = content.find("<?php") + len("<?php")
            content = content[:start] + "\n" + comment + content[start:]
        else:
            content = comment + "\n" + content
        item["content"] = content
    return result


def validate_generated_files(payload: dict[str, Any], case_ids: set[str]) -> dict[str, Any]:
    """Validate generated paths, implementation traceability, and non-placeholder tests."""
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list) or not payload["files"]:
        raise ValueError("payload.files must be a nonempty array")
    if not isinstance(case_ids, set) or not all(isinstance(value, str) and value for value in case_ids):
        raise ValueError("case_ids must be a set of nonempty strings")
    covered: set[str] = set()
    seen_paths: set[str] = set()
    declaration_total = 0
    for item in payload["files"]:
        if not isinstance(item, dict):
            raise TypeError("file entry must be an object")
        path, content, linked = item.get("path"), item.get("content"), item.get("case_ids")
        if not isinstance(path, str) or not path.strip() or "\\" in path:
            raise ValueError("generated path must be nonempty POSIX text")
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or any(part in {".git", ".env"} for part in pure.parts):
            raise ValueError(f"unsafe generated path: {path}")
        if path in seen_paths:
            raise ValueError(f"duplicate generated path: {path}")
        if pure.as_posix() != path or ":" in path or any(ord(c) < 32 for c in path):
            raise ValueError(f"unsafe generated path: {path}")
        seen_paths.add(path)
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"generated file is empty: {path}")
        if pure.suffix.lower() == ".py":
            try:
                tree = ast.parse(content, filename=path)
                # ast.parse does not compile: limits such as "too many statically nested blocks" (>20 nested
                # `with`/`for`) only fail in compile(), i.e. at test collection time. Catch them now.
                compile(content, path, "exec", dont_inherit=True)
            except (SyntaxError, ValueError, RecursionError) as exc:
                raise ValueError(f"generated Python source is invalid in {path}: {exc}") from exc
            imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
            if sum(len(node.names) for node in imports) > 20:
                raise ValueError(f"generated file contains excessive imports: {path}")
            for test in _python_tests(content):
                body = [
                    node
                    for node in test.body
                    if not (
                        isinstance(node, ast.Expr)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    )
                ]
                if not body or all(
                    isinstance(node, ast.Pass)
                    or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
                    for node in body
                ):
                    raise ValueError(f"generated file contains a placeholder test body: {path}")
                if any(
                    isinstance(node, ast.Assert)
                    and isinstance(node.test, ast.Constant)
                    and node.test.value is True
                    for node in ast.walk(test)
                ):
                    raise ValueError(f"generated file contains a trivial placeholder assertion: {path}")
                for node in ast.walk(test):
                    if isinstance(node, ast.Assert) and not any(
                        isinstance(child, (ast.Name, ast.Call, ast.Attribute))
                        for child in ast.walk(node.test)
                    ):
                        raise ValueError(f"generated test contains a constant assertion: {path}")
        if _PLACEHOLDER.search(content):
            raise ValueError(f"generated file contains placeholder work: {path}")
        if _SKIP_MARKERS.search(content):
            raise ValueError(f"generated file skips or disables its test (placeholder): {path}")
        syntax_error = _native_syntax_check(path, content)
        if syntax_error:
            raise ValueError(f"generated source has a syntax error in {path}: {syntax_error}")
        if (
            re.search(r"(?im)^\s*(?:assert\s+True|pass)\s*$", content)
            and len(content.strip().splitlines()) <= 12
        ):
            raise ValueError(f"generated file contains a trivial placeholder test: {path}")
        if (
            not isinstance(linked, list)
            or not linked
            or not all(isinstance(value, str) and value for value in linked)
        ):
            raise ValueError(f"generated file needs case_ids: {path}")
        if len(linked) != len(set(linked)):
            raise ValueError(f"generated file repeats case_ids: {path}")
        if len(linked) != 1:
            raise ValueError(
                f"generated file must implement exactly one approved case ID so traceability is unambiguous: {path}"
            )
        if covered & set(linked):
            raise ValueError(f"approved case implemented more than once: {linked}")
        unknown = set(linked) - case_ids
        if unknown:
            raise ValueError(f"generated file references unknown case IDs: {sorted(unknown)}")
        missing_markers = [case_id for case_id in linked if f"CASE_ID: {case_id}" not in content]
        if missing_markers:
            raise ValueError(
                f"generated source is missing CASE_ID markers in {path}: {', '.join(missing_markers)}"
            )
        declarations = _test_declaration_count(path, content)
        if not declarations:
            raise ValueError(f"generated file has no recognized concrete test declarations: {path}")
        if declarations != 1:
            names = ", ".join(_test_declaration_names(path, content)[:6])
            raise ValueError(
                f"generated file must contain exactly one concrete test declaration for its one case ID: {path} "
                f"(found {declarations}{': ' + names if names else ''}). Keep ONE test method and move the extra "
                "assertions of the others into it; do not add a second test for a second assertion."
            )
        declaration_total += declarations
        covered.update(linked)
    return {
        "files": len(payload["files"]),
        "test_declarations": declaration_total,
        "covered_case_ids": sorted(covered),
        "uncovered_case_ids": sorted(case_ids - covered),
    }


_COMMON_TEST_MODULES = {
    "pytest",
    "unittest",
    "mock",
    "pytest_mock",
    "pytest_asyncio",
    "requests_mock",
    "responses",
    "freezegun",
    "hypothesis",
    "_pytest",
}


def _module_symbols(source: str) -> tuple[set[str], dict[str, tuple[str, int]]] | None:
    """Names importable from a module (module-level bindings) and names that exist only as locals of a function.

    Returns ``None`` when the set cannot be known statically (``from x import *`` or a module ``__getattr__``).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    names: set[str] = set()

    def bind(target: ast.AST) -> None:
        for node in ast.walk(target):
            if isinstance(node, ast.Name):
                names.add(node.id)

    def visit(body: list[ast.stmt]) -> bool:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    bind(target)
            elif isinstance(node, ast.AnnAssign | ast.AugAssign):
                bind(node.target)
            elif isinstance(node, ast.Import):
                names.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if any(a.name == "*" for a in node.names):
                    return False
                names.update(a.asname or a.name for a in node.names)
            elif isinstance(node, ast.If | ast.Try | ast.With | ast.For | ast.While):
                for field in ("body", "orelse", "finalbody", "handlers"):
                    for child in getattr(node, field, []):
                        if isinstance(child, ast.ExceptHandler):
                            if not visit(child.body):
                                return False
                        elif not visit([child]):
                            return False
        return True

    if not visit(tree.body) or "__getattr__" in names:
        return None
    local: dict[str, tuple[str, int]] = {}
    for func in ast.walk(tree):
        if isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            for node in ast.walk(func):
                inner = (
                    [node.name]
                    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                    and node is not func
                    else [
                        t.id
                        for t in ast.walk(node)
                        if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store)
                    ]
                    if isinstance(node, ast.Assign)
                    else []
                )
                for name in inner:
                    local.setdefault(name, (func.name, getattr(node, "lineno", func.lineno)))
    return names, local


def _check_imported_names(
    node: ast.ImportFrom, module_file: str, source: str, modules: set[str], path: str
) -> None:
    """`from pkg.mod import a, b`: every name must be a module-level binding (or a submodule) of pkg/mod.py."""
    parsed = _module_symbols(source)
    if parsed is None:
        return
    names, local = parsed
    base = module_file.removesuffix(".py").removesuffix("/__init__")
    for alias in node.names:
        if alias.name in names or f"{base}/{alias.name}" in modules:
            continue
        where = local.get(alias.name)
        hint = (
            f" It exists only inside {where[0]}() (line {where[1]}), which cannot be imported: exercise that code by "
            f"calling {where[0]}() with its collaborators patched, or test a module-level function instead."
            if where
            else " Use only names defined at module level in the supplied source."
        )
        raise ValueError(
            f"cannot import name '{alias.name}' from '{node.module}' in {path or 'generated test'}: "
            f"{module_file} defines no such module-level name.{hint}"
        )


def extra_import_roots(py_files: list[str], limit: int = 12) -> list[str]:
    """Directories whose modules cannot be imported by dotted path (a hyphen or leading digit in the path).

    The runner puts these directories on PYTHONPATH, exactly as a maintainer would configure ``pythonpath`` for the
    suite, so a test can simply ``import module`` for a file like ``skill-packages/03-agent-sdk/module.py``.
    """
    roots = {
        parent.as_posix()
        for parent in (PurePosixPath(f).parent for f in py_files if f.endswith(".py"))
        if parent.as_posix() != "." and not all(part.isidentifier() for part in parent.parts)
    }
    return sorted(roots)[:limit]


def check_python_imports(
    content: str,
    repo_files: list[str],
    dependency_text: str,
    path: str = "",
    read_source: Callable[[str], str | None] | None = None,
) -> None:
    """Reject imports that cannot resolve: not stdlib, not a repository module, not a declared dependency.

    Catches the most common model failures (guessing an import path, importing a name that does not exist at module
    level) before a run is wasted on them. With ``read_source`` the imported *names* are verified too.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return
    stdlib = getattr(__import__("sys"), "stdlib_module_names", frozenset())
    # The runner puts the workspace root and src/ (and lib/) on PYTHONPATH, so all three layouts resolve.
    py_files = [f for f in repo_files if f.endswith(".py")]
    import_roots = ("", "src/", "lib/", *(f"{r}/" for r in extra_import_roots(py_files)))
    rooted = {f[len(root) :] for f in py_files for root in import_roots if f.startswith(root)}
    modules_in_repo = {PurePosixPath(f).with_suffix("").as_posix() for f in rooted}
    packages = {PurePosixPath(f).parent.as_posix() for f in rooted}
    dependencies = dependency_text.lower().replace("_", "-")
    stems = {PurePosixPath(m).name for m in modules_in_repo}
    # A test that puts a directory on sys.path (or loads a file by path) may import its modules by bare name.
    path_aware = "sys.path" in content or "spec_from_file_location" in content
    for node in ast.walk(tree):
        names = (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module]
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
            else []
        )
        for name in names:
            top = name.split(".")[0]
            if top in stdlib or top in _COMMON_TEST_MODULES:
                continue
            dotted = name.replace(".", "/")
            if (
                dotted in modules_in_repo
                or dotted in packages
                or top in {m.split("/")[0] for m in modules_in_repo}
            ):
                if (
                    dotted in modules_in_repo
                    or dotted in packages
                    or any(m.startswith(dotted + "/") for m in modules_in_repo)
                ):
                    if read_source and isinstance(node, ast.ImportFrom):
                        for rel in (
                            f"{dotted}.py",
                            f"{dotted}/__init__.py",
                            f"src/{dotted}.py",
                            f"lib/{dotted}.py",
                        ):
                            source = read_source(rel)
                            if source is not None:
                                _check_imported_names(node, rel, source, modules_in_repo, path)
                                break
                    continue
                raise ValueError(
                    f"import '{name}' in {path or 'generated test'} does not match any repository path "
                    f"(top-level '{top}' exists; check the sub-module against source_files)"
                )
            if top.lower().replace("_", "-") in dependencies:
                continue
            leaf = name.split(".")[-1]
            if path_aware and leaf in stems:
                continue
            hints = sorted(m for m in modules_in_repo if m.endswith("/" + leaf) or m == leaf)[:3]
            if hints and any(not all(part.isidentifier() for part in h.split("/")) for h in hints):
                shown = ", ".join(f"{h}.py" for h in hints)
                raise ValueError(
                    f"import '{name}' in {path or 'generated test'} cannot work: the module lives at {shown}, whose path "
                    "is not an importable package (hyphen or leading digit). The tests run with the repository root as the "
                    "working directory: add that directory to sys.path (e.g. sys.path.insert(0, str(Path.cwd() / "
                    "'<dir>'))) or load the file with importlib.util.spec_from_file_location, then import it."
                )
            hint = (
                f" Did you mean the repository module {', '.join(h.replace('/', '.') for h in hints)}?"
                if hints
                else ""
            )
            raise ValueError(
                f"import '{name}' in {path or 'generated test'} is not stdlib, not a repository module "
                f"and not a declared dependency.{hint} Derive import paths from source_files."
            )


def _call_name(node: ast.Call) -> str:
    """Dotted name of the callee: ``mocker.patch.object`` -> 'mocker.patch.object'."""
    parts: list[str] = []
    func: ast.AST = node.func
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _string_arg(node: ast.Call, index: int = 0) -> str | None:
    if len(node.args) > index and isinstance(node.args[index], ast.Constant):
        value = node.args[index].value
        return value if isinstance(value, str) else None
    return None


def _mocks_sys_exit(node: ast.Call) -> bool:
    """patch("sys.exit") / patch.object(sys, "exit") / monkeypatch.setattr(sys, "exit", mock) without side_effect."""
    name = _call_name(node)
    if any(k.arg == "side_effect" for k in node.keywords):
        return False  # patch("sys.exit", side_effect=SystemExit) is the correct way to mock it
    if name.endswith(("patch", "patch.object")):
        target = _string_arg(node)
        if (
            name.endswith("patch")
            and target
            and (target in {"sys.exit", "exit"} or target.endswith(".sys.exit"))
        ):
            return True  # also 'pkg.module.sys.exit', which patches the same global function
        return (
            name.endswith("patch.object")
            and bool(node.args)
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "sys"
            and _string_arg(node, 1) == "exit"
        )
    if name.endswith("setattr"):
        first = node.args[0] if node.args else None
        by_string = isinstance(first, ast.Constant) and first.value == "sys.exit"
        by_module = isinstance(first, ast.Name) and first.id == "sys" and _string_arg(node, 1) == "exit"
        value = node.args[-1] if len(node.args) >= (2 if by_string else 3) else None
        return (by_string or by_module) and isinstance(value, ast.Lambda | ast.Call)
    return False


_BUILTIN_NAMES = frozenset(dir(__import__("builtins"))) | {
    "__file__",
    "__name__",
    "__doc__",
    "__spec__",
    "__package__",
    "__loader__",
    "__builtins__",
    "__class__",
    "__path__",
    "__debug__",
}


def _bound_names(tree: ast.AST) -> set[str] | None:
    """Every name bound anywhere in the module (any scope); ``None`` when a star import makes that unknowable."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            bound.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Import):
            bound.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if any(a.name == "*" for a in node.names):
                return None
            bound.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Global | ast.Nonlocal):
            bound.update(node.names)
        elif isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
    return bound


def _repo_module_files(repo_files: list[str]) -> dict[str, str]:
    """Dotted module name -> repository file, for every layout the runner puts on PYTHONPATH."""
    py_files = [f for f in repo_files if f.endswith(".py")]
    roots = ("", "src/", "lib/", *(f"{r}/" for r in extra_import_roots(py_files)))
    modules: dict[str, str] = {}
    for f in py_files:
        for root in roots:
            if f.startswith(root):
                dotted = f[len(root) :].removesuffix(".py").removesuffix("/__init__").replace("/", ".")
                modules.setdefault(dotted, f)
    return modules


def check_python_test_hygiene(
    content: str,
    path: str = "",
    repo_files: list[str] | None = None,
    read_source: Callable[[str], str | None] | None = None,
) -> None:
    """Static defects that always fail at run time, found before a run is spent on them.

    * mocking ``sys.exit`` (execution continues past the exit and fails later),
    * a name used but never bound or imported anywhere in the file (a missing ``import unittest``),
    * ``patch("pkg.mod.name")`` where ``name`` is not a module-level binding of ``pkg/mod.py`` (typically a package that
      the code imports lazily inside a function, so the attribute does not exist when the patch is applied).
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return
    where = path or "generated test"
    modules = _repo_module_files(repo_files or [])
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _mocks_sys_exit(node):
            raise ValueError(
                f"{where} line {node.lineno} mocks sys.exit: code keeps running past the mocked exit and fails later. "
                "Use `with pytest.raises(SystemExit) as exc:` and assert `exc.value.code`."
            )
        name = _call_name(node)
        target = _string_arg(node)
        if (
            name.endswith("patch")
            and target
            and modules
            and read_source
            and not any(k.arg == "create" for k in node.keywords)
        ):
            parts = target.split(".")
            for cut in range(len(parts) - 1, 0, -1):
                module_file = modules.get(".".join(parts[:cut]))
                if module_file is None:
                    continue
                parsed = _module_symbols(read_source(module_file) or "")
                attribute = parts[cut]
                if ".".join(parts[: cut + 1]) in modules or (
                    attribute in _BUILTIN_NAMES and not attribute.startswith("_")
                ):
                    break  # a submodule, or a builtin (mock.patch creates those on module targets)
                if parsed is not None and attribute not in parsed[0]:
                    local = parsed[1].get(attribute)
                    hint = (
                        f" It is only imported/assigned inside {local[0]}(), so it does not exist on the module: patch "
                        f"`sys.modules` with a stub before calling {local[0]}(), or patch a module-level name."
                        if local
                        else (
                            " Patch only module-level names shown in target_source; if it is a package the module imports "
                            "lazily inside a function, patch `sys.modules` with a stub instead."
                        )
                    )
                    raise ValueError(
                        f"{where} line {node.lineno} patches '{target}' but {module_file} has no module-level "
                        f"'{attribute}'.{hint}"
                    )
                break
    _reject_unused_setup(tree, where)
    bound = _bound_names(tree)
    if bound is not None:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id not in bound
                and node.id not in _BUILTIN_NAMES
            ):
                raise ValueError(
                    f"NameError: name '{node.id}' is not defined in {where} line {node.lineno}: add the missing import "
                    "or definition."
                )


_COLLECTION_VALUES = (ast.List, ast.Dict, ast.Set, ast.Tuple, ast.ListComp, ast.DictComp, ast.SetComp)


def _reject_unused_setup(tree: ast.AST, where: str) -> None:
    """A collection built in a test function and never read: the setup never reaches the code under test.

    The typical case is a boundary test that builds the inputs it describes (``sections = [...]``) and then calls the
    function with unrelated empty arguments, so the assertion checks something else entirely.
    """
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef) or not func.name.startswith("test"):
            continue
        assigned = {
            target.id: node.lineno
            for node in ast.walk(func)
            if isinstance(node, ast.Assign) and isinstance(node.value, _COLLECTION_VALUES)
            for target in node.targets
            if isinstance(target, ast.Name) and not target.id.startswith("_")
        }
        loaded = {n.id for n in ast.walk(func) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        for name, line in assigned.items():
            if name not in loaded:
                raise ValueError(
                    f"{where} line {line}: '{name}' is built but never used, so the test's setup never reaches the code "
                    "under test. Pass it to the function under test (or patch the collaborators that produce it)."
                )


_URL_LINE = re.compile(r"BASE_URL|base_url|baseUrl", re.I)
_URL_LITERAL = re.compile(r"""["'`](/[A-Za-z0-9_\-./{}%$]*)""")


def check_route_literals(content: str, path: str, routes: list[str], app_url: str | None) -> None:
    """Live-application tests must use URL paths the code defines, relative to the BASE_URL the human supplied.

    Only lines that build a URL from BASE_URL are checked (high precision). The common failure this catches is a
    guessed context path (``/petstore/rest/items``) when BASE_URL already ends in the real one.
    """
    if not routes:
        return
    first_segments = {r.split(" ", 1)[1].strip("/").split("/")[0] for r in routes if " " in r}
    context = (urlsplit(app_url).path.strip("/").split("/")[0]) if app_url else ""
    for number, line in enumerate(content.splitlines(), 1):
        if not _URL_LINE.search(line):
            continue
        for literal in _URL_LITERAL.findall(line):
            segment = literal.strip("/").split("/")[0]
            if not segment or segment.startswith(("{", "$")):
                continue
            if context and segment == context:
                raise ValueError(
                    f"{path} line {number}: '{literal}' repeats the context path '/{context}' that BASE_URL "
                    f"({app_url}) already contains. Append only the route, e.g. BASE_URL + '/{sorted(first_segments)[0]}'."
                )
            if segment not in first_segments:
                known = ", ".join(sorted(f"/{s}" for s in first_segments)[:8])
                raise ValueError(
                    f"{path} line {number}: '{literal}' is not a route this application defines. Routes start with: "
                    f"{known}. BASE_URL already contains the application context path."
                )


_JAVA_IMPORT = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([A-Za-z_][\w.]*)\s*(?:\.\*)?;")
_JAVA_PLATFORM = ("java.", "javax.", "jakarta.", "jdk.", "sun.")


def check_java_imports(content: str, repo_files: list[str], dependency_text: str, path: str = "") -> None:
    """Reject imports of libraries that are neither JDK, nor repository code, nor declared in the build files.

    One unresolvable import breaks compilation of the whole test module (every test then reports as not run),
    so this is checked before a run is wasted. Matching is by library token (e.g. `restassured`, `junit`).
    """
    repo_packages = set()
    for f in repo_files:
        if f.endswith(".java"):
            parts = PurePosixPath(f).parts
            for marker in ("java", "kotlin"):
                if marker in parts:
                    repo_packages.add(".".join(parts[parts.index(marker) + 1 : -1]))
                    break
    normalized = re.sub(r"[^a-z0-9]", "", dependency_text.lower())
    for name in _JAVA_IMPORT.findall(content):
        if name.startswith(_JAVA_PLATFORM):
            continue
        if any(
            name == pkg or name.startswith(pkg + ".") or pkg.startswith(name + ".")
            for pkg in repo_packages
            if pkg
        ):
            continue
        segments = name.split(".")
        token = re.sub(r"[^a-z0-9]", "", (segments[1] if len(segments) > 2 else segments[0]).lower())
        if token and token in normalized:
            continue
        raise ValueError(
            f"import '{name}' in {path or 'generated test'} needs a library that is not declared in the build files "
            f"(looked for '{token}' in pom.xml/build.gradle) and is not repository code. Use only JDK classes, "
            "repository classes and the dependencies already declared; do not assume RestAssured, AssertJ or Mockito "
            "unless the build file declares them. For HTTP calls use java.net.http.HttpClient (JDK 11+) or "
            'java.net.HttpURLConnection, reading the base URL from System.getenv("BASE_URL").'
        )

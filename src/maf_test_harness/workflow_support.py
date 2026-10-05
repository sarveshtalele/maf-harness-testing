"""Shared workflow constants and artifact helpers."""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .settings import (
    GENERATION_BATCH_SIZE,
    GENERATION_MAX_ATTEMPTS,
)
from .skills.contracts import (
    extra_import_roots,
    resolve_id,
)

CORE_STAGES = ["repo_analysis", "requirements", "test_planning", "test_design", "test_generation"]


ENV_STAGE = "environment_setup"


EXECUTION_STAGE = "test_execution"


STAGES = [
    "repo_analysis",
    "requirements",
    "test_planning",
    "test_design",
    "test_generation",
    ENV_STAGE,
    EXECUTION_STAGE,
]


TEST_GENERATION_BATCH_SIZE = GENERATION_BATCH_SIZE


TEST_GENERATION_MAX_ATTEMPTS = GENERATION_MAX_ATTEMPTS


MAX_HEAL_ROUNDS = 3


MODEL_REPAIR_ATTEMPTS = 3  # host-validation repair loop for requirements/planning/design


LIVE_APP_LEVELS = {"ui", "e2e", "accessibility"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def atomic_json(path: Path, data: dict):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def _canonicalize_case_ids(candidate: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """Rewrite garbled case IDs (see skill_contracts.resolve_id) to the approved ones before validation."""
    for item in candidate.get("files", []):
        item["case_ids"] = [resolve_id(cid, known) or cid for cid in item.get("case_ids", [])]
    return candidate


def _compact_repo(analysis: dict[str, Any], preferred: list[str]) -> dict[str, Any]:
    """Repository facts a generation call needs; the full inventory is not resent on every call."""
    sources = analysis.get("source_files", [])
    wanted = set(preferred)
    ordered = [f for f in sources if f in wanted] + [f for f in sources if f not in wanted]
    keys = (
        "repo_name",
        "commit_sha",
        "languages",
        "frameworks",
        "test_frameworks",
        "likely_test_directories",
        "has_tests",
    )
    return {
        **{k: analysis.get(k) for k in keys},
        "package_files": (analysis.get("package_files") or [])[:30],
        "source_files": ordered[:120],
        "source_files_total": len(sources),
        "existing_test_files": (analysis.get("test_files") or [])[:30],
    }


def _requirements_for(requirements: dict[str, Any], linked_ids: set[str]) -> dict[str, Any]:
    """Only the requirements, criteria, rules and contracts the batch's cases trace to (plus the actors)."""
    compact = _compact_requirements(requirements)
    if not linked_ids:
        return compact
    reqs = {r["id"] for r in compact["requirements"] if r["id"] in linked_ids}
    compact["requirements"] = [r for r in compact["requirements"] if r["id"] in reqs]
    for key in ("acceptance_criteria", "validation_rules"):
        compact[key] = [c for c in compact[key] if c["id"] in linked_ids or c.get("requirement_id") in reqs]
    compact["api_contracts"] = [c for c in compact["api_contracts"] if c["id"] in linked_ids]
    return compact


def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {k: plan.get(k) for k in ("objective", "recommended_frameworks", "setup_actions") if k in plan}


def _allowed_levels(state: dict[str, Any]) -> set[str]:
    base = {"unit", "integration", "api", "cli", "security", "performance"}
    if not state.get("app_url"):
        return base
    live = set(LIVE_APP_LEVELS)
    tooling = (state.get("environment") or {}).get("ui_tooling")
    # A Python environment without a browser driver cannot run browser tests: offer only what it can execute.
    if (
        tooling is not None
        and (state.get("stack") or {}).get("primary") == "python"
        and not tooling.get("playwright")
        and not tooling.get("node_playwright")
    ):
        live -= {"e2e", "accessibility"}
        if not tooling.get("streamlit"):
            live -= {"ui"}
    return base | live


def _compact_requirements(source: dict[str, Any]) -> dict[str, Any]:
    model = source.get("requirement_model", {})
    return {
        "app_name": source.get("app_name"),
        "summary": source.get("summary", ""),
        "actors": source.get("actors", []),
        "requirements": model.get("requirements", []),
        "validation_rules": model.get("validation_rules", []),
        "acceptance_criteria": model.get("acceptance_criteria", []),
        "api_contracts": source.get("api_contracts", []),
        "assumptions": source.get("assumptions", []),
        "gaps": source.get("gaps", []),
    }


def _compact_design(design: dict[str, Any]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for section in ("positive", "negative", "boundary", "validation", "contract"):
        for case in design.get(f"{section}_tests", []) or []:
            cases.append(
                {
                    "id": case.get("id"),
                    "type": section,
                    "title": case.get("title"),
                    "priority": case.get("priority"),
                    "level": case.get("level"),
                    "actor": case.get("actor"),
                    "target": case.get("target"),
                    "description": case.get("description", ""),
                    "preconditions": case.get("preconditions", []),
                    "data_notes": case.get("data_notes", ""),
                    "steps": case.get("steps", []),
                    "expected_result": case.get("expected_result"),
                    "traceability_links": case.get("traceability_links", []),
                }
            )
    return {"cases": cases, "gaps_report": design.get("gaps_report", {})}


_COMMON_LIBRARIES = {
    "com.jayway.restassured / io.restassured": "restassured",
    "org.jboss.arquillian": "arquillian",
    "org.mockito": "mockito",
    "org.assertj": "assertj",
    "org.hamcrest": "hamcrest",
    "org.testng": "testng",
}


def _undeclared_libraries(declared: list[str]) -> list[str]:
    """Popular test libraries the build files do NOT declare: a model reaches for them by habit."""
    text = " ".join(declared).lower().replace("-", "")
    return [name for name, token in _COMMON_LIBRARIES.items() if token not in text]


def _generation_policy(repo: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    languages = repo.get("languages") or {}
    dominant = max(languages, key=languages.get) if languages else "Unknown"
    state = state or {}
    stack = state.get("stack") or {}
    is_jvm = stack.get("primary") == "java" or dominant.lower() in {"java", "kotlin"}
    return {
        "dominant_language": dominant,
        "languages": languages,
        "user_repository_context_unverified": state.get("repo_knowledge", ""),
        "application_frameworks": repo.get("frameworks", []),
        "detected_test_frameworks": repo.get("test_frameworks", []),
        "existing_test_files": (repo.get("test_files") or [])[:30],
        "likely_test_directories": repo.get("likely_test_directories", []),
        "package_files": (repo.get("package_files") or [])[:30],
        "selected_application_component": state.get("app_component"),
        "component_path_rule": "Place Node tests inside the component containing its package.json. For the selected frontend, write new specs under <selected_application_component>/tests/. Root tests/ cannot resolve nested frontend dependencies. Other ecosystems belong to their own manifest component.",
        "python_import_roots": extra_import_roots(repo.get("source_files") or []),
        "declared_dependencies": (state.get("declared_dependencies") or [])[:120],
        "forbidden_imports": _undeclared_libraries(state.get("declared_dependencies") or []),
        "dependency_rule": (
            (
                "Use only the JDK/standard library and the declared_dependencies. "
                if is_jvm
                else "Use only the standard library and the declared_dependencies. "
            )
            + "Any other import is rejected by the host before you are asked again, which wastes a call."
        ),
        "structure_rule": (
            "One file, one case ID, one test declaration: literally one `def test_...` / `@Test` method in the file. "
            "When a case covers more than one variant of the same behavior - a different input (a flag and its alias "
            "such as --help/-h, GET vs HEAD), a different expected outcome, or a different exception/error type (the "
            "same function raising ValueError vs RuntimeError for two invalid inputs) - that is still ONE test: assert "
            "every variant inside that single function, or parametrize it"
            + (
                " (JUnit `@ParameterizedTest` with `@ValueSource`/`@MethodSource`)"
                if is_jvm
                else " (`@pytest.mark.parametrize(('value', 'expected'), [(...), (...)])`)"
            )
            + " - never write a second function for the second variant. The host rejects a second declaration before "
            "you are asked again, which wastes a call."
        ),
        "stack": {
            k: stack.get(k) for k in ("primary", "test_runner", "web_ui", "http_service") if k in stack
        },
        "environment": {
            "capability_status": "planned until execution setup is verified",
            "python": (state.get("environment") or {}).get("python_version"),
            "unavailable_packages": (state.get("environment") or {}).get("missing_packages") or [],
            "ui_tooling": (state.get("environment") or {}).get("ui_tooling"),
            "rule": (
                "Environment creation and runtime validation are deferred until Test execution. The tests will run in the isolated environment described here. Before execution, dependencies are planned from repository manifests and are not verified installed. After setup approval, declared dependencies are checked; failures remain explicit. Packages are unavailable only when "
                "listed in unavailable_packages (then: never import it; patch the code that needs it, or stub it in "
                "sys.modules before the import). Do not add sys.path hacks or install steps."
            ),
        },
        "live_app": {
            "available": bool(state.get("app_url")),
            "base_url_env_var": "BASE_URL",
            "base_url": state.get("app_url"),
            "routes": (state.get("routes") or [])[:60],
            "rule": (
                "UI/E2E/API tests against the running application read the target from the BASE_URL environment "
                "variable; never hard-code a host. BASE_URL ALREADY INCLUDES the application's context path: append "
                "only a route from `routes` (BASE_URL + '/rest/items'), never a guessed prefix such as '/petstore'. "
                "Use only the HTTP methods listed for a route and send bodies in the format the source shows."
            ),
        },
        "level_rules": {
            "unit": "Isolated, no network; mock boundaries but never the behavior under test.",
            "integration": "Real collaborators in-process; temp dirs/in-memory DBs only. Use the database schema verbatim, unique composite keys and actual response fields. Close connections in finally/context managers and roll back on failure. Never mutate os.environ at module import; configure captured database paths with a per-test monkeypatch before importing the target, restoring imported module constants when necessary. Derive input JSON nesting and expected totals from the real parser, never invented fields.",
            "api": "Use the framework's in-process test client when one exists, else BASE_URL.",
            "cli": "Invoke the real entry point via subprocess or the framework runner; assert exit code and output.",
            "jvm": (
                "`mvn test` runs without an application server: no Arquillian container, no CDI/EJB injection, no Bean "
                "Validation provider unless declared. Test plain classes with plain JUnit; for the live application use "
                'java.net.http.HttpClient with System.getenv("BASE_URL"). Call only constructors and methods that '
                "appear verbatim in target_source or collaborator_signatures; a class you cannot see is a class you must not "
                "instantiate. If a case cannot be tested without a container, test the nearest plain-Java behavior instead."
            ),
            "python": "Never nest more than 5 `with` blocks (CPython allows 20): use @patch decorators or contextlib.ExitStack.",
            "ui/e2e/accessibility": (
                "Only with live_app.available and only the levels in allowed_test_levels. A Streamlit UI is tested in-process "
                "with `streamlit.testing.v1.AppTest` (never by fetching the server's HTML: it is an empty JavaScript shell); "
                "For a Node frontend plan TypeScript @playwright/test; its installation and browser runtime are verified only during execution setup; use BASE_URL and real rendered selectors. Capture screenshots and retain traces. Derive selectors from rendered source, including shared page-header and navigation components. Assert fixture prerequisites and each expected page/detail unconditionally: do not silently skip assertions with if(count>0). Seed disposable data using the actual backend schema and isolated application database before detail checks. For Python browser cases require a source-supported Playwright dependency and planned browser setup; never claim it is installed before execution."
            ),
            "security": "Assert rejection/sanitization paths that the source shows.",
        },
        "framework_selection_rule": "Prefer detected test framework; otherwise choose a mainstream native framework compatible with the dominant stack.",
        "execution_policy": "Tests are executed by the host after human approval; write them to run unattended and deterministically.",
    }


def friendly_error(exc: BaseException) -> str:
    """A readable message for provider failures; everything else keeps its own text."""
    text = str(exc)
    status = re.search(r"Error code: (\d{3})", text)
    detail = re.search(r"'message': '([^']+)'", text)
    if status and status.group(1) == "429":
        return (
            f"The model gateway refused the request (HTTP 429): {detail.group(1) if detail else 'rate or quota limit'}. "
            "This is the provider's limit, not the harness's daily cap; retrying will not help until it resets."
        )
    if status:
        return (
            f"The model gateway returned HTTP {status.group(1)}: {detail.group(1) if detail else text[:200]}"
        )
    return text

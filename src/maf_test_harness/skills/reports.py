"""QA outputs shaped by the vendored skills' contracts and checked by the skills' own validators.

* ``traceability-generator``: requirement -> test -> generated artifact matrix, validated by its reviewed
  ``validate_traceability.py`` (run by the host through the hash-checked script runner);
* ``coverage-analyzer``: coverage/gap summary validated against the JSON Schema in its ``coverage_templates.yaml``.

Nothing here infers links: it only re-shapes the IDs and links the approved design already contains.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from ..qa.report import design_cases
from .scripts import HarnessScriptRunner, ScriptRefused


def _requirement_ids(requirements: dict[str, Any]) -> list[str]:
    return [r["id"] for r in requirements.get("requirement_model", {}).get("requirements", [])]


def build_traceability(
    requirements: dict[str, Any], design: dict[str, Any], generation: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(traceability-generator output, its validation source) from the approved design and generated files."""
    model = requirements.get("requirement_model", {})
    criterion_parent = {c["id"]: c["requirement_id"] for c in model.get("acceptance_criteria", [])}
    file_for_case = {f["case_ids"][0]: f["path"] for f in generation.get("files", [])}
    matrix, orphan_tests, seen = [], [], set()
    for case in design_cases(design):
        linked = {
            link["id"] if link["type"] == "requirement" else criterion_parent.get(link["id"])
            for link in case.get("traceability_links", [])
            if link["type"] in {"requirement", "acceptance_criterion"}
        } - {None}
        if not linked:
            orphan_tests.append(case["id"])
        for requirement_id in sorted(linked):
            if (requirement_id, case["id"]) in seen:
                continue
            seen.add((requirement_id, case["id"]))
            path = file_for_case.get(case["id"])
            matrix.append(
                {
                    "requirement_id": requirement_id,
                    "test_case_id": case["id"],
                    "artifact_id": path,
                    "artifact_type": "generated_test" if path else "unknown",
                    "status": "covered" if path else "missing_artifact",
                }
            )
    mapped = {row["requirement_id"] for row in matrix}
    report = {
        "metadata": {
            "generated_at": datetime.now(UTC).isoformat(),
            "generator_version": "1.1",
            "source_skill_names": ["test-design-generator", "generic-test-generator"],
        },
        "traceability_matrix": matrix,
        "gaps_report": {
            "uncovered_requirements": [r for r in _requirement_ids(requirements) if r not in mapped],
            "orphan_tests": orphan_tests,
            "missing_links": [],
            "warnings": [],
            "notes": "Links are the approved design's traceability_links; none were inferred.",
        },
    }
    source = {
        "requirements": [{"id": r} for r in _requirement_ids(requirements)],
        "test_cases": [{"id": c["id"]} for c in design_cases(design)],
        "generated_artifacts": [
            {"id": f["path"], "type": "generated_test", "test_case_id": f["case_ids"][0]}
            for f in generation.get("files", [])
        ],
    }
    return report, source


def build_coverage(requirements: dict[str, Any], design: dict[str, Any]) -> dict[str, Any]:
    """coverage-analyzer summary from the design's own coverage analysis (explicit links only)."""
    model = requirements.get("requirement_model", {})
    matrix = design.get("traceability_matrix", {})
    by_type = {
        "requirement": (model.get("requirements", []), matrix.get("requirement_to_tests", {})),
        "acceptance_criterion": (
            model.get("acceptance_criteria", []),
            matrix.get("requirement_to_tests", {}),
        ),
        "validation": (model.get("validation_rules", []), matrix.get("validation_to_tests", {})),
        "api_contract": (requirements.get("api_contracts", []), matrix.get("api_to_tests", {})),
    }
    details = [
        {
            "source_id": item["id"],
            "source_type": kind,
            "covered": bool(links.get(item["id"])),
            "test_case_ids": list(links.get(item["id"], [])),
        }
        for kind, (items, links) in by_type.items()
        for item in items
    ]

    def metric(kind: str) -> dict[str, Any]:
        rows = [d for d in details if d["source_type"] == kind]
        covered = sum(d["covered"] for d in rows)
        return {
            "total": len(rows),
            "covered": covered,
            "percentage": round(100 * covered / len(rows), 2) if rows else 0,
        }

    def uncovered(kind: str) -> list[str]:
        return [d["source_id"] for d in details if d["source_type"] == kind and not d["covered"]]

    known = {c["id"] for c in design_cases(design)}
    linked = {t for d in details for t in d["test_case_ids"]}
    return {
        "summary": {
            "requirements_total": metric("requirement")["total"],
            "requirements_covered": metric("requirement")["covered"],
            "validations_total": metric("validation")["total"],
            "validations_covered": metric("validation")["covered"],
            "api_contracts_total": metric("api_contract")["total"],
            "api_contracts_covered": metric("api_contract")["covered"],
            "traceable_items": len(details),
        },
        "coverage_report": {
            "acceptance_criteria_coverage": metric("acceptance_criterion"),
            "validation_coverage": metric("validation"),
            "api_coverage": metric("api_contract"),
        },
        "coverage_details": details,
        "gaps_report": {
            "uncovered_requirements": uncovered("requirement"),
            "uncovered_validations": uncovered("validation"),
            "uncovered_api_contracts": uncovered("api_contract"),
            "orphaned_linkages": [],
            "unlinked_test_cases": sorted(known - linked),
            "notes": "Design-level coverage from explicit traceability links; no coverage was inferred."
            + (
                ""
                if requirements.get("api_contracts")
                else " No API contracts were supplied; API coverage is not assessed."
            ),
        },
    }


def run_skill_checks(
    skills_root: Path, requirements: dict[str, Any], design: dict[str, Any], generation: dict[str, Any]
) -> dict[str, Any]:
    """Produce both reports and validate them with the skills' own contract/validator. Never raises."""
    runner = HarnessScriptRunner(skills_root, tiers=("host", "model"))
    traceability, source = build_traceability(requirements, design, generation)
    coverage = build_coverage(requirements, design)
    checks: dict[str, Any] = {}
    try:
        outcome = runner.run(
            "traceability-generator",
            "scripts/validate_traceability.py",
            ["input/traceability.json", "--source", "input/source.json"],
            files={"traceability.json": json.dumps(traceability), "source.json": json.dumps(source)},
        )
        checks["traceability"] = {
            "skill": "traceability-generator",
            "script": "scripts/validate_traceability.py",
            "sha256": outcome["sha256"],
            "passed": outcome["exit_code"] == 0,
            "output": (outcome["stdout"] + outcome["stderr"]).strip()[:600],
        }
    except (ScriptRefused, OSError) as exc:
        checks["traceability"] = {"skill": "traceability-generator", "passed": False, "output": str(exc)}
    try:
        schema = yaml.safe_load(
            (Path(skills_root) / "coverage-analyzer" / "assets" / "coverage_templates.yaml").read_text(
                encoding="utf-8"
            )
        )["output_contract"]
        jsonschema.validate(coverage, schema)
        checks["coverage"] = {
            "skill": "coverage-analyzer",
            "contract": "assets/coverage_templates.yaml",
            "passed": True,
            "output": "valid",
        }
    except (jsonschema.ValidationError, OSError, KeyError, yaml.YAMLError) as exc:
        checks["coverage"] = {"skill": "coverage-analyzer", "passed": False, "output": str(exc)[:300]}
    return {"checks": checks, "traceability": traceability, "coverage": coverage}

#!/usr/bin/env python3
"""Validate the output contract of traceability-generator."""
import json
import sys
from pathlib import Path

REQUIRED_ENTRY_FIELDS = {"requirement_id", "test_case_id", "artifact_id", "artifact_type", "status"}
REQUIRED_GAP_FIELDS = {"uncovered_requirements", "orphan_tests", "missing_links", "warnings", "notes"}


def validate(payload, source=None):
    errors = []
    if not isinstance(payload, dict):
        return ["Root value must be an object."]
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("metadata must be an object.")
    elif not {"generated_at", "generator_version", "source_skill_names"}.issubset(metadata):
        errors.append("metadata must include generated_at, generator_version, and source_skill_names.")
    matrix = payload.get("traceability_matrix")
    if not isinstance(matrix, list):
        errors.append("traceability_matrix must be an array.")
        matrix = []
    for index, entry in enumerate(matrix):
        if not isinstance(entry, dict):
            errors.append(f"traceability_matrix[{index}] must be an object.")
            continue
        missing = REQUIRED_ENTRY_FIELDS - entry.keys()
        if missing:
            errors.append(f"traceability_matrix[{index}] missing: {', '.join(sorted(missing))}.")
    gaps = payload.get("gaps_report")
    if not isinstance(gaps, dict):
        errors.append("gaps_report must be a non-null object.")
    else:
        missing = REQUIRED_GAP_FIELDS - gaps.keys()
        if missing:
            errors.append(f"gaps_report missing: {', '.join(sorted(missing))}.")
        for key in REQUIRED_GAP_FIELDS - {"notes"}:
            if key in gaps and not isinstance(gaps[key], list):
                errors.append(f"gaps_report.{key} must be an array.")
    if source is not None and isinstance(source, dict) and isinstance(gaps, dict):
        requirement_ids = {item.get("id") for item in source.get("requirements", []) if isinstance(item, dict) and item.get("id")}
        test_ids = {item.get("id") for item in source.get("test_cases", []) if isinstance(item, dict) and item.get("id")}
        artifact_ids = {item.get("id") for item in source.get("generated_artifacts", []) if isinstance(item, dict) and item.get("id")}
        mapped_requirements = {entry.get("requirement_id") for entry in matrix if isinstance(entry, dict)}
        mapped_tests = {entry.get("test_case_id") for entry in matrix if isinstance(entry, dict)}
        uncovered = set(gaps.get("uncovered_requirements", []))
        orphaned = set(gaps.get("orphan_tests", []))
        for requirement_id in requirement_ids:
            if requirement_id not in mapped_requirements and requirement_id not in uncovered:
                errors.append(f"Requirement {requirement_id} is neither mapped nor reported as uncovered.")
        for test_id in test_ids:
            if test_id not in mapped_tests and test_id not in orphaned:
                errors.append(f"Test {test_id} is neither mapped nor reported as an orphan.")
        for entry in matrix:
            if isinstance(entry, dict) and entry.get("artifact_id") is not None and entry["artifact_id"] not in artifact_ids:
                errors.append(f"Artifact {entry['artifact_id']} does not resolve in source.generated_artifacts.")
    return errors


def main():
    arguments = sys.argv[1:]
    if len(arguments) not in (1, 3) or (len(arguments) == 3 and arguments[1] != "--source"):
        print("Usage: validate_traceability.py <traceability-matrix.json> [--source <input.json>]", file=sys.stderr)
        return 2
    try:
        payload = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
        source = json.loads(Path(arguments[2]).read_text(encoding="utf-8")) if len(arguments) == 3 else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2
    errors = validate(payload, source)
    if errors:
        print("Traceability validation failed:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1
    print("Traceability validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

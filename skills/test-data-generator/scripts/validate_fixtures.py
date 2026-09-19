#!/usr/bin/env python3
"""Validate generated fixture output against the bundled JSON Schema contract."""

import json
import sys
from pathlib import Path

try:
    import jsonschema
    import yaml
except ImportError as exc:  # pragma: no cover - CLI feedback path
    raise SystemExit(
        f"Required validator dependency is missing: {exc}. "
        "Install dependencies with: pip install -r requirements.txt"
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python validate_fixtures.py <fixture-output.json>")
        return 2

    fixture_path = Path(sys.argv[1])
    if not fixture_path.is_file():
        print(f"File not found: {fixture_path}")
        return 2

    template_path = Path(__file__).resolve().parents[1] / "assets" / "fixture_templates.yaml"
    try:
        fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Invalid JSON: {exc}")
        return 1

    try:
        template = yaml.safe_load(template_path.read_text(encoding="utf-8"))
        schema = template["templates"]["fixture_model"]
        jsonschema.Draft7Validator.check_schema(schema)
    except (OSError, KeyError, TypeError, yaml.YAMLError, jsonschema.exceptions.SchemaError) as exc:
        print(f"Invalid fixture schema: {exc}")
        return 1

    errors = sorted(
        jsonschema.Draft7Validator(schema).iter_errors(fixture_data),
        key=lambda error: list(error.path),
    )
    if errors:
        print("VALIDATION FAILED")
        for error in errors:
            path = "/".join(str(part) for part in error.absolute_path) or "root"
            print(f"- {path}: {error.message}")
        return 1

    print("VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

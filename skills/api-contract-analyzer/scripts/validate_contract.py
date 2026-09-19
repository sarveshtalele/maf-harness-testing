import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install PyYAML")
    sys.exit(1)

try:
    from jsonschema import validate, ValidationError, Draft7Validator
except ImportError:
    print("ERROR: jsonschema is required. Install with: pip install jsonschema")
    sys.exit(1)


def load_schema_templates(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("templates", {}).get("api_contract_report", {})


def validate_contract(data: dict, schema: dict) -> list:
    errors = []
    validator = Draft7Validator(schema)
    for err in validator.iter_errors(data):
        errors.append(f"  – {'.'.join(str(p) for p in err.path)}: {err.message}")
    return errors


def check_semantic(data: dict) -> list:
    errors = []
    contracts = data.get("api_contracts", [])
    for op in contracts:
        op_id = op.get("operationId", "<unknown>")
        status_codes = op.get("status_codes", [])
        response_schemas = op.get("response_schemas", {})
        for code in status_codes:
            if code not in response_schemas:
                errors.append(
                    f"  – operation '{op_id}': status code {code} missing from response_schemas"
                )
        missing = data.get("gaps_report", {}).get("missing_schemas", [])
        incomplete = data.get("gaps_report", {}).get("incomplete_endpoints", [])
        if op_id in incomplete:
            continue
        if not status_codes and op_id not in [m.get("operationId") for m in missing]:
            errors.append(f"  – operation '{op_id}': no status_codes and not recorded in gaps_report")
    return errors


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_contract.py <contract.json> [--schema schema_templates.yaml]")
        sys.exit(1)

    contract_path = Path(sys.argv[1])
    schema_path = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--schema" else Path(__file__).parent.parent / "assets" / "schema_templates.yaml"

    if not contract_path.exists():
        print(f"ERROR: Contract file not found: {contract_path}")
        sys.exit(1)
    if not schema_path.exists():
        print(f"ERROR: Schema template file not found: {schema_path}")
        sys.exit(1)

    with open(contract_path, "r", encoding="utf-8") as f:
        contract = json.load(f)

    schema = load_schema_templates(schema_path)
    if not schema:
        print(f"ERROR: Could not load 'api_contract_report' schema from {schema_path}")
        sys.exit(1)

    print(f"Validating {contract_path} against schema ...")
    schema_errors = validate_contract(contract, schema)
    if schema_errors:
        print(f"\nSchema validation failed ({len(schema_errors)} error(s)):")
        for e in schema_errors:
            print(e)
    else:
        print("Schema validation: PASSED")

    semantic_errors = check_semantic(contract)
    if semantic_errors:
        print(f"\nSemantic check failed ({len(semantic_errors)} error(s)):")
        for e in semantic_errors:
            print(e)
    else:
        print("Semantic check: PASSED")

    if schema_errors or semantic_errors:
        sys.exit(1)
    print("\nContract report is valid.")


if __name__ == "__main__":
    main()

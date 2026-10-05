"""Reviewed host-only requirement-analysis entry point; JSON request/output, no model calls."""

import argparse
import json
from pathlib import Path
from maf_test_harness.repo.requirements import requirements_from_facts

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    result = requirements_from_facts(request["analysis"], request["app_name"], request.get("knowledge", ""))
    args.output.write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps({"skill": "requirement-analysis", "model_calls": 0, "status": "completed"}))

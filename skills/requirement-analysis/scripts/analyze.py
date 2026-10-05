"""Host-only requirements adapter over reviewed static facts."""
import argparse
import json
from pathlib import Path
from maf_test_harness.repo.requirements import requirements_from_facts

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--app-name", required=True)
    args = parser.parse_args()
    print(json.dumps(requirements_from_facts(json.loads(args.analysis.read_text()), args.app_name), indent=2))

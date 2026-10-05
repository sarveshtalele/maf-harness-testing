"""Host-only deterministic analyzer; no target code execution."""
import argparse
import json
from pathlib import Path
from maf_test_harness.repo.reverse_engineering import reverse_engineer

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--source-files", type=Path, required=True, help="JSON array from host repository inventory")
    args = parser.parse_args()
    print(json.dumps(reverse_engineer(args.root, json.loads(args.source_files.read_text())), indent=2))

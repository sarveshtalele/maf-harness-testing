import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("benchmark_seeded", ROOT / "scripts" / "benchmark_seeded.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_apply_replaces_exactly_one_occurrence(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    bench.apply(tmp_path, {"id": "M", "file": "m.py", "find": "x = 1", "replace": "x = 2"})
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == "x = 2\n"
    (tmp_path / "d.py").write_text("a\na\n", encoding="utf-8")
    with pytest.raises(ValueError, match="2 times"):
        bench.apply(tmp_path, {"id": "M", "file": "d.py", "find": "a", "replace": "b"})


def test_seeded_specs_name_distinct_mutants():
    for path in (ROOT / "benchmarks" / "seeded").glob("*.json"):
        mutants = json.loads(path.read_text(encoding="utf-8"))["mutants"]
        assert len({m["id"] for m in mutants}) == len(mutants)
        assert all(m["find"] and m["find"] != m["replace"] and m["bug"] for m in mutants)

"""Read-only TokenTelemetry investigation; all database writes use a temporary directory.

Run: .venv/bin/python docs/audit/token_failure_repro.py /path/to/saved/run/workspace
"""

import ast
import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from maf_test_harness.agent.runtime import ToolTraceMiddleware
from maf_test_harness.tools.registry import ToolResult


def main(workspace: Path) -> None:
    source = ast.parse((workspace / "telemetry/db.py").read_text())
    schema = next(
        ast.literal_eval(n.value)
        for n in source.body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SCHEMA" for t in n.targets)
    )
    with tempfile.TemporaryDirectory(prefix="maf-fixture-repro-") as directory:
        path = Path(directory) / "test.db"
        first = sqlite3.connect(path, timeout=0.1)
        first.executescript(schema)
        first.execute("INSERT INTO usage(transcript_path,transcript_line) VALUES(?,?)", ("fixture.jsonl", 1))
        try:
            first.execute(
                "INSERT INTO usage(transcript_path,transcript_line) VALUES(?,?)", ("fixture.jsonl", 1)
            )
        except sqlite3.IntegrityError:
            print("Duplicate fixture key reproduced")
        second = sqlite3.connect(path, timeout=0.1)
        try:
            second.execute(
                "INSERT INTO usage(transcript_path,transcript_line) VALUES(?,?)", ("other.jsonl", 2)
            )
        except sqlite3.OperationalError as exc:
            assert "locked" in str(exc)
            print("Unclosed failed transaction causes subsequent database lock")
        finally:
            second.close()
            first.close()
        with sqlite3.connect(path) as conn:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(attributions)")}
            assert "reference_count" not in columns
            print("reference_count is not an attributions column")

    common = ast.parse((workspace / "telemetry/common.py").read_text())
    definitions = [
        n
        for n in common.body
        if isinstance(n, ast.FunctionDef)
        and n.name in {"first", "extract_usage"}
        or isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "TOKEN_FIELDS" for t in n.targets)
    ]
    namespace = {}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), "<source-contract>", "exec"), namespace)
    usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 42}
    wrong = {"message": {"role": "assistant"}, "usage": usage}
    correct = {"message": {"role": "assistant", "usage": usage}}
    assert namespace["extract_usage"](wrong)["total_tokens"] == 0
    assert namespace["extract_usage"](correct)["total_tokens"] == 15
    print("Generated transcript uses the wrong usage nesting and token total")

    async def tool_failure():
        trace = ToolTraceMiddleware()
        context = SimpleNamespace(
            arguments={}, function=SimpleNamespace(name="diagnose_env_error"), result=None
        )

        async def call_next():
            context.result = json.dumps({"ok": False, "code": "REPRO_FAILURE"})

        await trace.process(context, call_next)
        assert trace.calls[-1]["ok"] is False
        print("Typed failure is correctly recorded unsuccessful")

    asyncio.run(tool_failure())
    oversized = ToolResult(False, evidence="x" * 7000, suggested_fixes=["y" * 7000]).to_json()
    assert json.loads(oversized)["ok"] is False
    print("Bounded tool envelope remains valid JSON")


if __name__ == "__main__":
    main(Path(sys.argv[1]))

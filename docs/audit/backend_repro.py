import json
import sys
import tempfile
from pathlib import Path

from maf_test_harness.envs.app_launcher import detect_launch
from maf_test_harness.envs.environment import build_environment_plan, documented_packages
from maf_test_harness.envs.runners import RunPlan, Step, build_plan, execute_plan, map_results_to_cases
from maf_test_harness.envs.spec import detect_spec, specifier_allows
from maf_test_harness.envs.toolchains import satisfies
from maf_test_harness.qa.packaging import run_instructions
from maf_test_harness.qa.report import build_qa_report
from maf_test_harness.repo.stack import StackProfile, detect_stack
from maf_test_harness.skills.contracts import finalize_discovered_requirements

root = Path(".targets/tokentelemetry-inspection").resolve()
with tempfile.TemporaryDirectory() as folder:
    ws = Path(folder) / "workspace"
    ws.mkdir()
    out = Path(folder) / "run"
    code = "from pathlib import Path; Path('.harness-reports/junit.xml').write_text('<testsuite><testcase name=\"test_ok\" classname=\"test_one\"/></testsuite>'); raise SystemExit(1)"
    result = execute_plan(
        RunPlan(
            "audit",
            [
                Step(
                    "nonzero-with-report",
                    [sys.executable, "-c", code],
                    parser="junit",
                    report_glob=".harness-reports/junit.xml",
                )
            ],
        ),
        ws,
        out,
    )
    print(
        "NONZERO_COMMAND:",
        json.dumps(
            {
                "exit": result["steps"][0]["exit_code"],
                "status": result["status"],
                "summary": result["summary"],
            }
        ),
    )
    files = [
        {"path": "test_first.py", "case_ids": ["CASE-A"], "content": "def test_shared(): pass"},
        {"path": "test_second.py", "case_ids": ["CASE-B"], "content": "def test_shared(): pass"},
    ]
    mapped = map_results_to_cases(
        [
            {"name": "test_shared", "classname": "test_first", "status": "failed"},
            {"name": "test_shared", "classname": "test_second", "status": "passed"},
        ],
        files,
    )
    print("DUPLICATE_NAME_MAPPING:", json.dumps(mapped))
    q = build_qa_report(
        repo={},
        requirements={},
        design={"positive_tests": [{"id": "CASE-A", "title": "First"}, {"id": "CASE-B", "title": "Second"}]},
        generation={"files": files},
        execution={
            "status": "passed",
            "cases": [{"name": "test_shared", "classname": "test_second", "status": "passed"}],
        },
    )
    print("INCOMPLETE_QA:", json.dumps({"verdict": q["verdict"], "counts": q["case_status_counts"]}))
    stack = detect_stack(root)
    env = build_environment_plan(stack, root, ws / "cache")
    launch = detect_launch(root, 54321)
    print("TARGET_STACK:", json.dumps(stack.to_dict()))
    print(
        "TARGET_INSTALL:",
        json.dumps({"ecosystem": env["ecosystem"], "commands": [s["argv"] for s in env["steps"]]}),
    )
    print("TARGET_LAUNCH:", json.dumps(launch))
    mixed = [
        {"path": "test_backend.py", "content": "def test_api(): pass"},
        {"path": "ui.spec.ts", "content": "import {test} from '@playwright/test'; test('UI',async()=>{});"},
    ]
    plan = build_plan(
        stack,
        ws,
        mixed,
        cache_dir=ws / "cache",
        env_ready=True,
        assume_present=frozenset({"python3", "node", "npm"}),
    )
    print("MIXED_PLAN:", json.dumps({"adapter": plan.adapter, "commands": [s.argv for s in plan.steps]}))
    pw = build_plan(
        StackProfile(primary="node", test_runner="playwright"),
        ws,
        [mixed[1]],
        cache_dir=ws / "cache",
        env_ready=True,
        assume_present=frozenset({"node", "npm"}),
    )
    print("PLAYWRIGHT_PLAN:", json.dumps(pw.to_dict()))
    print("PYTHON_UPPER_BOUND:", specifier_allows(">=3.10,<3.13", "3.13"))
    print(
        "RERUN_INSTRUCTIONS:",
        run_instructions(
            {
                "execution_plan": {
                    "plan": {
                        "steps": [
                            {
                                "kind": "test",
                                "argv": ["/old-machine/.harness-venv/bin/python", "-m", "pytest"],
                                "cwd": "backend",
                                "env": {"BASE_URL": "http://127.0.0.1:54321"},
                            }
                        ]
                    }
                }
            }
        ),
    )

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder) / "repo"
    root.mkdir()
    (Path(folder) / "outside.txt").write_text("pytest")
    print(
        "OUTSIDE_EVIDENCE:",
        documented_packages(
            [
                {
                    "kind": "dependency",
                    "how_to_install": "pip install pytest",
                    "evidence_file": "../outside.txt",
                }
            ],
            root,
        ),
    )
    (root / "pyproject.toml").write_text('[project]\nname="demo"\nrequires-python=">=3.10,<3.12"\n')
    requirement = detect_spec(root, "python").requirements[0]
    print(
        "CONSTRAINT_LOSS:",
        {
            "declared": ">=3.10,<3.12",
            "stored_minimum": requirement.version,
            "accepts_3_13": satisfies("python", "3.13", requirement.version),
            "spec_accepts_3_13": specifier_allows(">=3.10,<3.12", "3.13"),
        },
    )
    draft = {
        "app_name": "Demo",
        "requirements": [
            {"text": "Does a thing", "evidence": "invented evidence", "source_files": ["nonexistent.py"]}
        ],
    }
    result = finalize_discovered_requirements(draft, "https://github.com/acme/demo")
    print("MISSING_SOURCE_ACCEPTED:", result["requirement_model"]["requirements"][0]["source_files"])

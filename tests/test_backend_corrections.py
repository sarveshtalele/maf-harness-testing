"""Regression proofs for approved backend audit findings."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from maf_test_harness.agent.runtime import ToolTraceMiddleware
from maf_test_harness.envs import runners
from maf_test_harness.envs.environment import documented_packages
from maf_test_harness.envs.spec import python_requirement
from maf_test_harness.envs.toolchains import satisfies
from maf_test_harness.qa.report import build_qa_report
from maf_test_harness.tools.registry import ToolResult


def test_failed_process_with_passing_xml_cannot_pass(tmp_path):
    script = "from pathlib import Path; import sys; Path('.harness-reports/j.xml').write_text('<testsuite><testcase name=\"test_x\"/></testsuite>'); sys.exit(1)"
    plan = runners.RunPlan(
        "python-pytest",
        [
            runners.Step(
                "test", [sys.executable, "-c", script], parser="junit", report_glob=".harness-reports/j.xml"
            )
        ],
    )
    result = runners.execute_plan(plan, tmp_path, tmp_path / "result")
    assert result["summary"]["passed"] == 1 and result["status"] == "failed"


def test_identity_aggregation_and_incomplete_qa():
    files = [
        {"path": f"tests/{n}.py", "content": "def test_shared(): pass", "case_ids": [n]} for n in ["a", "b"]
    ]
    results = [
        {"name": "test_shared[param]", "classname": "tests.a", "status": "failed", "message": "failure"},
        {"name": "test_shared[other]", "classname": "tests.a", "status": "passed", "message": ""},
    ]
    mapped = runners.map_results_to_cases(results, files)
    assert all(r["case_id"] == "a" for r in mapped)
    assert (
        runners.map_results_to_cases([{"name": "test_shared", "status": "passed"}], files)[0]["case_id"]
        is None
    )
    design = {"positive_tests": [{"id": n, "title": n} for n in ["a", "b"]]}
    qa = build_qa_report(
        repo={},
        requirements={},
        design=design,
        generation={"files": files},
        execution={"status": "passed", "cases": results},
    )
    assert qa["cases"][0]["status"] == "failed" and qa["verdict"] == "FAIL"
    qa = build_qa_report(
        repo={},
        requirements={},
        design=design,
        generation={"files": files},
        execution={"status": "passed", "cases": [results[1]]},
    )
    assert qa["verdict"] == "INCONCLUSIVE"


def test_tool_failure_and_bounded_json():
    trace = ToolTraceMiddleware()
    context = SimpleNamespace(arguments={}, function=SimpleNamespace(name="probe"), result=None)

    async def next_call():
        context.result = json.dumps({"ok": False, "code": "FAILED"})

    asyncio.run(trace.process(context, next_call))
    assert trace.calls[0]["ok"] is False and trace.calls[0]["code"] == "FAILED"
    text = ToolResult(False, evidence="💥" * 7000, suggested_fixes=["x" * 7000]).to_json()
    assert len(text) <= 6000 and json.loads(text)["ok"] is False


def test_evidence_containment_and_python_bounds(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("pytest")
    assert not documented_packages(
        [{"kind": "test_tool", "how_to_install": "pip install pytest", "evidence_file": "../outside.txt"}],
        root,
    )
    (root / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10,<3.12,!=3.11.*"')
    req = python_requirement(root)
    assert req is not None and req.specifier == ">=3.10,<3.12,!=3.11.*"
    assert not satisfies("python", "3.13.0", req.version, specifier=req.specifier)
    assert not satisfies("python", "3.11.1", req.version, specifier=req.specifier)
    assert satisfies("python", "3.10.9", req.version, specifier=req.specifier)


def test_component_plans_and_explicit_launch(tmp_path):
    from maf_test_harness.envs.app_launcher import detect_launch
    from maf_test_harness.repo.stack import detect_stack

    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\npytest\n")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/main.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/health")\ndef health(): return {"ok": True}\n'
    )
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/package.json").write_text('{"scripts":{"dev":"vite"},"dependencies":{"vite":"1"}}')
    (tmp_path / "frontend/vite.config.ts").write_text(
        'export default {proxy: {"/api": "http://localhost:8000"}}'
    )
    assert runners.repository_components(detect_stack(tmp_path), tmp_path) == [
        ("python", "."),
        ("node", "frontend"),
    ]
    spec = detect_launch(tmp_path, 5001, "frontend")
    assert len(spec["services"]) == 2 and spec["services"][0]["ready_path"] == "/health"
    assert spec["services"][1]["cwd"] == "frontend" and "--strictPort" in spec["services"][1]["argv"]


def test_state_contracts_and_packaged_evidence(tmp_path):
    import zipfile

    import pytest

    from maf_test_harness.qa.packaging import RERUN, build_deliverable
    from maf_test_harness.tools.code_intel import RepoContext, check_stateful_fixtures

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "db.py").write_text('SCHEMA = "CREATE TABLE attributions (id INTEGER, usage_id INTEGER);"')
    ctx = RepoContext(repo, ["db.py"])
    with pytest.raises(ValueError, match="nonexistent"):
        check_stateful_fixtures(
            ctx, "test_x.py", 'query = "INSERT INTO attributions (reference_count) VALUES (1)"'
        )
    with pytest.raises(ValueError, match="import-time"):
        check_stateful_fixtures(ctx, "test_x.py", 'import os\nos.environ["DB"]="shared.db"')
    folder = tmp_path / "browser-reports/playwright-report/data"
    folder.mkdir(parents=True)
    (folder / "screenshot.png").write_bytes(b"png")
    (folder.parent / "index.html").write_text("<h1>Report</h1>")
    state = {
        "artifacts": {"repo_analysis": {"repo_name": "demo"}},
        "execution_plan": {
            "plan": {
                "steps": [
                    {
                        "kind": "test",
                        "argv": ["python", "test_x.py"],
                        "cwd": ".",
                        "env": {"BASE_URL": "http://localhost:5001"},
                    }
                ]
            }
        },
    }
    target = build_deliverable(tmp_path, state)
    with zipfile.ZipFile(target) as archive:
        assert (
            "harness-report/evidence/browser-reports/playwright-report/data/screenshot.png"
            in archive.namelist()
        )
        assert json.loads(archive.read("harness-report/rerun-plan.json"))["test"][0]["env"]["BASE_URL"]
    compile(RERUN, "rerun.py", "exec")


def test_extracted_deliverable_runs_with_cwd_and_environment(tmp_path):
    import subprocess
    import zipfile

    from maf_test_harness.qa.packaging import build_deliverable

    (tmp_path / "repo/component").mkdir(parents=True)
    source = 'import os\nfrom pathlib import Path\nassert os.environ["EXPECTED"] == "fixture"\nassert "OPENAI_API_KEY" not in os.environ\nPath("proof.txt").write_text("ok")\n'
    state = {
        "artifacts": {
            "repo_analysis": {"repo_name": "demo"},
            "test_generation": {"files": [{"path": "component/check.py", "content": source}]},
        },
        "execution_plan": {
            "plan": {
                "steps": [
                    {
                        "kind": "test",
                        "argv": [sys.executable, str(tmp_path / "workspace/component/check.py")],
                        "cwd": "component",
                        "env": {"EXPECTED": "fixture"},
                    }
                ]
            }
        },
    }
    archive_path = build_deliverable(tmp_path, state)
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extracted)
    subprocess.run([sys.executable, str(extracted / "harness-report/rerun.py")], cwd=extracted, check=True)
    assert (extracted / "demo/component/proof.txt").read_text() == "ok"


def test_java_validation_requires_a_provider_and_compatible_fallback(tmp_path):
    from maf_test_harness.envs import toolchains
    from maf_test_harness.repo.stack import detect_stack

    (tmp_path / "pom.xml").write_text(
        "<project><properties><maven.compiler.source>8</maven.compiler.source></properties><dependencies><dependency><groupId>javax.validation</groupId><artifactId>validation-api</artifactId></dependency></dependencies></project>"
    )
    files = [
        {
            "path": "src/test/java/ValidationTest.java",
            "content": "class ValidationTest { void test(){ Validation.buildDefaultValidatorFactory(); } }",
            "case_ids": ["a"],
        }
    ]
    plan = runners.build_plan(
        detect_stack(tmp_path),
        tmp_path,
        files,
        cache_dir=tmp_path / "cache",
        assume_present=frozenset({"java", "mvn"}),
    )
    assert plan.blocked_reason and "provider" in plan.blocked_reason.lower()
    version = toolchains.resolve_download(
        "jdk",
        "8",
        fetch=lambda url: (
            b"[]"
            if "/8/" in url
            else json.dumps(
                [
                    {
                        "version": {"semver": "11.0.2"},
                        "binary": {
                            "package": {
                                "link": "https://example.com/jdk.tar.gz",
                                "checksum": "a" * 64,
                                "name": "jdk.tar.gz",
                            }
                        },
                    }
                ]
            ).encode()
        ),
        fallback_version="11",
    )
    assert version.version == "11.0.2" and "compatible JDK 11" in version.source


def test_browser_generation_receives_shared_source_and_rejects_wrong_component(tmp_path, monkeypatch):
    import pytest

    import maf_test_harness.workflow as workflow

    host = workflow.Harness(tmp_path / "run", skills_root=Path("skills"))
    host.workspace.mkdir(parents=True)
    (host.workspace / "frontend").mkdir()
    (host.workspace / "frontend/package.json").write_text('{"dependencies":{"react":"*"}}')
    paths = ["frontend/src/components/PageHead.tsx", "frontend/src/pages/Requests.tsx"]
    for path in paths:
        target = host.run_dir / "repo" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export default function PageHead(){return <div>Requests</div>}")
    host.state.update({"app_component": "frontend", "artifacts": {"repo_analysis": {"source_files": paths}}})
    source = host._selected_app_source(host.state, preferred=[paths[1]], max_chars=96000)
    assert set(source) == set(paths) and "<div>Requests</div>" in source[paths[0]]
    monkeypatch.setattr(
        __import__("maf_test_harness.workflow_steps.generation", fromlist=["static_gate"]),
        "static_gate",
        lambda *args: None,
    )
    candidate = {
        "files": [{"path": "tests/browser.spec.ts", "content": "import {test} from '@playwright/test';"}]
    }
    with pytest.raises(ValueError, match="outside its package.json component"):
        host._check_imports(candidate, host.state)
    candidate["files"][0]["path"] = "frontend/tests/browser.spec.ts"
    host._check_imports(candidate, host.state)


def test_nested_playwright_junit_filename_maps_without_guessing_duplicate_names():
    report = [{"name": "Suite › visible journey", "classname": "tests/browser.spec.ts", "status": "passed"}]
    file = {"path": "frontend/tests/browser.spec.ts", "content": "", "case_ids": ["browser"]}
    assert runners.map_results_to_cases(report, [file])[0]["case_id"] == "browser"
    duplicate = {**file, "path": "other/tests/browser.spec.ts", "case_ids": ["other"]}
    result = runners.map_results_to_cases(report, [file, duplicate])[0]
    assert result["case_id"] is None and result["mapping_error"] == "ambiguous test identity"


def test_windows_export_paths_are_portable_on_a_mac_host():
    from pathlib import PureWindowsPath

    from maf_test_harness.qa.packaging import portable_plan

    run = PureWindowsPath("C:/runs/example")
    state = {
        "execution_plan": {
            "plan": {
                "steps": [
                    {
                        "argv": ["C:\\tools\\npm.cmd", str(run / "workspace/frontend/test.js")],
                        "env": {"CLAUDE_TELEMETRY_DB": str(run / "workspace/data.db")},
                    }
                ]
            }
        }
    }
    step = portable_plan(state, run)["test"][0]
    assert step["argv"] == ["npm.cmd", "{REPO}/frontend/test.js"]
    assert step["env"]["CLAUDE_TELEMETRY_DB"] == "{REPO}/data.db"


def test_jsonl_evidence_is_redacted_like_other_text(tmp_path):
    from maf_test_harness.ui.evidence import clean_content

    path = tmp_path / "trace.jsonl"
    path.write_text('{"detail": "token=abcdefghijklmnop"}\n', encoding="utf-8")
    assert b"abcdefghijklmnop" not in clean_content(path)


def test_deliverable_audit_and_execution_reports_carry_no_planted_secret(tmp_path):
    import zipfile

    from maf_test_harness.qa.packaging import build_deliverable

    secret = "abcdefghijklmnop"
    (tmp_path / "audit.jsonl").write_text(f'{{"detail": "token={secret}"}}\n', encoding="utf-8")
    attempt = tmp_path / "executions" / "attempt-1"
    attempt.mkdir(parents=True)
    (attempt / "report.json").write_text(json.dumps({"detail": f"token={secret}"}), encoding="utf-8")
    state = {
        "artifacts": {
            "repo_analysis": {"repo_name": "demo"},
            "requirements": {"note": f"token={secret}"},
            "test_execution": {
                "qa_report": {"detail": f"token={secret}"},
                "markdown": f"# QA\n\ntoken={secret}\n",
                "execution": {},
            },
        }
    }
    with zipfile.ZipFile(build_deliverable(tmp_path, state)) as archive:
        for name in ("audit.jsonl", "executions/attempt-1.json", "requirements.json", "qa_report.md"):
            content = archive.read(f"harness-report/{name}").decode("utf-8")
            assert secret not in content, name


def test_deliverable_excludes_playwright_trace_archives(tmp_path):
    import zipfile

    from maf_test_harness.qa.packaging import build_deliverable

    folder = tmp_path / "browser-reports" / "playwright-report" / "data"
    folder.mkdir(parents=True)
    (folder / "trace.zip").write_bytes(b"raw network and page data")
    (folder / "screenshot.png").write_bytes(b"png")
    state = {"artifacts": {"repo_analysis": {"repo_name": "demo"}}}
    with zipfile.ZipFile(build_deliverable(tmp_path, state)) as archive:
        names = archive.namelist()
    assert "harness-report/evidence/browser-reports/playwright-report/data/screenshot.png" in names
    assert not any(name.endswith("trace.zip") for name in names)


def test_run_lock_held_by_another_process_fails_clearly_instead_of_blocking(tmp_path, monkeypatch):
    import pytest
    from filelock import FileLock

    import maf_test_harness.workflow as wf

    monkeypatch.setattr(wf, "RUN_LOCK_TIMEOUT", 0.1)
    with FileLock(str(tmp_path / "run.lock")):
        with pytest.raises(RuntimeError, match="locked by another process"):
            with wf._run_lock(tmp_path):
                pass

"""Browser test fixture only: scripted model, fixture clone, real workflow and pytest execution."""

import json
import os
import tempfile
from pathlib import Path

from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
from test_execution_stage import local_pytest_plan

import maf_test_harness.ui.api as module
import maf_test_harness.workflow as wf
from maf_test_harness import settings as _settings
from maf_test_harness.workflow_steps import execution as execution_steps

wf.clone_repository = clone_fixture
execution_steps.build_plan = local_pytest_plan
execution_steps.build_environment_plan = local_env_plan
_settings.set_runtime(api_key="fixture-configured-flag")


# Playwright never reaches GitHub: the New-run autofill gets an empty preview.
module.preview_repository = lambda url: {"warnings": []}


def _record_generated_tests() -> None:
    """Same hook as tests/conftest.py: scripted reviewers derive literals from the generated tests they review."""
    from fakes import record_generated

    from maf_test_harness.models import GeneratedFilesDraft
    from maf_test_harness.workflow_steps.planning import PlanningSteps

    original = PlanningSteps._call_model

    async def recording(self, **kwargs):
        result = await original(self, **kwargs)
        if kwargs.get("response_format") is GeneratedFilesDraft and result.get("status") == "completed":
            record_generated(result.get("artifact", {}).get("files", []))
        return result

    PlanningSteps._call_model = recording


_record_generated_tests()


class BrowserRuntime(ScriptedStageRuntime):
    async def run_stage(self, *, stage, state, **kwargs):
        if state["repo_url"].endswith("/slow"):
            import asyncio

            await asyncio.sleep(30)
        if state["repo_url"].endswith("/failure"):
            self.test_body = "assert add(1, 2) == 4"
        result = await super().run_stage(stage=stage, state=state, **kwargs)
        # One acceptance criterion, and a negative case linked only through it: the QA table must show its parent requirement.
        if stage == "requirements" and "artifact" in result:
            result["artifact"]["acceptance_criteria"] = [
                {"text": "add accepts two integers", "requirement_ref": 1, "source_files": ["service.py"]}
            ]
        if stage == "test_design" and "negative_tests" in result.get("artifact", {}):
            criterion = state["artifacts"]["requirements"]["requirement_model"]["acceptance_criteria"][0][
                "id"
            ]
            result["artifact"]["negative_tests"].append(
                {
                    "title": "add returns sum for the criterion",
                    "priority": "P2",
                    "level": "unit",
                    "actor": "Developer",
                    "target": "service.add",
                    "description": "Integers from the criterion are summed.",
                    "preconditions": ["service.py is importable"],
                    "data_notes": "1 and 2",
                    "expected_result": "add(1, 2) returns 3",
                    "steps": ["Call add(1, 2)", "Compare with 3"],
                    "traceability_links": [{"type": "acceptance_criterion", "id": criterion}],
                }
            )
        return result


class BrowserAPI(module.LocalAPI):
    def evidence(self, key):
        run = self.read(key)
        qa = run["state"].get("artifacts", {}).get("test_execution", {}).get("qa_report")
        if qa:
            folder = self.run_path(key) / "browser-reports" / "fixture"
            folder.mkdir(parents=True, exist_ok=True)
            if not (folder / "index.html").exists():
                (folder / "data.json").write_text(json.dumps({"verdict": qa["verdict"]}))
                (
                    folder / "index.html"
                ).write_text("""<!doctype html><h1>Browser report fixture</h1><p id="verdict"></p><script>
fetch('data.json').then(r=>r.json()).then(r=>document.querySelector('#verdict').textContent=r.verdict);
try { parent.document.body.dataset.reportEscaped='yes'; } catch { document.body.dataset.isolated='yes'; }
</script>""")
        return super().evidence(key)


with tempfile.TemporaryDirectory(prefix="maf-ui-browser-") as directory:
    os.environ["HARNESS_USAGE_DIR"] = str(Path(directory) / "usage")
    api = BrowserAPI(Path(directory) / "runs", requirements_mode="model", runtime_factory=BrowserRuntime)
    server = module.make_server(
        api, os.environ["HARNESS_API_TOKEN"], int(os.environ.get("PW_API_PORT", "8876"))
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        api.close()

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


class BrowserRuntime(ScriptedStageRuntime):
    async def run_stage(self, *, stage, state, **kwargs):
        if state["repo_url"].endswith("/slow"):
            import asyncio

            await asyncio.sleep(30)
        if state["repo_url"].endswith("/failure"):
            self.test_body = "assert add(1, 2) == 4"
        return await super().run_stage(stage=stage, state=state, **kwargs)


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
    server = module.make_server(api, os.environ["HARNESS_API_TOKEN"], 8876)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        api.close()

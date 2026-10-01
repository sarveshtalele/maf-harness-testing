"""Browser test fixture only: scripted model, fixture clone, real workflow and pytest execution."""

import os
import tempfile
from pathlib import Path

from fakes import ScriptedStageRuntime, clone_fixture, local_env_plan
from test_execution_stage import local_pytest_plan

import maf_test_harness.ui.api as module
import maf_test_harness.workflow as wf

wf.clone_repository = clone_fixture
wf.build_plan = local_pytest_plan
wf.build_environment_plan = local_env_plan
module.API_KEY = "fixture-configured-flag"
with tempfile.TemporaryDirectory(prefix="maf-ui-browser-") as directory:
    os.environ["HARNESS_USAGE_DIR"] = str(Path(directory) / "usage")
    api = module.LocalAPI(Path(directory) / "runs", runtime_factory=ScriptedStageRuntime)
    server = module.make_server(api, os.environ["HARNESS_API_TOKEN"], 8876)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        api.close()

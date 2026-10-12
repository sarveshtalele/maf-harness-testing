import pytest


@pytest.fixture(autouse=True)
def isolated_usage_ledger(tmp_path, monkeypatch):
    """Tests never touch the real per-machine daily token ledger."""
    monkeypatch.setenv("HARNESS_USAGE_DIR", str(tmp_path / "usage-ledger"))


@pytest.fixture(autouse=True)
def isolated_hooks_config(monkeypatch):
    """Tests never read the developer real hooks files (~/.maf-harness/hooks.json, ./.harness/hooks.json)."""
    monkeypatch.setattr("maf_test_harness.hooks.config.config_paths", lambda project_root=None: [])


@pytest.fixture(autouse=True)
def scripted_generated_text(monkeypatch):
    """Record every generated test as the scripted transport returns it, so scripted reviewers can derive literals."""
    from fakes import GENERATED_TEXT, record_generated

    from maf_test_harness.models import GeneratedFilesDraft
    from maf_test_harness.workflow_steps.planning import PlanningSteps

    original = PlanningSteps._call_model

    async def recording(self, **kwargs):
        result = await original(self, **kwargs)
        if kwargs.get("response_format") is GeneratedFilesDraft and result.get("status") == "completed":
            record_generated(result.get("artifact", {}).get("files", []))
        return result

    GENERATED_TEXT.clear()
    monkeypatch.setattr(PlanningSteps, "_call_model", recording)

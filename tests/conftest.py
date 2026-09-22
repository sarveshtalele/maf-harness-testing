import pytest


@pytest.fixture(autouse=True)
def isolated_usage_ledger(tmp_path, monkeypatch):
    """Tests never touch the real per-machine daily token ledger."""
    monkeypatch.setenv("HARNESS_USAGE_DIR", str(tmp_path / "usage-ledger"))


@pytest.fixture(autouse=True)
def isolated_hooks_config(monkeypatch):
    """Tests never read the developer real hooks files (~/.maf-harness/hooks.json, ./.harness/hooks.json)."""
    monkeypatch.setattr("maf_test_harness.hooks.config.config_paths", lambda project_root=None: [])

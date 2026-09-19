import pytest


@pytest.fixture(autouse=True)
def isolated_usage_ledger(tmp_path, monkeypatch):
    """Tests never touch the real per-machine daily token ledger."""
    monkeypatch.setenv("HARNESS_USAGE_DIR", str(tmp_path / "usage-ledger"))

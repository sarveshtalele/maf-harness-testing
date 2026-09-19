import asyncio
from pathlib import Path

from maf_test_harness.workflow import Harness


def test_downstream_failure_becomes_retryable_gate(tmp_path, monkeypatch):
    import maf_test_harness.workflow as wf

    def clone(url, destination):
        destination.mkdir()
        (destination / "service.py").write_text("def add(a, b): return a + b\n")

    monkeypatch.setattr(wf, "clone_repository", clone)

    class BrokenProvider:
        async def run_stage(self, **kwargs):
            raise ValueError("provider unavailable")

    async def scenario():
        h = Harness(tmp_path, skills_root=Path("skills"), runtime=BrokenProvider())
        await h.start("https://github.com/acme/repo")
        await h.respond(h.view["pending"]["id"], {"decision": "approve"})
        approved_id = h.view["pending"]["id"]
        await h.respond(approved_id, {"decision": "approve"})
        assert h.view["status"] == "waiting"
        assert h.view["pending"]["stage"] == "requirements"
        assert h.view["pending"]["kind"] == "error"
        assert h.view["pending"]["id"] != approved_id
        assert "repo_analysis" in h.state["approved"]
        # Restore the real MAF checkpoint in a new host before retrying.
        restored = Harness(tmp_path, skills_root=Path("skills"), runtime=BrokenProvider())
        await restored.respond(h.view["pending"]["id"], {"decision": "retry"})
        assert restored.view["pending"]["stage"] == "requirements"
        assert restored.view["pending"]["kind"] == "error"

    asyncio.run(scenario())

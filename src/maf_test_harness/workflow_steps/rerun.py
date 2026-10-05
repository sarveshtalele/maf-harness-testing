"""Explicit stage replay with retained evidence and fresh workflow checkpoints."""

import json
from uuid import uuid4

from filelock import FileLock

from ..workflow_support import atomic_json


class RerunSteps:
    async def rerun(self, stage: str, request_id: str | None):
        with FileLock(str(self.run_dir / "run.lock"), timeout=0):
            view = json.loads(self.view_path.read_text(encoding="utf-8"))
            pending = view.get("pending")
            if (pending or {}).get("id") != request_id:
                raise ValueError("Run changed. Refresh before rerunning a step.")
            state = view["state"]
            if stage not in self.stages:
                raise ValueError("Unknown workflow step.")
            index = self.stages.index(stage)
            environment_index = (
                self.stages.index("environment_setup")
                if "environment_setup" in self.stages
                else len(self.stages)
            )
            if any(s not in state.get("approved", {}) for s in self.stages[:index]):
                raise ValueError("Approve the preceding steps before rerunning this step.")
            attempt = uuid4().hex
            archive = self.run_dir / "attempts" / attempt
            archive.mkdir(parents=True)
            atomic_json(archive / "run.json", view)
            # Retain existing source and environments instead of deleting/replaying stale files.
            if index == 0 and (self.run_dir / "repo").exists():
                (self.run_dir / "repo").rename(archive / "repo")
            if index <= environment_index and self.workspace.exists():
                self.workspace.rename(archive / "workspace")
            for name in self.stages[index:]:
                for key in (
                    "artifacts",
                    "approved",
                    "sessions",
                    "feedback",
                    "model_requests",
                    "skill_provenance",
                    "skill_tool_trace",
                ):
                    state.get(key, {}).pop(name, None)
            for key in (
                "draft",
                "accepted_generation_batches",
                "heal",
                "deliverable_zip",
                "completed_reason",
                "design_attempt",
            ):
                state.pop(key, None)
            if index <= environment_index:
                for key in ("environment", "workspace_generated", "env_python_tried"):
                    state.pop(key, None)
            if state.get("launch_app"):
                from ..envs.app_launcher import free_port

                state["app_url"] = f"http://127.0.0.1:{free_port()}"
            self.view = {
                **view,
                "status": "new",
                "pending": None,
                "checkpoint_id": None,
                "rerun_attempt": attempt,
                "restart_stage": stage,
            }
            self.view.pop("error", None)
            self.state = state
            self.save()
            self.audit("stage_rerun", stage=stage, attempt=attempt, retained_evidence=str(archive))
            self._build_workflow()
            result = await self.workflow.run(self.state)
            self._restored = True
            return await self._collect(result)

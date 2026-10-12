"""Explicit stage replay with retained evidence and fresh workflow checkpoints."""

import logging
from functools import partial
from uuid import uuid4

from filelock import FileLock

from ..storage import read_json, retry
from ..workflow_support import atomic_json

# Per-stage caches and records. A rerun must regenerate them for the stages it restarts.
_STAGE_CACHES = (
    ("accepted_planning_batches", "test_planning"),
    ("accepted_design_batches", "test_design"),
    ("design_reviews", "test_design"),
    ("generation_rounds", "test_generation"),
)
_SHARING_VIOLATIONS = {5, 32, 33}  # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION


def _move_into_archive(source, target) -> None:
    """Move a run folder into the attempt archive, retrying short Windows sharing locks."""
    try:
        retry(partial(source.rename, target))
    except PermissionError as exc:
        if getattr(exc, "winerror", None) not in _SHARING_VIOLATIONS:
            raise
        raise RuntimeError(
            f"Could not move {source.name} into the attempt archive; close programs using files in the run folder and retry."
        ) from exc


def _archive_run_folders(moves: list[tuple]) -> None:
    """Apply (source, target) moves in order. If one fails, move the completed ones back so the live run is unchanged."""
    done: list[tuple] = []
    try:
        for source, target in moves:
            _move_into_archive(source, target)
            done.append((source, target))
    except Exception:
        for source, target in reversed(done):
            try:
                retry(partial(target.rename, source))
            except OSError:
                logging.getLogger(__name__).warning(
                    "Could not restore %s after a failed rerun; move it back by hand", source, exc_info=True
                )
        raise


class RerunSteps:
    async def rerun(self, stage: str, request_id: str | None):
        with FileLock(str(self.run_dir / "run.lock"), timeout=0):
            view = read_json(self.view_path)
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
            moves = []
            if index == 0 and (self.run_dir / "repo").exists():
                moves.append((self.run_dir / "repo", archive / "repo"))
            if index <= environment_index and self.workspace.exists():
                moves.append((self.workspace, archive / "workspace"))
            _archive_run_folders(moves)
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
            for key, owner in _STAGE_CACHES:  # batch caches and review records of the restarted stages
                if owner in self.stages[index:]:
                    state.pop(key, None)
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
                "status": "running",
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

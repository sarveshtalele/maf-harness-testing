"""Approval milestones; workflow completion is separate from the test verdict."""


def progress(view: dict) -> dict:
    state = view.get("state", {})
    stages = state.get("stages", [])
    approved = state.get("approved", {})
    done = sum(stage in approved for stage in stages)
    percent = (
        100 if view.get("status") == "complete" else min(99, round(100 * done / len(stages))) if stages else 0
    )
    return {"percent": percent, "approved_stages": done, "total_stages": len(stages)}

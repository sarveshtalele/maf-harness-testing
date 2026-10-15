"""Portable run ledger, evidence gates and dashboard for the Agentic QE plugin."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from git_scope import _path, capture
from sources import fetch_jira, ingest_files, ingest_jira_issues

STAGES = ("intake", "scope", "requirements", "planning", "design", "review", "generation")
ARTIFACTS = {
    "stories": ("stories.json", "stories", "STY-"),
    "requirements": ("requirements.json", "requirements", None),
    "planning": ("plan.json", "items", "PLN-"),
    "design": ("cases.json", "cases", "TC-"),
}
REVIEW_DIMS = {"source_support": 40, "traceability": 30, "e2e_quality": 20, "clarity": 10}
DEFAULT_WORKFLOW = Path(__file__).resolve().parent.parent / "workflows" / "default.json"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def memory_line(run: Path, phase: str, action: str, summary: str) -> None:
    if phase not in {"BEFORE", "AFTER"} or not re.fullmatch(r"[a-z][a-z0-9-]{1,49}", action):
        raise ValueError("Invalid memory event")
    summary = " ".join(summary.split())
    if not 2 <= len(summary) <= 240:
        raise ValueError("Memory summary must be 2–240 characters")
    path = run_file(run, "memory.md")
    with path.open("a", encoding="utf-8") as output:
        if path.stat().st_size == 0:
            output.write(
                "# Agentic QE run memory\n\nDecisions and actions are recorded before and after they occur. Entries omit source text and credentials.\n\n"
            )
        output.write(f"- {now()} | {phase} | {action} | {summary}\n")


@contextmanager
def logged_action(run: Path, action: str, summary: str):
    memory_line(run, "BEFORE", action, summary)
    try:
        yield
    except Exception as exc:
        memory_line(run, "AFTER", action, f"Failed: {type(exc).__name__}")
        raise
    else:
        memory_line(run, "AFTER", action, "Completed")


def stable_id(prefix: str, source: str, claim: str) -> str:
    if prefix not in {"STY", "AC", "CTL", "FR", "TR", "PLN", "TC"} or not source or not claim:
        raise ValueError("Stable ID requires a supported prefix, source and claim")
    return f"{prefix}-{hashlib.sha256((source + chr(0) + claim.casefold().strip()).encode()).hexdigest()[:12].upper()}"


def validate_workflow(workflow: dict) -> dict:
    if workflow.get("schema_version") != 1 or not isinstance(workflow.get("name"), str):
        raise ValueError("Workflow needs schema_version 1 and a name")
    stages = workflow.get("stages")
    if not isinstance(stages, list) or [item.get("id") for item in stages if isinstance(item, dict)] != list(
        STAGES
    ):
        raise ValueError("Workflow stages must follow the required intake → generation order")
    for item in stages:
        if not isinstance(item.get("agent"), str) or not re.fullmatch(r"[a-z][a-z0-9-]{2,49}", item["agent"]):
            raise ValueError("Each workflow stage needs an installed agent name")
        if not isinstance(item.get("prompt"), str) or len(item["prompt"]) > 4000:
            raise ValueError("Stage prompt must be text of at most 4000 characters")
        servers = item.get("mcp_servers", [])
        intents = item.get("tool_intents", [])
        if (
            not isinstance(servers, list)
            or not isinstance(intents, list)
            or any(not isinstance(s, str) for s in servers + intents)
        ):
            raise ValueError("MCP servers and tool intents must be lists of names")
        if item["id"] != "intake" and "atlassian" in servers:
            raise ValueError("Jira MCP may be used only for read-only intake")
    observation = workflow.get("optional_observation") or {}
    if observation.get("enabled"):
        origins = observation.get("allowed_origins") or []
        if (
            not isinstance(origins, list)
            or not origins
            or any(
                not isinstance(origin, str) or not re.fullmatch(r"https?://[A-Za-z0-9.:-]+", origin)
                for origin in origins
            )
        ):
            raise ValueError("Playwright observation needs explicit allowed origins")
    if workflow.get("confidence_floor") != 95:
        raise ValueError("The confidence floor is fixed at 95")
    if workflow.get("max_repair_rounds") != 2:
        raise ValueError("The repair limit is fixed at two rounds")
    return workflow


def run_file(run: Path, name: str) -> Path:
    return run / name


def state(run: Path) -> dict:
    data = read_json(run_file(run, "run.json"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Run directory has no Agentic QE state")
    return data


def create(project: str, mode: str, workflow_path: Path, output_root: Path) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}", project):
        raise ValueError("Project name must be 1–80 letters, digits, spaces, dots, underscores or hyphens")
    if mode not in {"greenfield", "brownfield"}:
        raise ValueError("Mode must be greenfield or brownfield")
    workflow = validate_workflow(read_json(workflow_path))
    slug = re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-")
    run = output_root / f"{slug}-{uuid4().hex[:8]}"
    run.mkdir(parents=True, exist_ok=False)
    with logged_action(run, "initialize", f"Create {mode} run with frozen workflow {sha(workflow)[:12]}"):
        write_json(run_file(run, "workflow.json"), workflow)
        write_json(
            run_file(run, "run.json"),
            {
                "schema_version": 1,
                "project": project,
                "mode": mode,
                "created_at": now(),
                "workflow_sha256": sha(workflow),
                "stages": dict.fromkeys(STAGES, "pending"),
                "approvals": {},
                "repair_approvals": [],
            },
        )
    return run


def add_sources(run: Path, intake: dict) -> None:
    state(run)
    existing = read_json(run_file(run, "sources.json"), {"sources": [], "sections": [], "warnings": []})
    known = {item["id"] for item in existing["sources"]}
    for source in intake["sources"]:
        if source["id"] not in known:
            existing["sources"].append(source)
            existing["sections"].extend(
                section for section in intake["sections"] if section["source_id"] == source["id"]
            )
            known.add(source["id"])
    existing["warnings"].extend(intake.get("warnings") or [])
    write_json(run_file(run, "sources.json"), existing)


def artifact_hash(run: Path, gate: str) -> str:
    name = {"sources": "sources.json", "stories": "stories.json", "scope": "scope.json"}.get(gate)
    if gate == "final":
        required = (
            "sources.json",
            "stories.json",
            "requirements.json",
            "plan.json",
            "cases.json",
            "reviews.json",
            "REPORT.md",
            "traceability.csv",
        )
        if not all(run_file(run, filename).exists() for filename in required):
            raise ValueError("Cannot approve final: generation artifacts are missing")
        filenames = required + (("scope.json",) if run_file(run, "scope.json").exists() else ())
        return sha(
            {
                filename: hashlib.sha256(run_file(run, filename).read_bytes()).hexdigest()
                for filename in filenames
            }
        )
    if not name or not run_file(run, name).exists():
        raise ValueError(f"Cannot approve {gate}: its artifact is missing")
    return sha(read_json(run_file(run, name)))


def approval(run: Path, gate: str, person: str) -> None:
    if gate not in {"sources", "stories", "scope", "final"} or not person.strip():
        raise ValueError("Approval needs a gate and approver name")
    data = state(run)
    if gate == "scope":
        validate_scope(run)
    if gate == "final" and (
        data["stages"].get("generation") != "complete" or not validate_reviews(run)["ok"]
    ):
        raise ValueError("Final approval requires completed generation and a passing review")
    data["approvals"][gate] = {"by": person.strip(), "at": now(), "sha256": artifact_hash(run, gate)}
    write_json(run_file(run, "run.json"), data)


def approved(run: Path, gate: str) -> bool:
    record = state(run)["approvals"].get(gate)
    return bool(record and record.get("sha256") == artifact_hash(run, gate))


def validate_scope(run: Path) -> None:
    if state(run)["mode"] != "brownfield":
        return
    if not approved(run, "stories"):
        raise ValueError("Brownfield impact needs current story approval")
    scope = read_json(run_file(run, "scope.json"))
    if not isinstance(scope, dict) or not scope.get("changed_files"):
        raise ValueError("Brownfield run needs a captured Git change scope")
    repo = Path(scope["repo"])
    revision = scope["revision"]
    current = capture(
        repo,
        scope["mode"],
        revision.get("base"),
        revision.get("target", "HEAD"),
        scope.get("include_untracked", False),
    )
    if current["revision"] != revision or current["changed_files"] != scope["changed_files"]:
        raise ValueError("Git scope changed after capture; recapture and approve it")
    changed = {entry["path"] for entry in scope["changed_files"]}
    for related in scope.get("related_files", []):
        if (
            not isinstance(related, dict)
            or related.get("path") in changed
            or len(str(related.get("reason", "")).strip()) < 10
        ):
            raise ValueError("Related file needs a distinct path and a specific reason")
        path = _path(repo, related["path"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != related.get("sha256"):
            raise ValueError(f"Related file changed or is missing: {related['path']}")
    impacts = scope.get("impacted_stories")
    story_ids = _ids(items(run, "stories"), "STY-", "stories")
    if not isinstance(impacts, list) or not impacts:
        raise ValueError("Brownfield scope needs at least one story mapped to changed files")
    selected = set()
    for impact in impacts:
        if not isinstance(impact, dict):
            raise ValueError("Impact mapping must be an object")
        story_id = impact.get("story_id")
        paths = impact.get("changed_paths")
        if (
            story_id not in story_ids
            or story_id in selected
            or not isinstance(paths, list)
            or not paths
            or any(not isinstance(path, str) or path not in changed for path in paths)
            or len(str(impact.get("reason", "")).strip()) < 10
        ):
            raise ValueError("Impact mapping needs one approved story, changed paths and a specific reason")
        selected.add(story_id)


def select_impact(run: Path, story_id: str, paths: list[str], reason: str) -> None:
    if state(run)["mode"] != "brownfield" or state(run)["stages"]["intake"] != "complete":
        raise ValueError("Impact selection needs a brownfield run with approved intake")
    if not approved(run, "stories"):
        raise ValueError("Impact selection needs current story approval")
    scope = read_json(run_file(run, "scope.json"))
    if not isinstance(scope, dict):
        raise ValueError("Capture the Git diff before selecting impact")
    story_ids = _ids(items(run, "stories"), "STY-", "stories")
    changed = {entry["path"] for entry in scope.get("changed_files", [])}
    if (
        story_id not in story_ids
        or not paths
        or any(path not in changed for path in paths)
        or len(reason.strip()) < 10
    ):
        raise ValueError("Select an approved story, changed paths and a specific impact reason")
    scope["impacted_stories"] = [
        item
        for item in scope.get("impacted_stories", [])
        if isinstance(item, dict) and item.get("story_id") != story_id
    ] + [{"story_id": story_id, "changed_paths": list(dict.fromkeys(paths)), "reason": reason.strip()}]
    write_json(run_file(run, "scope.json"), scope)


def items(run: Path, group: str) -> list[dict]:
    filename, key, _ = ARTIFACTS[group]
    data = read_json(run_file(run, filename))
    if not isinstance(data, dict) or not isinstance(data.get(key), list) or not data[key]:
        raise ValueError(f"Missing or empty {filename} {key}")
    if any(not isinstance(item, dict) for item in data[key]):
        raise ValueError(f"{filename} contains a non-object item")
    return data[key]


def _ids(records: list[dict], prefix: str | None, name: str) -> set[str]:
    ids = [item.get("id") for item in records]
    if any(
        not isinstance(identifier, str)
        or not re.fullmatch(r"[A-Z]+-[A-Za-z0-9-]+", identifier)
        or (prefix and not identifier.startswith(prefix))
        for identifier in ids
    ):
        raise ValueError(f"{name} has an invalid ID")
    if len(ids) != len(set(ids)):
        raise ValueError(f"{name} has duplicate IDs")
    return set(ids)


def _citations(item: dict, sections: dict[tuple[str, str], str]) -> None:
    refs = item.get("source_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError(f"{item.get('id')} has no source references")
    for ref in refs:
        if not isinstance(ref, dict) or not all(
            isinstance(ref.get(k), str) and ref[k] for k in ("source_id", "locator", "quote")
        ):
            raise ValueError(f"{item.get('id')} has an incomplete source reference")
        source = sections.get((ref["source_id"], ref["locator"]))
        if source is None or ref["quote"] not in source:
            raise ValueError(
                f"{item.get('id')} cites text not found in {ref['source_id']} at {ref['locator']}"
            )


def validate_artifacts(run: Path) -> dict:
    if not approved(run, "sources") or not approved(run, "stories"):
        raise ValueError("Source and story approvals are missing or stale")
    validate_scope(run)
    if state(run)["mode"] == "brownfield" and not approved(run, "scope"):
        raise ValueError("Brownfield scope approval is missing or stale")
    sources = read_json(run_file(run, "sources.json"))
    if not isinstance(sources, dict) or not sources.get("sections"):
        raise ValueError("No parsed source evidence")
    sections = {
        (section["source_id"], section["locator"]): section["text"] for section in sources["sections"]
    }
    all_stories = items(run, "stories")
    requirements = items(run, "requirements")
    plans = items(run, "planning")
    cases = items(run, "design")
    all_story_ids = _ids(all_stories, "STY-", "stories")
    story_ids = (
        {item["story_id"] for item in read_json(run_file(run, "scope.json"))["impacted_stories"]}
        if state(run)["mode"] == "brownfield"
        else all_story_ids
    )
    stories = [story for story in all_stories if story["id"] in story_ids]
    requirement_ids = _ids(requirements, None, "requirements")
    plan_ids = _ids(plans, "PLN-", "plans")
    case_ids = _ids(cases, "TC-", "cases")
    controls = set()
    story_controls = {}
    for story in all_stories:
        _citations(story, sections)
        if not re.fullmatch(r"As a .+, I want to .+, so that .+", str(story.get("user_story") or ""), re.I):
            raise ValueError(f"{story['id']} is not a complete role/action/benefit user story")
        criteria = story.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria:
            raise ValueError(f"{story['id']} has no acceptance criteria")
        for criterion in criteria:
            if (
                not str(criterion.get("id", "")).startswith("AC-")
                or not str(criterion.get("control_id", "")).startswith("CTL-")
                or not criterion.get("text")
            ):
                raise ValueError(f"{story['id']} has an incomplete acceptance criterion")
            _citations(criterion, sections)
            if story["id"] in story_ids:
                controls.add(criterion["control_id"])
                story_controls.setdefault(story["id"], set()).add(criterion["control_id"])
    requirement_by_id = {item["id"]: item for item in requirements}
    plan_by_id = {item["id"]: item for item in plans}
    for requirement in requirements:
        _citations(requirement, sections)
        if requirement["id"][:3] not in {"FR-", "TR-"} or requirement.get("type") != (
            "functional" if requirement["id"].startswith("FR-") else "technical"
        ):
            raise ValueError(f"{requirement['id']} has an invalid functional/technical type")
        if (
            not requirement.get("description")
            or not set(requirement.get("story_ids") or []).issubset(story_ids)
            or not requirement.get("story_ids")
        ):
            raise ValueError(f"{requirement['id']} lacks a description or story link")
        if not set(requirement.get("control_ids") or []).issubset(controls) or not requirement.get(
            "control_ids"
        ):
            raise ValueError(f"{requirement['id']} lacks a valid control link")
        if not set(requirement["control_ids"]).issubset(
            {control for identifier in requirement["story_ids"] for control in story_controls[identifier]}
        ):
            raise ValueError(f"{requirement['id']} links controls outside its stories")
    if not story_ids.issubset(
        {identifier for requirement in requirements for identifier in requirement["story_ids"]}
    ):
        raise ValueError("At least one story has no functional or technical requirement")
    for plan in plans:
        _citations(plan, sections)
        if (
            not plan.get("objective")
            or not set(plan.get("requirement_ids") or []).issubset(requirement_ids)
            or not plan.get("requirement_ids")
        ):
            raise ValueError(f"{plan['id']} lacks an objective or requirement link")
        if not set(plan.get("control_ids") or []).issubset(controls) or not plan.get("control_ids"):
            raise ValueError(f"{plan['id']} lacks a valid control link")
        if not set(plan["control_ids"]).issubset(
            {
                control
                for identifier in plan["requirement_ids"]
                for control in requirement_by_id[identifier]["control_ids"]
            }
        ):
            raise ValueError(f"{plan['id']} links controls outside its requirements")
    for case in cases:
        _citations(case, sections)
        if (
            case.get("level") != "e2e"
            or not case.get("title")
            or not case.get("expected_outcome")
            or not isinstance(case.get("steps"), list)
            or not case["steps"]
        ):
            raise ValueError(f"{case['id']} is not a complete E2E case")
        if not set(case.get("requirement_ids") or []).issubset(requirement_ids) or not case.get(
            "requirement_ids"
        ):
            raise ValueError(f"{case['id']} lacks a valid requirement link")
        if not set(case.get("control_ids") or []).issubset(controls) or not case.get("control_ids"):
            raise ValueError(f"{case['id']} lacks a valid control link")
        if not set(case.get("plan_ids") or []).issubset(plan_ids) or not case.get("plan_ids"):
            raise ValueError(f"{case['id']} lacks a valid plan link")
        if not set(case["control_ids"]).issubset(
            {
                control
                for identifier in case["requirement_ids"]
                for control in requirement_by_id[identifier]["control_ids"]
            }
        ):
            raise ValueError(f"{case['id']} links controls outside its requirements")
        if not set(case["requirement_ids"]).issubset(
            {
                identifier
                for plan_id in case["plan_ids"]
                for identifier in plan_by_id[plan_id]["requirement_ids"]
            }
        ):
            raise ValueError(f"{case['id']} links requirements outside its plan")
    if not requirement_ids.issubset({identifier for plan in plans for identifier in plan["requirement_ids"]}):
        raise ValueError("At least one requirement has no plan item")
    if not requirement_ids.issubset({identifier for case in cases for identifier in case["requirement_ids"]}):
        raise ValueError("At least one requirement has no E2E case")
    if not plan_ids.issubset({identifier for case in cases for identifier in case["plan_ids"]}):
        raise ValueError("At least one plan item has no E2E case")
    if not controls.issubset({control for case in cases for control in case.get("control_ids") or []}):
        raise ValueError("At least one story control has no E2E case")
    return {
        "stories": stories,
        "requirements": requirements,
        "plans": plans,
        "cases": cases,
        "controls": controls,
        "ids": story_ids | requirement_ids | plan_ids | case_ids,
    }


def validate_reviews(run: Path) -> dict:
    graph = validate_artifacts(run)
    reviews = read_json(run_file(run, "reviews.json"))
    if not isinstance(reviews, dict) or not isinstance(reviews.get("reviews"), list):
        raise ValueError("Independent review ledger is missing")
    latest = {}
    for review in reviews["reviews"]:
        if not isinstance(review, dict) or review.get("subject_id") not in graph["ids"]:
            raise ValueError("Review refers to an unknown subject")
        score_parts = review.get("scores")
        if not isinstance(score_parts, dict) or set(score_parts) != set(REVIEW_DIMS):
            raise ValueError(f"{review['subject_id']} has an incomplete review rubric")
        if any(
            not isinstance(score_parts[key], int)
            or isinstance(score_parts[key], bool)
            or not 0 <= score_parts[key] <= maximum
            for key, maximum in REVIEW_DIMS.items()
        ):
            raise ValueError(f"{review['subject_id']} has invalid review points")
        if not isinstance(review.get("round"), int) or not 0 <= review["round"] <= 2:
            raise ValueError(f"{review['subject_id']} has an invalid repair round")
        previous = latest.get(review["subject_id"])
        if previous and review["round"] <= previous["round"]:
            raise ValueError(f"{review['subject_id']} review rounds must increase")
        latest[review["subject_id"]] = review
    subjects = {
        item["id"]: item
        for group in (graph["stories"], graph["requirements"], graph["plans"], graph["cases"])
        for item in group
    }
    failures = []
    data = state(run)
    for identifier, subject in subjects.items():
        review = latest.get(identifier)
        if not review:
            failures.append(f"{identifier}: no independent review")
            continue
        if review.get("subject_sha256") != sha(subject):
            failures.append(f"{identifier}: review is stale")
            continue
        if review["round"] > 0 and not any(
            a.get("subject_id") == identifier and a.get("round") == review["round"]
            for a in data["repair_approvals"]
        ):
            failures.append(f"{identifier}: repair round lacks human approval")
            continue
        points = sum(review["scores"].values())
        if review.get("verdict") != "approved" or points < 95:
            failures.append(f"{identifier}: {points}/100, {review.get('verdict', 'unreviewed')}")
    return {
        "ok": not failures,
        "failures": failures,
        "reviewed": len(latest),
        "total": len(subjects),
        "threshold": 95,
    }


def approve_repair(run: Path, subject_id: str, person: str) -> None:
    data = state(run)
    reviews = read_json(run_file(run, "reviews.json"), {"reviews": []})["reviews"]
    history = [entry for entry in reviews if entry.get("subject_id") == subject_id]
    if not history or not person.strip():
        raise ValueError("Repair approval needs an existing review and a person")
    last = history[-1]
    next_round = last["round"] + 1
    if next_round > 2 or (last.get("verdict") == "approved" and sum(last["scores"].values()) >= 95):
        raise ValueError("Repair is not needed or the two-round limit is reached")
    data["repair_approvals"].append(
        {
            "subject_id": subject_id,
            "round": next_round,
            "by": person.strip(),
            "at": now(),
            "prior_review_sha256": sha(last),
        }
    )
    write_json(run_file(run, "run.json"), data)


def complete_stage(run: Path, stage: str) -> None:
    data = state(run)
    if stage not in STAGES:
        raise ValueError("Unknown stage")
    for previous in STAGES[: STAGES.index(stage)]:
        if data["stages"].get(previous) != "complete":
            raise ValueError(f"Complete {previous} first")
    if stage == "intake":
        items(run, "stories")
        if not approved(run, "sources") or not approved(run, "stories"):
            raise ValueError("Approve current sources and stories before completing intake")
    elif stage == "scope" and data["mode"] == "brownfield":
        validate_scope(run)
        if not approved(run, "scope"):
            raise ValueError("Approve current Git change scope before continuing")
    elif stage in {"requirements", "planning", "design"}:
        items(run, {"requirements": "requirements", "planning": "planning", "design": "design"}[stage])
    elif stage == "review":
        result = validate_reviews(run)
        if not result["ok"]:
            raise ValueError("Confidence gate blocked: " + "; ".join(result["failures"][:10]))
    elif stage == "generation" and not all(
        run_file(run, name).exists() for name in ("REPORT.md", "traceability.csv")
    ):
        raise ValueError("Generate the report and traceability matrix first")
    data["stages"][stage] = "complete"
    data["updated_at"] = now()
    write_json(run_file(run, "run.json"), data)


def generate(run: Path) -> None:
    data = state(run)
    if data["stages"].get("review") != "complete":
        raise ValueError("Complete independent review before generation")
    result = validate_reviews(run)
    if not result["ok"]:
        raise ValueError("Confidence gate blocked: " + "; ".join(result["failures"][:10]))
    graph = validate_artifacts(run)
    reviews = read_json(run_file(run, "reviews.json"))["reviews"]
    scores = {entry["subject_id"]: sum(entry["scores"].values()) for entry in reviews}
    scope = read_json(run_file(run, "scope.json"), {}) if data["mode"] == "brownfield" else {}
    impact_paths = {item["story_id"]: item["changed_paths"] for item in scope.get("impacted_stories", [])}
    control_stories = {}
    for story in graph["stories"]:
        for criterion in story["acceptance_criteria"]:
            control_stories.setdefault(criterion["control_id"], set()).add(story["id"])
    rows = []
    for case in graph["cases"]:
        for control in case["control_ids"]:
            rows.append(
                {
                    "source_ids": ", ".join(sorted({ref["source_id"] for ref in case["source_refs"]})),
                    "story_ids": ", ".join(sorted(control_stories[control])),
                    "changed_paths": ", ".join(
                        sorted(
                            {
                                path
                                for story_id in control_stories[control]
                                for path in impact_paths.get(story_id, [])
                            }
                        )
                    ),
                    "control_id": control,
                    "requirement_ids": ", ".join(case["requirement_ids"]),
                    "plan_ids": ", ".join(case["plan_ids"]),
                    "case_id": case["id"],
                    "review_score": scores[case["id"]],
                }
            )
    with run_file(run, "traceability.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=(
                "source_ids",
                "story_ids",
                "changed_paths",
                "control_id",
                "requirement_ids",
                "plan_ids",
                "case_id",
                "review_score",
            ),
        )
        writer.writeheader()
        writer.writerows(rows)

    def display(value: object) -> str:
        return html.escape(" ".join(str(value).split()))

    lines = [
        f"# {display(data['project'])} · Agentic QE",
        "",
        "Generation status: complete · Execution status: not applicable",
        "",
        f"{len(graph['stories'])} stories · {len(graph['requirements'])} requirements · {len(graph['cases'])} E2E cases · confidence floor 95/100",
        "",
    ]
    if scope:
        lines += ["## Approved Git change impact", ""]
        for impact in scope["impacted_stories"]:
            lines.append(
                f"- **{impact['story_id']}** → {', '.join(impact['changed_paths'])}: {display(impact['reason'])}"
            )
        lines.append("")
    lines += ["## Sources and stories", ""]
    for story in graph["stories"]:
        lines += [
            f"- **{story['id']} ({scores[story['id']]}/100):** {display(story['user_story'])}",
            f"  - Controls: {', '.join(criterion['control_id'] for criterion in story['acceptance_criteria'])}",
            f"  - Evidence: {', '.join(ref['source_id'] + ' ' + ref['locator'] for ref in story['source_refs'])}",
        ]
    lines += ["", "## Functional and technical requirements", ""]
    for requirement in graph["requirements"]:
        lines.append(
            f"- **{requirement['id']} ({scores[requirement['id']]}/100):** {display(requirement['description'])} · controls {', '.join(requirement['control_ids'])}"
        )
    lines += ["", "## E2E plan", ""]
    for plan in graph["plans"]:
        lines.append(
            f"- **{plan['id']} ({scores[plan['id']]}/100):** {display(plan['objective'])} · requirements {', '.join(plan['requirement_ids'])}"
        )
    lines += ["", "## E2E cases", ""]
    for case in graph["cases"]:
        lines += [
            f"### {case['id']} · {display(case['title'])} ({scores[case['id']]}/100)",
            "",
            f"Expected outcome: {display(case['expected_outcome'])}",
            "",
            f"Traceability: {', '.join(case['requirement_ids'])} · {', '.join(case['control_ids'])} · {', '.join(case['plan_ids'])}",
            "",
        ]
        for number, step in enumerate(case["steps"], 1):
            action = step.get("action") if isinstance(step, dict) else str(step)
            expected = step.get("expected") if isinstance(step, dict) else ""
            lines.append(f"{number}. {display(action)}" + (f" → {display(expected)}" if expected else ""))
        lines.append("")
    run_file(run, "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    complete_stage(run, "generation")
    dashboard(run)


def dashboard(run: Path) -> str:
    data = state(run)
    done = sum(data["stages"].get(stage) == "complete" for stage in STAGES)
    percent = round(done / len(STAGES) * 100)
    gate_status = {}
    for gate in ("sources", "stories", "scope", "final"):
        if gate == "scope" and data["mode"] != "brownfield":
            continue
        try:
            current = approved(run, gate)
            if current and gate == "scope":
                validate_scope(run)
        except (ValueError, OSError):
            current = False
        gate_status[gate] = "current" if current else "stale" if gate in data["approvals"] else "pending"
    review = None
    if run_file(run, "reviews.json").exists() and all(
        run_file(run, ARTIFACTS[group][0]).exists() for group in ARTIFACTS
    ):
        try:
            review = validate_reviews(run)
        except ValueError as exc:
            review = {"ok": False, "failures": [str(exc)], "reviewed": 0, "total": 0}
    next_stage = next((stage for stage in STAGES if data["stages"].get(stage) != "complete"), None)
    if next_stage is None:
        next_stage = (
            "export approved artifacts" if gate_status["final"] == "current" else "final approval and export"
        )
    stale_gate = next((gate for gate, status in gate_status.items() if status == "stale"), None)
    if stale_gate:
        next_stage = f"renew {stale_gate} approval"
    blockers = review["failures"] if review and not review["ok"] else []
    rows = "".join(
        f"<tr><th>{html.escape(stage.title())}</th><td>{html.escape(data['stages'].get(stage, 'pending'))}</td></tr>"
        for stage in STAGES
    )
    blocker_html = "".join(f"<li>{html.escape(blocker)}</li>" for blocker in blockers) or "<li>None</li>"
    source_data = read_json(run_file(run, "sources.json"), {"sources": [], "sections": []})
    case_data = read_json(run_file(run, "cases.json"), {"cases": []})
    scope_data = read_json(run_file(run, "scope.json"), {}) if data["mode"] == "brownfield" else {}
    scope_summary = (
        f"{len(scope_data.get('changed_files', []))} changed files · "
        f"{len(scope_data.get('impacted_stories', []))} impacted stories"
        if data["mode"] == "brownfield"
        else "Greenfield · no Git scope"
    )
    impact_rows = "".join(
        f"<li><strong>{html.escape(str(item.get('story_id', '')))}</strong> · "
        f"<code>{html.escape(', '.join(str(path) for path in item.get('changed_paths', [])))}</code>"
        f"<p>{html.escape(str(item.get('reason', '')))}</p></li>"
        for item in scope_data.get("impacted_stories", [])
        if isinstance(item, dict)
    )
    impact_html = (
        f"<h2>Approved change impact</h2><ul class='impact-list'>{impact_rows}</ul>" if impact_rows else ""
    )
    gate_rows = "".join(
        f"<tr><th>{html.escape(gate.title())}</th><td>{html.escape(status)}</td></tr>"
        for gate, status in gate_status.items()
    )
    artifact_links = (
        "".join(
            f'<li><a href="{name}">{html.escape(name)}</a></li>'
            for name in (
                "REPORT.md",
                "traceability.csv",
                "stories.json",
                "requirements.json",
                "plan.json",
                "cases.json",
            )
            if run_file(run, name).exists()
        )
        or "<li>Artifacts appear as stages complete</li>"
    )
    reviews_data = read_json(run_file(run, "reviews.json"), {"reviews": []})
    case_scores = {
        entry["subject_id"]: sum(entry["scores"].values())
        for entry in reviews_data.get("reviews", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("scores"), dict)
        and all(isinstance(point, int) for point in entry["scores"].values())
    }
    case_rows = "".join(
        f"<tr><th>{html.escape(str(case.get('id', '')))}</th><td>{html.escape(str(case.get('title', '')))}</td><td>{case_scores.get(case.get('id'), 'Pending')}</td></tr>"
        for case in case_data.get("cases", [])
    )
    case_status = (
        "Approved for export"
        if done == len(STAGES) and gate_status["final"] == "current"
        else "Reviewed, awaiting final approval"
        if done == len(STAGES)
        else "Draft until generation and final approval"
    )
    cases_html = (
        f"<h2>E2E case catalog</h2><p class='small'>{case_status}.</p><table><thead><tr><th>ID</th><th>Journey</th><th>Evidence score</th></tr></thead><tbody>{case_rows}</tbody></table>"
        if case_rows
        else ""
    )
    review_summary = (
        f"{review['reviewed']} of {review['total']} subjects reviewed · 95/100 minimum"
        if review
        else "Independent review pending"
    )
    memory = (
        run_file(run, "memory.md").read_text(encoding="utf-8").splitlines()
        if run_file(run, "memory.md").exists()
        else []
    )
    recent = (
        "".join(
            f"<li>{html.escape(line.removeprefix('- '))}</li>"
            for line in memory[-6:]
            if line.startswith("- ")
        )
        or "<li>No actions recorded yet</li>"
    )
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agentic QE · {html.escape(data["project"])}</title><style>body{{font:16px/1.55 system-ui,sans-serif;background:#f5f7fa;color:#162235;max-width:900px;margin:0 auto;padding:32px}}main{{background:white;border:1px solid #dce3ed;border-radius:14px;padding:28px}}h1{{margin:0 0 8px}}.bar{{height:12px;border-radius:10px;background:#e6ebf2;overflow:hidden}}.fill{{height:100%;width:{percent}%;background:#126b61}}table{{border-collapse:collapse;width:100%;margin:20px 0}}th,td{{text-align:left;border-bottom:1px solid #e6ebf2;padding:10px;overflow-wrap:anywhere}}.impact-list{{list-style:none;padding:0}}.impact-list li{{border:1px solid #dce3ed;border-radius:10px;padding:12px 14px;margin:10px 0}}.impact-list p{{margin:6px 0 0}}.impact-list code{{overflow-wrap:anywhere}}.small{{color:#50627a}}a{{color:#075b83}}a:focus-visible{{outline:3px solid #075b83;outline-offset:3px}}@media(max-width:600px){{body{{padding:12px}}main{{padding:16px}}}}</style><main><p class="small">Agentic QE · {html.escape(data["mode"])}</p><h1>{html.escape(data["project"])}</h1><p><strong>{percent}%</strong> · {done} of {len(STAGES)} mandatory stages complete</p><div class="bar" role="progressbar" aria-label="Workflow progress" aria-valuenow="{percent}" aria-valuemin="0" aria-valuemax="100"><div class="fill"></div></div><p class="small">{len(source_data.get("sources", []))} sources · {len(source_data.get("sections", []))} evidence sections · {len(case_data.get("cases", []))} E2E cases<br>{html.escape(review_summary)}<br>{html.escape(scope_summary)}</p><h2>Stages</h2><table>{rows}</table><h2>Approval gates</h2><table>{gate_rows}</table><h2>Next action</h2><p>{html.escape(next_stage.title())}</p><h2>Confidence blockers</h2><ul>{blocker_html}</ul>{impact_html}{cases_html}<h2>Recent decisions and actions</h2><ul>{recent}</ul><h2>Artifacts</h2><ul>{artifact_links}<li><a href="memory.md">Run memory</a></li></ul><p class="small">Saved {html.escape(now())}. Progress is stage completion, not confidence or an ETA.</p></main></html>"""
    run_file(run, "dashboard.html").write_text(page, encoding="utf-8")
    summary = (
        f"# {data['project']} · Agentic QE\n\n**Progress:** {percent}% ({done}/{len(STAGES)} stages; {len(STAGES) - done} remaining)  \n**Evidence:** {len(source_data.get('sources', []))} sources, {len(source_data.get('sections', []))} sections, {len(case_data.get('cases', []))} E2E cases  \n**Scope:** {scope_summary}  \n**Next:** {next_stage}\n\n| Stage | Status |\n| --- | --- |\n"
        + "".join(f"| {stage.title()} | {data['stages'].get(stage, 'pending')} |\n" for stage in STAGES)
    )
    summary += "\n| Approval | Status |\n| --- | --- |\n" + "".join(
        f"| {gate.title()} | {status} |\n" for gate, status in gate_status.items()
    )
    if blockers:
        summary += "\n**Confidence blockers:** " + "; ".join(blockers[:5])
    if memory:
        summary += "\n**Recent action:** " + next(
            (line.removeprefix("- ") for line in reversed(memory) if line.startswith("- ")), "none"
        )
    summary += f"\nDashboard: {run_file(run, 'dashboard.html').resolve()}"
    return summary


def export(run: Path, destination: Path) -> None:
    data = state(run)
    if (
        any(data["stages"].get(stage) != "complete" for stage in STAGES)
        or not validate_reviews(run)["ok"]
        or not approved(run, "final")
    ):
        raise ValueError("Export needs all stages, a passing 95+ review and current final approval")
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    dashboard(run)
    for filename in (
        "run.json",
        "workflow.json",
        "memory.md",
        "sources.json",
        "scope.json",
        "stories.json",
        "requirements.json",
        "plan.json",
        "cases.json",
        "reviews.json",
        "traceability.csv",
        "REPORT.md",
        "dashboard.html",
    ):
        if run_file(run, filename).exists():
            shutil.copy2(run_file(run, filename), destination / filename)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create_cmd = sub.add_parser("init")
    create_cmd.add_argument("--project", required=True)
    create_cmd.add_argument("--mode", choices=("greenfield", "brownfield"), required=True)
    create_cmd.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    create_cmd.add_argument("--output-root", type=Path, default=Path(".agentic-qe/runs"))
    ingest_cmd = sub.add_parser("ingest")
    ingest_cmd.add_argument("--run", type=Path, required=True)
    ingest_cmd.add_argument("files", nargs="+", type=Path)
    jira_cmd = sub.add_parser("jira-rest")
    jira_cmd.add_argument("--run", type=Path, required=True)
    jira_cmd.add_argument("--site", required=True)
    jira_cmd.add_argument("--key", action="append", default=[])
    jira_cmd.add_argument("--jql", default="")
    jira_import_cmd = sub.add_parser("jira-import")
    jira_import_cmd.add_argument("--run", type=Path, required=True)
    jira_import_cmd.add_argument("--file", type=Path, required=True)
    note_cmd = sub.add_parser("note")
    note_cmd.add_argument("--run", type=Path, required=True)
    note_cmd.add_argument("--phase", choices=("before", "after"), required=True)
    note_cmd.add_argument("--stage", choices=(*STAGES, "workflow"), required=True)
    note_cmd.add_argument("--summary", required=True)
    scope_cmd = sub.add_parser("scope")
    scope_cmd.add_argument("--run", type=Path, required=True)
    scope_cmd.add_argument("--repo", type=Path, required=True)
    scope_cmd.add_argument("--mode", choices=("committed", "working-tree"), required=True)
    scope_cmd.add_argument("--base")
    scope_cmd.add_argument("--target", default="HEAD")
    scope_cmd.add_argument("--include-untracked", action="store_true")
    impact_cmd = sub.add_parser("impact")
    impact_cmd.add_argument("--run", type=Path, required=True)
    impact_cmd.add_argument("--story", required=True)
    impact_cmd.add_argument("--path", action="append", required=True)
    impact_cmd.add_argument("--reason", required=True)
    for name in ("status", "dashboard", "validate", "generate"):
        sub.add_parser(name).add_argument("--run", type=Path, required=True)
    approve_cmd = sub.add_parser("approve")
    approve_cmd.add_argument("--run", type=Path, required=True)
    approve_cmd.add_argument("--gate", choices=("sources", "stories", "scope", "final"), required=True)
    approve_cmd.add_argument("--by", required=True)
    repair_cmd = sub.add_parser("approve-repair")
    repair_cmd.add_argument("--run", type=Path, required=True)
    repair_cmd.add_argument("--subject", required=True)
    repair_cmd.add_argument("--by", required=True)
    stage_cmd = sub.add_parser("stage-complete")
    stage_cmd.add_argument("--run", type=Path, required=True)
    stage_cmd.add_argument("--stage", choices=STAGES, required=True)
    export_cmd = sub.add_parser("export")
    export_cmd.add_argument("--run", type=Path, required=True)
    export_cmd.add_argument("--dest", type=Path, required=True)
    id_cmd = sub.add_parser("id")
    id_cmd.add_argument("--kind", required=True)
    id_cmd.add_argument("--source", required=True)
    id_cmd.add_argument("--claim", required=True)
    hash_cmd = sub.add_parser("subject-hashes")
    hash_cmd.add_argument("--run", type=Path, required=True)
    workflow_cmd = sub.add_parser("validate-workflow")
    workflow_cmd.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "init":
            print(create(args.project, args.mode, args.workflow, args.output_root))
        elif args.command == "ingest":
            state(args.run)
            with logged_action(args.run, "source-ingest", f"Parse {len(args.files)} selected local files"):
                add_sources(args.run, ingest_files(args.files))
            print(dashboard(args.run))
        elif args.command == "jira-rest":
            state(args.run)
            with logged_action(args.run, "jira-rest-intake", "Read selected Jira Cloud issues"):
                add_sources(
                    args.run,
                    fetch_jira(
                        args.site,
                        os.environ.get("JIRA_EMAIL", ""),
                        os.environ.get("JIRA_API_TOKEN", ""),
                        args.key,
                        args.jql,
                    ),
                )
            print(dashboard(args.run))
        elif args.command == "jira-import":
            state(args.run)
            with logged_action(args.run, "jira-mcp-import", "Import selected Jira MCP issue fields"):
                add_sources(args.run, ingest_jira_issues(read_json(args.file)))
            print(dashboard(args.run))
        elif args.command == "scope":
            if state(args.run)["mode"] != "brownfield":
                raise ValueError("Git scope is only used for brownfield runs")
            with logged_action(args.run, "git-scope", f"Capture {args.mode} change set"):
                write_json(
                    run_file(args.run, "scope.json"),
                    capture(args.repo, args.mode, args.base, args.target, args.include_untracked),
                )
            print(dashboard(args.run))
        elif args.command == "impact":
            state(args.run)
            with logged_action(args.run, "story-impact", f"Map {args.story} to selected Git change paths"):
                select_impact(args.run, args.story, args.path, args.reason)
            print(dashboard(args.run))
        elif args.command == "approve":
            state(args.run)
            with logged_action(args.run, "human-approval", f"Record {args.gate} gate approval"):
                approval(args.run, args.gate, args.by)
            print(dashboard(args.run))
        elif args.command == "approve-repair":
            state(args.run)
            with logged_action(args.run, "repair-approval", f"Approve targeted repair for {args.subject}"):
                approve_repair(args.run, args.subject, args.by)
            print(dashboard(args.run))
        elif args.command == "stage-complete":
            state(args.run)
            with logged_action(args.run, "stage-gate", f"Complete {args.stage} after gate validation"):
                complete_stage(args.run, args.stage)
            print(dashboard(args.run))
        elif args.command == "note":
            state(args.run)
            memory_line(args.run, args.phase.upper(), f"{args.stage}-decision", args.summary)
            print(dashboard(args.run))
        elif args.command in {"status", "dashboard"}:
            print(dashboard(args.run))
        elif args.command == "validate":
            result = validate_reviews(args.run)
            print(json.dumps(result, indent=2))
            if not result["ok"]:
                raise SystemExit(2)
        elif args.command == "generate":
            state(args.run)
            with logged_action(args.run, "generation", "Build reviewed E2E report and traceability matrix"):
                generate(args.run)
            print(dashboard(args.run))
        elif args.command == "export":
            state(args.run)
            with logged_action(args.run, "export", "Copy approved E2E artifacts to selected destination"):
                export(args.run, args.dest)
            shutil.copy2(run_file(args.run, "memory.md"), args.dest / "memory.md")
            print(f"Exported approved artifacts to {args.dest}")
        elif args.command == "id":
            print(stable_id(args.kind, args.source, args.claim))
        elif args.command == "subject-hashes":
            graph = validate_artifacts(args.run)
            print(
                json.dumps(
                    {
                        item["id"]: sha(item)
                        for group in (graph["stories"], graph["requirements"], graph["plans"], graph["cases"])
                        for item in group
                    },
                    indent=2,
                )
            )
        elif args.command == "validate-workflow":
            validate_workflow(read_json(args.file))
            print("Workflow valid")
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Agentic QE: {exc}\n")


if __name__ == "__main__":
    main()

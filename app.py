"""Streamlit UI for the autonomous, human-approved MAF test-engineering workflow."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from uuid import uuid4

import streamlit as st

from maf_test_harness.artifact_presenter import artifact_downloads, humanize, render_json_ui
from maf_test_harness.maf_runtime import MAFHarnessRuntime
from maf_test_harness.personas import PERSONAS
from maf_test_harness.runtime_service import RuntimeService
from maf_test_harness.settings import API_KEY, BASE_URL, MODEL, validate_gateway_settings
from maf_test_harness.usage import DailyUsageLedger
from maf_test_harness.workflow import STAGES, Harness

ROOT = Path(__file__).resolve().parent
RUNS = Path(os.getenv("HARNESS_RUNS_DIR") or ROOT / ".harness_runs")

st.set_page_config(page_title="MAF Test Harness", page_icon="T", layout="wide")
st.title("MAF Test Harness")
st.caption(
    "Repository analysis → requirements → isolated environment → test plan → test design → generated tests → host execution → QA report + ZIP. "
    "Every stage is reviewed by a human; running the tests needs its own explicit approval."
)


STAGE_LABELS = {
    "repo_analysis": "Repository Analysis",
    "requirements": "Requirements",
    "environment_setup": "Environment Setup",
    "test_planning": "Test Planning",
    "test_design": "Test Design",
    "test_generation": "Test Generation",
    "test_execution": "Test Execution & QA",
    "complete": "Complete",
    "stopped": "Stopped",
    "interrupted": "Interrupted",
    "new": "New Run",
}


def stage_label(value: str) -> str:
    return STAGE_LABELS.get(value, humanize(value))


def service() -> RuntimeService:
    if "service" not in st.session_state:
        st.session_state.service = RuntimeService()
    return st.session_state.service


def close_runtime() -> None:
    old = st.session_state.get("harness")
    if old and old.runtime:
        try:
            service().submit(old.runtime.close()).result(15)
        except Exception:
            pass
    old_service = st.session_state.pop("service", None)
    if old_service:
        old_service.close()


def dispatch(coro) -> None:
    try:
        st.session_state.job = service().submit(coro)
        st.session_state.pop("ui_error", None)
    except Exception as exc:
        st.session_state.ui_error = str(exc)
    st.rerun()


def source_language(path: str) -> str:
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "jsx",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".java": "java",
        ".cs": "csharp",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".kt": "kotlin",
        ".swift": "swift",
        ".sh": "bash",
        ".ps1": "powershell",
    }.get(PurePosixPath(path).suffix.lower(), "text")


def render_stage_artifact(name: str, artifact: dict, *, key_prefix: str, expanded: bool = False) -> None:
    with st.expander(stage_label(name), expanded=expanded):
        if name == "test_execution":
            render_qa_report(artifact, key_prefix=f"{key_prefix}-{name}")
            return
        artifact_downloads(name, artifact, key_prefix=f"{key_prefix}-{name}")
        render_json_ui(artifact, key_prefix=f"{key_prefix}-{name}-view")
        with st.expander("Raw JSON", expanded=False):
            st.json(artifact, expanded=False)


def render_qa_report(artifact: dict, *, key_prefix: str) -> None:
    qa = artifact["qa_report"]
    verdict_col, status_col, adapter_col, cases_col = st.columns(4)
    verdict_col.metric("QA verdict", qa["verdict"])
    status_col.metric("Execution", qa["execution_status"])
    adapter_col.metric("Runner", qa.get("adapter") or "n/a")
    cases_col.metric("Cases", ", ".join(f"{k}: {v}" for k, v in qa["case_status_counts"].items()) or "0")
    if qa["verdict"] == "INCONCLUSIVE":
        st.warning(
            "Inconclusive is never a pass: tests did not run, setup failed, or no test evidence was produced."
        )
    st.markdown("### Case results")
    st.dataframe(
        [{k: c[k] for k in ("title", "level", "actor", "priority", "status", "file")} for c in qa["cases"]],
        width="stretch",
        hide_index=True,
    )
    st.markdown("### Requirement coverage")
    st.dataframe(
        [{k: r[k] for k in ("state", "category", "actor", "text")} for r in qa["requirement_coverage"]],
        width="stretch",
        hide_index=True,
    )
    for failure in qa["failures"]:
        with st.expander(f"Failure: {failure['title']}"):
            st.code(failure["message"] or "(no message)", language="text")
    checks = qa.get("skill_checks", {})
    if checks:
        st.markdown("### Skill contract checks")
        st.dataframe(
            [
                {
                    "skill": c["skill"],
                    "check": name,
                    "result": "PASS" if c["passed"] else "FAIL",
                    "detail": c["output"][:120],
                }
                for name, c in checks.items()
            ],
            width="stretch",
            hide_index=True,
        )
    updates = qa.get("updates", {})
    st.markdown("### Files to update")
    if not any(updates.values()):
        st.success("Nothing to update.")
    if updates.get("generated_test_files_to_update"):
        st.caption("Generated test files")
        st.dataframe(
            [
                {
                    "file": u["file"],
                    "lines": ", ".join(map(str, u["lines"])) or "whole file",
                    "case": u["case"],
                    "class": u["classification"],
                    "what": u["detail"],
                }
                for u in updates["generated_test_files_to_update"]
            ],
            width="stretch",
            hide_index=True,
        )
    if updates.get("repository_files_to_review"):
        st.caption("Repository source files to review (file:line)")
        st.dataframe(
            [
                {
                    "file:line": f"{u['file']}:{u['line']}",
                    "case": u["case"],
                    "class": u["classification"],
                    "evidence": u["detail"],
                }
                for u in updates["repository_files_to_review"]
            ],
            width="stretch",
            hide_index=True,
        )
    if updates.get("requirements_needing_tests"):
        st.caption("Requirements that still need a passing test")
        st.dataframe(
            [
                {
                    "requirement": u["requirement"],
                    "state": u["state"],
                    "cover": ", ".join(u["source_files_to_cover"][:4]),
                }
                for u in updates["requirements_needing_tests"]
            ],
            width="stretch",
            hide_index=True,
        )
    for action in updates.get("environment_actions", []):
        st.warning(action["action"])
    st.download_button(
        "Download QA report (Markdown)",
        artifact["markdown"],
        file_name="qa_report.md",
        mime="text/markdown",
        key=f"{key_prefix}-qa-md",
    )
    st.download_button(
        "Download QA report (JSON)",
        json.dumps(qa, indent=2),
        file_name="qa_report.json",
        mime="application/json",
        key=f"{key_prefix}-qa-json",
    )


def render_generated_tests(artifact: dict, *, key_prefix: str, execution: dict | None = None) -> None:
    if not artifact:
        st.info("Generated test source is not available yet.")
        return

    col1, col2, col3, col4 = st.columns(4)
    validation = artifact.get("validation") or {}
    col1.metric("Generated files", validation.get("files", len(artifact.get("files", []))))
    col2.metric("Test declarations", validation.get("test_declarations", "—"))
    col3.metric("Covered cases", len(validation.get("covered_case_ids", [])))
    col4.metric("Execution", (execution or {}).get("qa_report", {}).get("execution_status", "Not run"))

    st.markdown("### Test case catalog")
    catalog = artifact.get("case_catalog") or []
    if catalog:
        st.dataframe(catalog, width="stretch", hide_index=True)
    else:
        st.caption("No case catalog available.")

    st.markdown("### Generated source")
    files = artifact.get("files") or []
    for index, item in enumerate(files, 1):
        path = item.get("path", f"generated-test-{index}")
        case_ids = item.get("case_ids") or []
        label = f"{path} · {', '.join(case_ids)}" if case_ids else path
        with st.expander(label, expanded=index == 1):
            st.download_button(
                f"Download {PurePosixPath(path).name}",
                item.get("content", ""),
                file_name=PurePosixPath(path).name,
                mime="text/plain",
                key=f"{key_prefix}-file-{index}",
            )
            st.code(item.get("content", ""), language=source_language(path), line_numbers=True)


def render_audit(harness: Harness) -> None:
    audit = harness.run_dir / "audit.jsonl"
    if not audit.exists():
        st.info("No audit records yet.")
        return
    raw = audit.read_text(encoding="utf-8")
    records: list[dict] = []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                records.append(item)
        except json.JSONDecodeError:
            continue
    if records:
        st.dataframe(records, width="stretch", hide_index=True)
    st.download_button(
        "Download approval audit",
        raw,
        file_name="audit.jsonl",
        mime="application/x-ndjson",
        key="download-audit",
    )


job = st.session_state.get("job")
busy = job is not None and not job.done()
harness = st.session_state.get("harness")

with st.sidebar:
    st.subheader("Model gateway")
    st.caption(f"Model: {MODEL}")
    st.caption(f"Base URL: {BASE_URL or 'not configured'}")
    st.caption("API key: configured" if API_KEY else "API key: not configured")
    daily = DailyUsageLedger().snapshot()
    st.caption(f"Daily token cap (all runs, resets at local midnight): {daily['limit']:,} (the only limit)")
    st.progress(
        min(1.0, daily["used"] / daily["limit"]),
        text=f"Used today: {daily['used']:,} · left: {daily['remaining']:,}",
    )

    st.divider()
    st.subheader("Agent personas")
    for workflow_stage in STAGES:
        persona = PERSONAS[workflow_stage]
        st.caption(f"{stage_label(workflow_stage)}: {persona.name}")

    st.divider()
    saved = sorted((p.parent.name for p in RUNS.glob("*/run.json")), reverse=True) if RUNS.exists() else []
    selected = st.selectbox("Saved run", saved, index=None, disabled=busy)
    if st.button("Resume selected run", disabled=busy or not selected, width="stretch"):
        try:
            validate_gateway_settings()
            close_runtime()
            runtime = MAFHarnessRuntime(
                api_key=API_KEY,
                model=MODEL,
                base_url=BASE_URL,
                skills_root=ROOT / "skills",
            )
            st.session_state.harness = Harness(
                RUNS / selected,
                skills_root=ROOT / "skills",
                runtime=runtime,
            )
            st.session_state.pop("job", None)
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    if st.button("New run", disabled=busy, width="stretch"):
        close_runtime()
        st.session_state.pop("harness", None)
        st.session_state.pop("job", None)
        st.session_state.pop("ui_error", None)
        st.rerun()

    if harness:
        st.divider()
        st.caption(f"Run: {harness.run_dir.name}")
        budget = harness.state.get("budget", {})
        st.metric("Tokens used by this run", f"{budget.get('reported_tokens', budget.get('charged', 0)):,}")
        st.caption(f"Provider calls: {budget.get('calls', 0)}")


if harness is None:
    st.markdown("### Start a repository review")
    st.write(
        "Provide only the public GitHub repository URL. The harness infers requirements, frameworks, test levels, "
        "test directories, fixtures, mocks, and generation strategy from repository evidence."
    )
    with st.form("repository"):
        url = st.text_input(
            "Public GitHub repository URL",
            placeholder="https://github.com/owner/repository",
        )
        app_url = st.text_input(
            "Running application URL (optional)",
            placeholder="http://localhost:8080",
            help="Local URL of the started application. Enables UI/E2E/accessibility cases; tests read it as BASE_URL.",
        )
        start = st.form_submit_button("Start autonomous review", type="primary")
    if start:
        try:
            from maf_test_harness.repo_analyzer import validate_github_url

            validate_github_url(url)
            validate_gateway_settings()
            runtime = MAFHarnessRuntime(
                api_key=API_KEY,
                model=MODEL,
                base_url=BASE_URL,
                skills_root=ROOT / "skills",
            )
            harness = Harness(
                RUNS / uuid4().hex[:12],
                skills_root=ROOT / "skills",
                runtime=runtime,
            )
            st.session_state.harness = harness
            dispatch(harness.start(url, app_url.strip() or None))
        except Exception as exc:
            st.error(str(exc))
    st.stop()


@st.fragment(run_every=1 if busy else None)
def job_status() -> None:
    current = st.session_state.get("job")
    if current is None:
        return
    if current.done():
        try:
            current.result()
        except Exception as exc:
            st.session_state.ui_error = str(exc)
        del st.session_state.job
        st.rerun()
    st.info("Agent is working. The next action will be a human review gate.")


job_status()
if busy:
    st.stop()
if st.session_state.get("ui_error"):
    st.error(st.session_state.ui_error)

view = harness.view
pending = view.get("pending")
state = harness.state
current_stage = state.get("active_stage") if view.get("status") == "interrupted" else None
current_stage = current_stage or (pending["stage"] if pending else view.get("status", "new"))

status_col, stage_col, calls_col = st.columns(3)
status_col.metric("Status", stage_label(view.get("status", "new")))
stage_col.metric("Current stage", stage_label(current_stage))
calls_col.metric("Provider calls", state.get("budget", {}).get("calls", 0))

if view.get("status") == "complete":
    zip_path = Path(state["deliverable_zip"]) if state.get("deliverable_zip") else None
    if zip_path and zip_path.is_file():
        st.download_button(
            "Download repository + generated tests + reports (ZIP)",
            zip_path.read_bytes(),
            file_name=zip_path.name,
            mime="application/zip",
            key="download-deliverable",
            type="primary",
        )
    st.success(state.get("completed_reason") or "Workflow complete.")
elif view.get("status") == "stopped":
    st.warning("Workflow stopped. Approved artifacts remain available for download.")
elif view.get("status") == "interrupted" or (pending and pending.get("id") in view.get("consumed", [])):
    st.error(
        "An operation was interrupted after approval. Automatic replay is blocked. "
        f"Recorded error: {view.get('error') or 'none'}. Inspect the audit and approved artifacts, then start a "
        "new run if necessary."
    )

review_tab, artifacts_tab, tests_tab, audit_tab = st.tabs(["Review", "Artifacts", "Generated Tests", "Audit"])

with review_tab:
    if view.get("status") in {"complete", "stopped", "interrupted"}:
        st.info("There is no active approval request for this run.")
    elif not pending:
        st.info("No pending request.")
    else:
        st.subheader(pending["title"])
        if pending.get("error"):
            st.error(pending["error"])

        kind = pending["kind"]
        details = pending["details"]
        request_id = pending["id"]

        def respond(payload: dict) -> None:
            dispatch(harness.respond(request_id, payload))

        if kind == "review":
            if details.get("role"):
                st.caption(f"{details['role']} · {details.get('goal', '')}")
            artifact = details["artifact"]
            if pending["stage"] == "test_execution":
                render_qa_report(artifact, key_prefix=f"pending-{request_id}")
            else:
                artifact_downloads(
                    pending["stage"],
                    artifact,
                    key_prefix=f"pending-{request_id}",
                )
                render_json_ui(artifact, key_prefix=f"pending-{request_id}-view")
                with st.expander("Raw JSON", expanded=False):
                    st.json(artifact, expanded=False)

            feedback = st.text_area(
                "Modification instructions",
                placeholder="Optional: describe the changes required before approval.",
                key=f"feedback-{request_id}",
            )
            approve_col, revise_col, stop_col = st.columns(3)
            if approve_col.button(
                "Approve and proceed",
                type="primary",
                key=request_id + "-approve",
                width="stretch",
            ):
                respond({"decision": "approve"})
            if revise_col.button(
                {
                    "test_execution": "Regenerate failing tests",
                    "environment_setup": "Re-check environment",
                }.get(pending["stage"], "Revise this plan"),
                key=request_id + "-revise",
                width="stretch",
            ):
                respond({"decision": "revise", "feedback": feedback})
            if stop_col.button(
                "Stop workflow",
                key=request_id + "-stop",
                width="stretch",
            ):
                respond({"decision": "stop"})

        elif kind == "tool":
            if pending["stage"] in {"test_execution", "environment_setup"}:
                st.warning(details.get("effects", ""))
                if pending["stage"] == "environment_setup":
                    st.markdown(f"**Isolation:** {details['isolation']}")
                    st.dataframe(
                        [
                            {
                                "tool": p["tool"],
                                "installed": p["present"],
                                "version": p["version"],
                                "how to install": p["install_hint"],
                            }
                            for p in details.get("prerequisites", [])
                        ],
                        width="stretch",
                        hide_index=True,
                    )
                st.code(
                    "\n".join(f"[{item['kind']}] {item['command']}" for item in details.get("steps", [])),
                    language="bash",
                )
            render_json_ui(
                {k: v for k, v in details.items() if k not in {"steps", "prerequisites"}},
                key_prefix=f"tool-{request_id}",
            )
            approve_col, deny_col, skip_col, stop_col = st.columns(4)
            if skip_col.button(
                "Skip",
                key=request_id + "-skip",
                width="stretch",
                disabled=pending["stage"] not in {"test_execution", "environment_setup"},
            ):
                respond({"decision": "skip"})
            if approve_col.button(
                {"test_execution": "Run the tests here", "environment_setup": "Install in isolated env"}.get(
                    pending["stage"], "Approve repository inspection"
                ),
                type="primary",
                key=request_id + "-approve",
                width="stretch",
            ):
                respond({"decision": "approve"})
            if deny_col.button("Deny", key=request_id + "-deny", width="stretch"):
                respond({"decision": "deny"})
            if stop_col.button("Stop workflow", key=request_id + "-stop", width="stretch"):
                respond({"decision": "stop"})

        elif kind == "error":
            if not pending.get("error"):  # the error is already shown above the request
                st.error(details.get("message") or "Stage failed.")
            retry_col, stop_col = st.columns(2)
            if retry_col.button("Retry stage", key=request_id + "-retry", width="stretch"):
                respond({"decision": "retry"})
            if stop_col.button("Stop workflow", key=request_id + "-stop", width="stretch"):
                respond({"decision": "stop"})
        else:
            st.error(f"Unsupported pending request type: {kind}")

with artifacts_tab:
    artifacts = state.get("artifacts", {})
    if not artifacts:
        st.info("No approved/generated artifacts yet.")
    else:
        for workflow_stage in STAGES:
            artifact = artifacts.get(workflow_stage)
            if artifact is not None:
                render_stage_artifact(
                    workflow_stage,
                    artifact,
                    key_prefix="artifact",
                    expanded=workflow_stage == current_stage or view.get("status") == "complete",
                )

with tests_tab:
    render_generated_tests(
        state.get("artifacts", {}).get("test_generation"),
        key_prefix="generated",
        execution=state.get("artifacts", {}).get("test_execution"),
    )

with audit_tab:
    st.subheader("MAF and skill provenance")
    render_json_ui(
        {
            "runtime": state.get("runtime_provenance", {}),
            "skills": state.get("skill_provenance", {}),
            "skill_tool_trace": state.get("skill_tool_trace", {}),
            "invocations": state.get("invocations", []),
        },
        key_prefix="provenance",
    )
    render_audit(harness)

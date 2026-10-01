# Backend correction and cleanup plan — approval required

No source files have been deleted. This plan proposes changes; it does not apply backend fixes. The authorized UI theme/skill-page changes are separate. The original B01–B12 findings remain in [the backend review](BACKEND_REVIEW_2026-10-01.md), with B06 partially implemented under the later reporting request.

## What actually failed in TokenTelemetry

Saved run `94b999567172`, target commit `276f37789304ca6d61a3923301f141c13ccd5a1b`: **9 passed, 26 setup errors, 1 assertion failure**. These counts describe generated Python cases, not the independent React browser suite (**14 passed, 1 failed**, confirmed mobile dashboard overflow).

| Saved result | Count | Evidence and cause |
|---|---:|---|
| SQLite database locked during setup | 21 | Generated modules assign global environment values during import; the target connection module captures `DEFAULT_DB` on its first import. Tests reuse that captured database. Failed seeding fixtures have no guaranteed rollback/close, leaving a transaction locked. A disposable two-connection reproduction demonstrates the cascade. |
| Duplicate `usage` transcript key | 2 | `test_timeline_daily_aggregated.py` inserts two days with the same `(transcript_path, transcript_line)`, violating the real unique constraint even in isolation. Another generated fixture collides in the shared database. |
| Duplicate `tool_calls` key | 1 | Reused `(transcript_path, transcript_line, tool_use_id)` fixture identity violates the actual unique constraint. |
| Nonexistent `attributions.reference_count` column | 2 | The generated INSERT invents database fields; `reference_count` is absent from the actual table schema. Generated attribution data also omits the required usage relationship. |
| Reconciliation assertion `0 >= 1` | 1 | Generated transcript puts `usage` outside `message`. The implementation reads `message.usage` and computes total tokens from its component counts. The authored total of 42 contradicts 10 input + 5 output. A source-contract reproduction returns 0 for the authored fixture, 15 for the supported nesting. |

Removing database locks alone will not make the suite correct. An isolated rerun of `test_usage_summary_global_statistics.py` reaches a new failure: it asserts `total_cost`, which the actual response does not contain. The live endpoint implementation must supply the source of expected fields; a requirement title is insufficient.

Four saved tests were rerun unchanged, individually, with a sanitized subprocess environment and disposable telemetry state. Timeline and hotspot setup errors persisted; reconciliation still failed; summary exposed its incorrect response key. Original run/checkpoint/verdict and target code were not edited. Diagnostic logs are preserved locally under the `maf-isolated-cases-*` temporary directory.

Reproduce the database/schema/transcript/tool findings without user telemetry:

```text
.venv/bin/python docs/audit/token_failure_repro.py /path/to/saved/run/workspace
```

## Why the harness accepted these tests

- The agent trace shows six `load_skill` calls and **zero central code-checking tool calls**. Loading a skill establishes availability, not factual correctness or actual tool use.
- Host validation checks syntax/imports/routes and collection. Python build checks run each file separately with `pytest --collect-only`, so they do not execute fixtures or detect combined import-time configuration contamination.
- Generation batches lack a reliable shared fixture contract covering database initialization, uniqueness, foreign keys, teardown and source-backed payloads. A collected test can still seed invalid rows or assert invented JSON fields.
- The selected runner covers Python only. The React/FastAPI application had to be checked independently; current generated coverage is not a complete repository qualification.

## Additional verified findings

| ID | Before | Proposed change after approval | Acceptance proof |
|---|---|---|---|
| B13: source-backed fixtures and combined validation | Invalid SQL keys/columns, global DB environment mutation and unsupported JSON payloads pass collection. | Extend existing code-intelligence/prompt assembly with bounded schema, constraints, response and configuration facts from the cited source. Provide a shared fixture contract: configuration before imports, disposable data, unique fixture identities, valid relationships and guaranteed connection cleanup. Reject source-demonstrably invalid fixtures; run combined collection and a separately approved fixture/execution check. Unsupported dynamic schemas remain an explicit gap. | Reproductions above are caught or regenerated correctly; two modules cannot silently share import-captured state; exception teardown releases locks; valid source-defined API/transcript fixtures retain meaningful assertions. |
| B14: tool outcome trace is inaccurate | `ToolTraceMiddleware` marks success unless text begins `Error`; a typed JSON `{ "ok": false }` is logged as successful. | Reuse the existing typed `ToolResult` envelope for outcome/code/category; handle MAF text Content and ordinary tool failures. Keep unknown/unparseable outcomes visibly unknown, without changing tool permissions. | JSON false, denied hooks, tool exceptions and valid successes are distinguished in traces/observability. |
| B15: tool output truncation breaks JSON | `ToolResult.to_json()` truncates a still-oversized serialized envelope at 6,000 characters. Long recommendations reproduce invalid JSON. | Bound fields/list entries before serialization and emit an explicit valid truncated envelope; reuse the existing result contract. | Parseable output within the declared bound for oversized Unicode/evidence/recommendations; original outcome and error code remain available. |

These defects are in the harness or generated tests. They do not justify changing TokenTelemetry’s production schema/parser to accommodate invalid tests. The independent responsive overflow remains a separately confirmed target defect.

## Framework documentation comparison

Reviewed the supplied local `agent-framework.pdf` (968 pages), Microsoft’s Python overview, Context7 `/microsoft/agent-framework` and the installed SDK APIs. The PDF contains substantial .NET examples and multiple release notes: those are not Python migration instructions.

Installed versions are `agent-framework-core==1.18.0` and `agent-framework-openai==1.14.3`. Existing `create_harness_agent`, list-based middleware, SkillsProvider progressive loading/read-only approval, Pydantic `response_format`, typed `result.value` and `AgentSession.to_dict/from_dict` are present in those versions and align with the relevant Python APIs. No SDK upgrade or replacement of the seven-stage workflow is proposed without a demonstrated compatibility defect.

The host must validate domain facts and fixture semantics itself; structured JSON output validates shape, not correctness. Ordinary framework tool failures can be returned to the model, so outcome traces must inspect the actual result rather than assume a successful middleware return means a successful tool.

Sources:

- [Microsoft Python overview](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python)
- [Python structured output sample](https://github.com/microsoft/agent-framework/blob/main/python/samples/02-agents/providers/openai/client_with_structured_output.py)
- [Python skill provider implementation](https://github.com/microsoft/agent-framework/blob/main/python/packages/core/agent_framework/_skills.py)
- [Python middleware implementation](https://github.com/microsoft/agent-framework/blob/main/python/packages/core/agent_framework/_middleware.py)

Context7 tracks current source; implementation changes will be checked against the pinned installed versions. The PDF remains untouched locally and is not uploaded as part of this UI commit.

## Implementation order after approval

| Step | Changes | Expected result |
|---|---|---|
| 1 | B01–B03, B07, B14–B15 | Honest process/case/coverage verdicts, contained evidence paths, accurate tool outcomes and valid tool JSON. Add focused regression proofs before changing implementations. |
| 2 | B13 plus existing source/fixture tooling | Evidence-backed generation and fixture isolation. Diagnose/regenerate invalid tests instead of relaxing assertions or silently patching target application behavior. |
| 3 | B04–B05, B08–B10 | Explicit component install/run plans, correct interpreter/runtime constraints and approved multi-service startup/readiness. Show every command and component selection before execution. |
| 4 | Remaining B06, B11–B12 | Attempt-scoped browser reports/screenshots/traces and logs included in portable deliverables; truthful downloadable QA locations and OS-specific replay instructions. |
| 5 | Regression and CI | Full Python, lint/skill integrity, Next build and browser journeys; macOS/Windows GitHub workflows. Retain genuine failing target assertions. |
| 6 | Fresh TokenTelemetry cycle | Record target commit; use the documented React/FastAPI app with disposable telemetry state. Generate and execute approved backend/browser cases; show coverage gaps explicitly, classify test/environment/app failures, and verify report screenshots/ZIP/trace links without credentials. Add CLI/alternate UI coverage only if explicitly selected. |

## Exact deletion candidates — nothing deleted

These are retired GUI artifacts on `new-ui`, not universally dead files. They may be removed **only if specifically approved**:

1. `app.py` — retained legacy GUI entry point; no longer installed/launched on this branch.
2. `src/maf_test_harness/monitoring/ui.py` — legacy GUI observability renderer, used only by the retired app and two legacy tests.
3. `tests/targets/test_harness_browser.py` — retired GUI-only browser check, superseded for this branch by Next.js Playwright journeys.

Associated edits after deletion approval: remove only the two legacy AppTest functions in `tests/test_automation_report.py`, the legacy-only `render_json_ui`/`artifact_downloads` functions in shared `ui/artifact_presenter.py`, and their direct documentation/lint references. Retain those shared files and every active Markdown export/API helper. Remove the confirmed uncalled `agent/diagnosis.py::as_json` helper/import, retaining the diagnosis module.

Optional historical artifacts, separate approval: `agentic-testing-harness-v5-fixed.html` and `design/concept.html`. They remain useful design/approval records; removal is not necessary for operation. Keep the supplied PDF, skill packages/lock, workflow, CLI, target-framework support, reports and local run evidence.

Approval choices: (A) implement B01–B15 and run the fresh React/FastAPI cycle; (B) delete the three retired GUI files and only the associated symbols/references listed above; (C) optionally remove the two historical HTML files. Each choice is independent. Deletion does not follow automatically from approval of backend fixes.

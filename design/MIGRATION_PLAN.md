# Next.js UI migration — approval draft

Status: approved and implemented. Local verification is documented in `../docs/UI_VERIFICATION.md`. The original concept remains for design comparison.

## Confirmed scope
- Internal team; preserve current features only.
- Use `agentic-testing-harness-v5-fixed.html` for visual ideas.
- Plan and `concept.html` approved before implementation. Added adjustable panels, saved layout preferences and per-run expandable audit/observability as requested.
- Confirmed: run locally for each teammate, with separate per-user history. Use each operating-system user’s local run storage; no shared history service in this phase.
- Approved: thin Python HTTP adapter. No login/SSO in this local-only phase; services bind to loopback and enforce local-origin requests.

## Current system and proposed boundary
`app.py` calls `Harness.start(repo_url, app_url, start_app)` and `Harness.respond(request_id, response)` through `RuntimeService`. The Python MAF workflow owns seven stages, decisions, hashes, checkpoint storage, hooks, retries, generation, execution and packaging. `run.json`, checkpoints, `audit.jsonl`, `trace.jsonl` and stage artifacts live in `.harness_runs`. `DailyUsageLedger` owns the shared daily token cap. `monitoring.report.build_report` already computes observability.

Proposal: Next.js/TypeScript frontend → thin Python HTTP adapter → existing Harness/runtime/report functions. No model or test orchestration in JavaScript. Keep CLI and Streamlit usable during migration. Reuse existing artifact Markdown export functions. API responses expose presentation data, never API keys, model sessions or arbitrary host paths. Allow-listed downloads must remain inside the selected run directory. Serialize mutations per run; preserve stale/consumed request rejection and artifact hashes. Do not hold long-running work inside a Next.js request. Poll operation/run state initially, matching current UI behavior; disable duplicate actions while work is running.

For this local version, the adapter owns operations independently of browser sessions and uses per-OS-user run storage. Keep existing project-local runs discoverable for compatibility without copying another user’s data. Runtime lifecycle and restart handling must preserve consumed approvals. Existing filesystem locks do not substitute for team identity or authorization. No new database, queue, SSO, private-repository integration or container platform is assumed.

## Screen-by-screen user journey and parity
| View | User sees and can do | Existing source |
|---|---|---|
| Runs | Saved runs, stage/status, open/resume, new run, empty/error states | app sidebar; run.json |
| New run | Public GitHub URL including supported ref, optional local app URL, start-app checkbox, gateway validation | Harness.start; analyzer.validate_github_url |
| Run / Review | Seven-stage progress, pending clone/install/run approval, complete command/effect/toolchain detail, human review/revise feedback, deny/skip/stop/retry as supported | Harness.view; Harness.respond |
| Run / Artifacts | Analysis, requirements/actors/contracts, environment evidence, plan, cases/data/gaps, generation, execution; complete nested detail, raw JSON and Markdown/JSON downloads | state.artifacts; artifact_presenter |
| Run / Generated tests | Catalog, case IDs, validation counts, source paths/code and per-file downloads | test_generation artifact |
| Run / QA report | PASS/PASS_WITH_SKIPS/FAIL/INCONCLUSIVE, case results, coverage, failures, skill checks, exact files/lines to update, environment actions, regenerate failing tests only at the valid gate (≤3 rounds), report exports, final ZIP | qa.report; workflow.heal; packaging |
| Run / Observability | Overview/stage tokens/cache/model runs; skills and hashes; tools with stage/category/failure filters; hooks/recovery/diagnosis; timeline/lane filters; requirement-case-file-result chain; integrity/telemetry; exports | monitoring.report; monitoring.ui |
| Run / Audit | Runtime/skill provenance, tool trace, invocations, approval events, audit download | app.render_audit |
| Configuration | Read-only gateway model/base URL/key-configured flag, token cap/used/remaining, run tokens/calls, personas; appearance | settings; DailyUsageLedger; PERSONAS |

Global states: waiting, busy, no artifacts, invalid input, missing gateway configuration, provider quota, incomplete environment, blocked hooks, stale gate, stopped, interrupted, reconnect and damaged observability data. Stop acts through existing pending-gate decisions; immediate process cancellation is not invented. Reconnection never silently replays consumed approvals.

## Visual proposal
Use the existing HTML's neutral light surfaces, dark primary button, compact sidebar and low-density tables. Runs replaces redundant dashboard/repositories pages. A single run workspace uses tabs; full details remain reachable. Native controls, keyboard navigation, visible focus, labelled inputs, responsive overflow and status text accompanying color. No simulated chat or unsupported GitHub integration. Concept uses explicitly labelled sample data and simulated controls only.

## Backend limitations to communicate
Public repositories only; app URL must be localhost/127.0.0.1 on the execution host. Supported runner/toolchain paths depend on detected stack and installed tools: universal “any repo works” is not a current guarantee. Isolated dependencies are not a security sandbox; approved commands execute as the host user. UI migration cannot alone make untrusted multi-user execution safe. No private repo OAuth, user accounts, roles or organization audit identity exists today.

## Implementation sequence AFTER approval
1. Capture baseline Python checks after `uv sync --extra dev`; inventory every rendered artifact field and response decision. Pin the approved access/deployment decision.
2. Add the smallest HTTP adapter and operation lifecycle around current workflow; validate input, request IDs, action availability, download scope, secret redaction and agreed team access. Add focused adapter checks.
3. Build Next.js shell, runs/new-run, run progress and approval views. Connect to real persisted state; retain all backend gates.
4. Implement artifacts, generated source, QA, ZIP, all observability views and audit; preserve nested JSON fallback so no existing fields disappear.
5. Run Python regression suite, lint/format, skill integrity checks; frontend typecheck/lint/build and browser journey checks. Test valid and stale approvals, revise/deny/skip/retry/stop, interruption/reconnect, error states and downloaded evidence.
6. Local real-repository run with complete installed requirements and configured model credentials: clone approval → analysis → requirements → installation → plan → design → source → execution → QA → ZIP. Record commands/results and verify provenance. A mocked workflow check must be labelled separately from a live gateway run.

## Acceptance and rollback
Every existing user action and displayed artifact is mapped above and verified in browser checks. No weaker approval, environment, secret or generation guardrail. Saved compatible runs remain readable and resumable at valid pending gates. FAIL/INCONCLUSIVE never appears as a pass. Browser reload cannot repeat tool execution. Frontend build and backend checks pass; real local E2E evidence recorded, with any credential/toolchain blockers stated.

Rollback: stop the new UI/API and use the retained Streamlit/CLI with the same run directories. Do not rewrite checkpoint formats or remove Streamlit in this phase. Keep new presentation-only preferences out of workflow state.

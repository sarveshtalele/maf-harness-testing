# TokenTelemetry verification — 2 October 2026

Target: [`sarveshtalele/tokentelemetry`](https://github.com/sarveshtalele/tokentelemetry), commit `276f37789304ca6d61a3923301f141c13ccd5a1b`.

Harness run: `426265c41efa`. The selected application is the React frontend in `frontend/`, backed by FastAPI. Target production source was not changed.

## Result scope

The independent application browser verification completed: **25 checks, 20 passed, 5 failed, zero skipped and zero flaky**. Its five failures are reproducible mobile layout defects in the target application.

The main agent workflow has approved requirements, installed component-specific dependencies and approved a 34-case design containing three React end-to-end cases. Generated-case execution completed with verdict **FAIL**: **12 passed, nine failed and 13 setup errors**, covering all 34 approved cases. The independent browser results below are supplemental evidence; they are not represented as completed agent-generated QA results.

## Real application browser verification

The final native Playwright report is:

`browser-reports/full-application-validation-attempt2/playwright-report/index.html`

This directory is inside the local harness run. The UI evidence ID is `a18a648bb6d2d85bf98280b9`. The report contains 52 PNG screenshots and 25 full Playwright traces, with JUnit and JSON results alongside the native HTML report.

The real Vite frontend and FastAPI backend ran against a new disposable SQLite database. HTTP responses were not mocked. Fixture data comprised one deterministic usage row, one skill event and one MCP tool call. Each proof attempt used a separate database and configuration directory, and child processes did not inherit model credentials. Services stopped after the checks.

| Coverage | Verified behavior |
| --- | --- |
| All eleven top-level views | Source-defined headings and visible fixture data, or explicitly labelled static content |
| Project details | Actual project navigation, summary, requests, sessions and attribution empty state |
| Request details | Drawer and separate full prompt/response page; together with project details this covers all thirteen router paths |
| API | Health/version, visible seeded usage and missing-request 404 |
| Search | Session/project filtering, empty results and restored fixture rows |
| MCP and plugins | Actual MCP fixture plus explicit plugin/hook empty states |
| Exports | Downloaded CSV and JSON contents parsed/checked against fixture data |
| Theme | Dark mode persisted through reload |
| Mobile | Screenshots and strict document-width checks at 390 × 844 |

The static About page is labelled static content. Plugin and hook empty states are not described as populated-data coverage.

### Target failures retained in the report

| View | Viewport width | Actual document width | Result |
| --- | ---: | ---: | --- |
| Dashboard `/` | 390 px | 452 px | Horizontal overflow |
| Projects `/projects` | 390 px | 464 px | Horizontal overflow |
| Requests `/requests` | 390 px | 472 px | Horizontal overflow |
| Reports `/reports` | 390 px | 458 px | Horizontal overflow |
| Settings `/settings` | 390 px | 477 px | Horizontal overflow |

These are target application defects, not database-lock or generated-fixture failures. The target uses a fixed-width sidebar, unwrapped header controls, generous main padding and fixed/minimum-width controls or grids. Responsive sidebar/header controls and content sizing should be corrected in the target repository, then these same strict assertions rerun. No assertions were weakened and no target production changes were made.

### Report viewer validation

The final report was loaded through the UI evidence endpoint with its `/view.html` asset base and inside the actual Next.js sandboxed Test reports iframe. Test rows, failure details and the attached viewport geometry rendered with **zero JavaScript errors and zero failed asset requests**.

Viewer screenshots are saved alongside the report as `report-ui.png`, `report-failure-ui.png`, `harness-report-ui.png` and `harness-report-failure-ui.png`.

### Superseded proof attempt

The first application proof produced 19 passes and six failures. One was an incorrect test expectation: the actual source-defined About heading is `About Claude Telemetry Enterprise`, rather than `About`. That expectation was corrected to match source and the entire 25-check suite rerun into the distinct attempt-2 directory. The five mobile failures remained. Original evidence was retained and must not be presented as the final qualification result.

## Main workflow investigation

The first test-design attempts omitted browser cases despite the selected React application and installed browser tooling. The host correctly rejected API-only qualification. An explicit, saved per-run design prompt retained the schema, approved source IDs and relevant React evidence, and required three meaningful browser cases. The resulting approved design contains 34 cases: 12 positive, six negative, three boundary, six validation and seven contract cases.

An early generation attempt placed Node specs at the repository root while Playwright was installed in the frontend package. Collection therefore failed before test execution. The shared harness correction validates component ownership and directs frontend specs into `frontend/tests/`. A second correction supplies selected React source to browser generation when an abstract case target cannot resolve a concrete source file. Accepted generation checkpoints are preserved.

The generated browser cases require populated project/request data. These preconditions will be inspected before execution; any host-prepared disposable fixture will be recorded separately from generated fixture code. Missing fixtures or guessed assertions must not be reported as target product defects.

## Limits

The supplemental browser proof does not qualify daemon/background ingestion, CLI installation/autostart, live event delivery, collector reconciliation, large data volumes or populated plugin/hook behavior. Those capabilities require their own source-backed cases and execution evidence. The completed generated workflow is not an application pass; the independently verified target mobile defects and generated-fixture defects remain separately identified below.

## Completed generated execution and remaining accuracy gaps

All 34 approved cases executed after host preparation of disposable, source-schema browser data (one usage row, skill event and MCP tool call for `test-project`). The fixture setup is recorded separately in the run audit; generated source was not rewritten and target production source was not changed. Component-native execution produced Python JUnit plus a native Playwright report: the three generated browser cases had **one pass and two failures**.

Final mapped QA: **12 passed, nine failed, 13 errors; FAIL**. One browser failure is the genuine dashboard overflow; the other uses an ambiguous `text=Projects` selector matching multiple source-rendered elements, a generated-test defect. The 20 unsuccessful backend cases are generated-fixture/assertion defects: improperly decorated async fixtures, an invented `DEFAULT_DB._value` attribute, iterating the schema string character by character, recreating an existing table, missing required `connect(db_path)` arguments, and overly strict CSV content-type equality rejecting the valid UTF-8 charset. These are not evidence of 20 application defects. Static collection cannot prove fixture/runtime correctness; source-contract guidance reduces but does not eliminate model mistakes. Regeneration should fix those authored fixtures/selectors while preserving the proven overflow assertion.

Native Playwright JUnit emitted component-relative filenames, initially leaving its three cases unmapped. The shared mapper now accepts a unique report filename after full-path/module matching and refuses ambiguous duplicate filenames. The QA was reconciled from the unchanged raw execution evidence; the original QA, run view and approved execution artifact were preserved in `qa-before-playwright-identity-reconciliation/`, with a reconciliation audit event. No tests were rerun or marked passing by changing assertions.

The rebuilt deliverable contains generated source, prompt settings and immutable request history, portable `rerun.py`, audit/QA data, and native report assets including the separate 25-check full-application proof and its screenshots/traces. The delegated agent completed generation and supplemental proof but hit its Codex usage limit before execution; the primary agent completed the approved execution and evidence reconciliation. No success is claimed for unsupported ingestion/daemon/CLI capabilities.

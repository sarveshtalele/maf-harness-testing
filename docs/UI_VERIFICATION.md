# Next.js migration verification — 1 October 2026

## Implemented

Next.js 16.3.8 / React 19.3.0 frontend, backed by the existing Python Harness through a loopback HTTP adapter. No workflow, checkpoint, runner, hook, environment or model-generation algorithm was changed. Streamlit and CLI remain usable.

All seven workflow stages and existing review/tool/error decisions are represented. UI includes saved runs, repo/app input, artifact detail and exports, source downloads, QA verdict/coverage/failure/file-update evidence, ZIP, all observability sections and approval/provenance audit. Panels resize in height and width, collapse and expand into accessible dialogs. Sidebar and workspace split are adjustable. Layout preferences persist in the browser for each run/panel.

## Local evidence

Host: macOS arm64, Python 3.12.14, Node 24.21.0.

| Check | Result |
|---|---|
| Python regression suite (`pytest -q --ignore=tests/targets`) | 722 passed, 1 existing skip |
| Launcher occupied-port check (added after the full suite) | 1 passed; existing service remains reachable |
| Ruff lint and formatting | Passed |
| Skill lock/spec checks | All 10 skills and lock passed |
| TypeScript check | Passed |
| Next.js optimized production build | Passed |
| Chromium complete-workflow journey | Passed |
| Chromium deny/retry/revise/stop/error journey | Passed |
| Production launcher initial startup and restart | Passed on macOS; UI and API listen on loopback |

Browser checks use the real HTTP adapter, checkpointed MAF workflow, validators, pytest subprocess, QA generation and ZIP packaging. Model replies, clone contents and install plan are fixture inputs, explicitly not live model/GitHub qualification. Checks cover stale approval rejection, required revision feedback, secret exclusion, reload/resume, per-run source/ZIP downloads, every observability tab, expanded dialogs, audit records, 390px responsive layout, hostile-origin rejection and persisted sidebar/panel sizes.

Adapter checks also cover path traversal and out-of-run ZIP refusal, secret redaction, consumed-gate interruption without replay, explicit environment skip, reconnection, fresh runtime restore, failed reconnect cleanup, bearer authorization and direct browser-origin rejection.

[Audit screenshot](ui/audit-desktop.png) is from the fixture workflow, not the selected live target.

## Live target qualification

Credentials were supplied and stored only in the git-ignored `.env` with file mode 0600. The app uses the configured OpenAI-compatible proxy and `deepseek-v4-flash-global`. A live gateway completion returned HTTP 200. No credential value is included in this report.

Live production Next.js/API run: `887fea4697c4`, target `https://github.com/agoncal/agoncal-application-petstore-ee7`. Repository analysis, live requirements/planning/design, dependency installation and application packaging completed. Java 8 has no Temurin macOS ARM build; a checksum-verified cached JDK 11 satisfied the declared minimum. Maven 3.9.9 resolved the project's dependencies. WildFly 26.1.3 was downloaded from its official release, upstream checksum verified, started on loopback and deployed with the unmodified target WAR.

The existing real Petstore Chromium/API acceptance checks returned **7 passed, 1 failed**. Four seeded catalog endpoints, anonymous browsing, invalid-user rejection and login/cart add/remove passed. A known user with an incorrect password was authenticated; a fresh browser session reproduced it. Assertions were retained. Evidence: `.targets/petstore-browser/results.xml` and [wrong-password screenshot](ui/petstore-wrong-password.png).

Production Chromium checks against the live run passed audit/observability fullscreen expansion, Escape close, 390px layout without horizontal overflow, no browser errors and credential exclusion. [Live audit screenshot](ui/live-audit-desktop.png). Run evidence and browser bundles were scanned for the actual configured key and had no matches. The full workflow completed with **41 generated cases: 21 passed, 10 failed, 10 errors, 0 skipped**. QA verdict is **FAIL**. All 14 generation batches passed validation after retries for multiple declarations and Arquillian container use. Existing assertions and backend algorithms were retained.

Ten validation tests errored because the generated standalone JUnit suite has no Bean Validation provider on its test classpath. Ten other tests failed on category updates, stale-version responses and item/product/customer HTTP 500 responses; these mix generated-input/expectation issues and possible target defects, and are not all classified as application bugs. The independent wrong-password browser failure was reproduced separately.

Final `verify-run` passed MAF/SkillsProvider/loading/provenance/lock checks (36 model invocations). QA JSON/Markdown, audit, observability, configuration, all 41 Java source exports and ZIP were downloaded through the production Next.js proxy and verified. The ZIP passed CRC validation and contains the FAIL QA report under `harness-report/`. A final scan of 1,020 run-evidence/frontend files plus every expanded ZIP entry/export found zero configured-key matches. Completed-run Chromium checks passed FAIL verdict display, Java source, reload and ZIP download. [Live QA screenshot](ui/live-qa-desktop.png).

Run evidence remains in `/Users/sarveshtalele/.maf-harness/runs/887fea4697c4`; acceptance XML and exported QA are in ignored `.targets/petstore-browser/`. Disposable WildFly was stopped after verification. The production harness remains available at http://127.0.0.1:3000/?run=887fea4697c4. No all-pass target result is claimed.

Windows-compatible process, npm.cmd, path and per-user storage handling is implemented. `.github/workflows/ui.yml` contains macOS and Windows adapter/platform/launcher/build/browser checks. That CI has not been run from this local checkout, and no actual Windows execution is claimed.

## Reproduce

See [NEXTJS_UI.md](NEXTJS_UI.md) for both OS startup and verification commands. Local working app: http://127.0.0.1:3000. Model settings require a launcher restart after `.env` changes.

## Follow-up UI clarification — mouse resizing and project skills

Explicit mouse handles now resize sidebar width, workspace columns, and panel width/height. Keyboard arrows, alternate sliders, fullscreen dialogs, responsive stacking and persisted preferences remain available. The Agent skills page reads all ten project packages with search, expandable instructions/resources/metadata and integrity/provenance details. Screenshot: [project skill cards](ui/agent-skills-desktop.png).

Follow-up verification: optimized build/TypeScript passed; focused adapter suite **6 passed**; browser journeys **3 passed**, including exact mouse drag distances and reload persistence; production skills page/mobile checks passed. Full backend regression before the new catalog test: **723 passed, 1 skipped**. Backend audit findings and proposed fixes are recorded in [BACKEND_REVIEW_2026-10-01.md](BACKEND_REVIEW_2026-10-01.md); workflow fixes and the next TokenTelemetry live cycle await the user's explicit approval.

## Setup, reports and usability update

- Primary setup documentation now describes `new-ui` and Next.js. Added `start-macos.command` and `start-windows.cmd`, preserving `.env`, installing locked dependencies and launching both servers through the existing Python coordinator.
- Searchable/sortable tables with sticky headers and record dialogs; visible filter chips; QA failure cards show saved output and existing triage recommendations without claiming a proven cause.
- Dedicated Test reports and Logs views. Native Playwright HTML renders in an opaque sandbox with report-local storage, contained assets and secret-redacted embedded JSON. Screenshots display inline; traces download for the local viewer. Logs provide line numbers, error highlighting and search.
- Full Python regression: **725 passed, 1 skipped**. Final targeted evidence, hooks and runner checks: **64 passed** after report rendering changes. Final production Next build/type checking passed; **four Chromium journeys passed** after the report rendering and attachment fixes.
- Native Node Playwright adapter exercised with a deliberate assertion mismatch: expected failure, with HTML/JUnit, PNG screenshot and trace ZIP preserved under the execution attempt. This is a report-capture proof, not a target repository defect. The native HTML test detail and screenshot loaded inside the UI; QA guidance and logs were visible.
- Screenshots: [report viewer](ui/playwright-report-view.png), [failure cards](ui/qa-failure-view.png), [logs](ui/log-view.png).
- Prior GitHub Actions run `36902430992`: Windows passed; macOS timed out after 30 seconds during server startup, before any browser tests. Startup timeout is now 120 seconds with piped server diagnostics. CI publishes HTML/JUnit and browser artifacts on success or failure, and smoke-checks both platform scripts.
- No source files were deleted. Previously reviewed backend verdict/mapping/mixed-stack findings remain pending approval; Node browser report capture was authorized by the newer report request.

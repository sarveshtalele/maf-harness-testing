# TokenTelemetry live qualification — 1 October 2026

Target: https://github.com/sarveshtalele/tokentelemetry at `276f37789304ca6d61a3923301f141c13ccd5a1b`.

- Harness run: `94b999567172` completed analysis, requirements, environment, planning, design, generation, execution and QA, with a deliverable ZIP. Final generated-suite verdict: **FAIL — 9 passed, 26 errors, 1 failed** across 36 Python cases. The adapter selected Python; it did not generate a React browser suite.
- Recorded generated-test setup errors include duplicate SQLite transcript keys, locked databases and an INSERT referencing a nonexistent `reference_count` column. These are evidence of fixture/schema inaccuracies; they must not all be classified as application defects. Exact output and existing improvement recommendations are available in QA and Logs.
- Target environment installation completed; the documented FastAPI backend and React/Vite frontend ran on loopback with disposable seeded telemetry/configuration. No user telemetry database or CLI hooks were changed. Model credentials were excluded from target subprocess environments.
- Existing backend API/reconciliation tests: **20 passed**, with an unknown `base_url` pytest setting warning and a Starlette/httpx deprecation warning.
- Target React production build: passed, with a large-chunk warning.
- Independent Chromium acceptance: **14 passed / 1 failed** across eleven pages, health/catalog/missing-request API behavior, project navigation, full prompt/response popup, CSV/JSON downloads and a 390px dashboard check.
- Confirmed failure: horizontal overflow on the mobile dashboard. Preserve as a target defect; no target code was changed.

Local report: `.targets/tokentelemetry-live-evidence/browser/attempt2-html-report/index.html`. JUnit: `attempt2-junit.xml`. Screenshots and failure trace: `attempt2-results/`. Evidence directories are ignored and excluded from Git. An earlier test-authoring attempt used nonexistent heading/link selectors; its separate evidence is retained and is not counted as a target failure.

Backend gaps B01–B12 remain pending approval in [the backend review](../BACKEND_REVIEW_2026-10-01.md). The independent checks supplement the completed generated cycle; neither establishes complete CLI or alternate-frontend coverage. The browser evidence was copied into this run’s `browser-reports/react-attempt2` for the Next.js viewer after the ZIP was created, so those additional browser assets are not included in that ZIP.

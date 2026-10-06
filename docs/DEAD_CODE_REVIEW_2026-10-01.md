# Cleanup review — 1 October 2026

> Historical plan/evidence. Counts and earlier UI/setup descriptions are preserved as observations from that date. See [current branch behavior](PETSTORE_STRICT_TEST_GENERATION.md); current UI is Next.js and setup/runtime checks occur at Test execution.

Historical review status on 1 October: no files had been deleted. On 2 October the user explicitly approved the exact retired GUI cleanup described below; only that cleanup has now been performed. Further file deletion still requires explicit approval. The requested shared **Adjust panel** form and its CSS were removed; mouse drag handles, keyboard resizing and fullscreen remain.

## Review scope and limits

Reviewed Next.js routes/components, the Python API/launcher, workflow and stage gates, environment/runner/QA paths, CLI and Streamlit entry points, tests, scripts, documentation and skill loading. Used repository-wide reference searches and inspected dynamic callbacks, package entry points, skill locks and documented standalone commands. Static searches cannot prove that an external consumer never imports a public Python symbol.

## Confirmed unused code candidate

| File | Candidate | Evidence | Proposed action after approval |
|---|---|---|---|
| `src/maf_test_harness/agent/diagnosis.py` | `as_json` at line 96 and its sole-use `json` import | No callers found across source, scripts, tests or documentation; excluded from module `__all__`. The rest of this module is active. | Remove only the helper/import, retaining the file. |

**No entire production source file is proven dead.** There are consequently no production files recommended for deletion on the available evidence.

## Optional historical artifacts — keep until explicitly approved

| File | Why it is retained | Optional decision |
|---|---|---|
| `agentic-testing-harness-v5-fixed.html` | User-selected design reference; linked from migration plan. | Archive/remove only if the reference is no longer wanted. |
| `design/concept.html` | Approved design record. | Archive/remove only if approval history is no longer wanted. |

These are historical artifacts, not proven dead files.

## Files that must not be mistaken for dead code

- `app.py` and `src/maf_test_harness/monitoring/ui.py`: supported Streamlit entry point and observability, retained for compatibility.
- `src/maf_test_harness/ui/runtime_service.py` and `artifact_presenter.py`: shared UI/CLI functionality, including the new API.
- `scripts/json_to_markdown.py`: standalone artifact conversion command.
- `scripts/vendor_skills.py`: documented skill vendoring, review and lock maintenance.
- Skill resources/scripts and `SKILLS.lock.json`: runtime or reviewed maintenance dependencies; filenames alone do not establish use.
- `memory.md`, documentation, audit evidence and `patches/`: linked project history and qualification evidence.
- Empty `__init__.py` files: Python package markers.
- `.env`: private configuration, excluded from Git. Generated `.targets`, environments, dependency directories and build/test outputs are ignored local data, not dead source.

## End-to-end review

The harness UI’s three Chromium workflow/layout journeys passed after removing the shared form. Production build and type checking passed. Python regression baseline: 723 passed, one skipped; focused adapter checks: six passed. Windows execution remains unverified locally; the branch includes a Windows/macOS CI matrix.

Backend correctness findings and proposed fixes are recorded in [the backend review](BACKEND_REVIEW_2026-10-01.md), B01–B12. Those fixes remain unapplied pending the previously requested approval. In particular, mixed ecosystem execution and QA verdict accuracy prevent interpreting a green generated suite as proof of full repository coverage.

TokenTelemetry independent Playwright validation at commit `276f37789304ca6d61a3923301f141c13ccd5a1b`: **14 passed / 1 failed**. All eleven React pages, API checks, project/request navigation and exports passed; the 390px dashboard overflows horizontally. Screenshots, HTML/JUnit and the failure trace are preserved locally. The first attempt used incorrect authored locators and is preserved separately; the corrected attempt exposed this actual responsive failure. The model-driven harness cycle is tracked separately in the live run report.

## Branch retirement update — 2 October 2026

The legacy GUI is no longer installed/launched on `new-ui`. `app.py`, `monitoring/ui.py` and `tests/targets/test_harness_browser.py` are now explicit retirement candidates, with their related GUI-only symbols/tests and documentation references. They remain present and were not deleted. Shared API/Markdown helpers and support for testing repositories that use this framework remain active. The exact deletion scope and independent approval choices are in [the updated backend/cleanup plan](BACKEND_FIX_PLAN_2026-10-02.md).

The preceding cleanup commit’s backend and macOS/Windows Next.js CI workflows passed. The completed TokenTelemetry generated cycle reported 9 passed, 26 errors and 1 failed; diagnosis and proposed corrections are in the updated plan.

## Approved cleanup — 2026-10-02

The user approved exactly `app.py`, `src/maf_test_harness/monitoring/ui.py`, `tests/targets/test_harness_browser.py` and the associated GUI-only symbols/tests/references in the backend plan. They are removed. Earlier retention statements above describe the original audit state. Historical HTML, the supplied PDF, shared API/report modules and target-framework support remain.

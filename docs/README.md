# Documentation index

Start with the [Developer guide](DEVELOPER_GUIDE.md) if you will change code. Start with the [root README](../README.md) if you want to run the platform.

Rules for people and agents are in [../CLAUDE.md](../CLAUDE.md). Contribution steps are in [../CONTRIBUTING.md](../CONTRIBUTING.md). Each area of the code also has its own `CLAUDE.md`: `frontend/`, `desktop/`, `scripts/`, `skills/`, `tests/`, and `src/maf_test_harness/` with one per subpackage.

Files marked **historical** are dated reviews, plans or evidence. They record what was true on that date. They are not current claims. Do not change their content; a dated status note at the top is allowed.

## Start here

- [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md): dev environment, layout, the run lifecycle, how-to recipes, debugging, testing and conventions.
- [README.md](README.md): this index.

## Architecture

- [ARCHITECTURE.md](ARCHITECTURE.md): design of the harness, its stages and its guarantees.
- [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md): generated module table with imports and importers. Regenerate with `uv run python scripts/gen_architecture.py`. Never edit by hand.
- [CATALOG.md](CATALOG.md): stage personas, tools and Agent Skills in one reference.
- [ENVIRONMENTS.md](ENVIRONMENTS.md): isolated Python, Java and .NET environments and toolchains.
- [AUTOMATION.md](AUTOMATION.md): what the harness recovers on its own between gates, the diagnosis agent, and prompt assembly.
- [HOOKS.md](HOOKS.md): the hook engine (fail-closed checks around tools and model calls) and typed tools.
- [SKILL_PLUGINS.md](SKILL_PLUGINS.md): skill folder format, hash lock, and the trust model for user plugins.
- [NEXTJS_UI.md](NEXTJS_UI.md): the Next.js workspace and how it talks to the local API.
- [MODULAR_WORKFLOW_IMPLEMENTATION.md](MODULAR_WORKFLOW_IMPLEMENTATION.md): how the modular workflow and deferred execution setup were implemented (implemented 5 October 2026).
- [DETERMINISTIC_SKILL_REVIEW.md](DETERMINISTIC_SKILL_REVIEW.md): the deterministic analysis flow for repository analysis and run navigation. The file name says "review"; it describes the flow.

## Operations

- [CACHING.md](CACHING.md): token spend, prompt caching and the response cache.
- [GENERATION_BENCHMARK.md](GENERATION_BENCHMARK.md): offline scorer for generated pytest suites (`scripts/benchmark_generation.py`, `benchmarks/generation/`).
- [OBSERVABILITY.md](OBSERVABILITY.md): run tracing, the Observability tab and `maf-harness trace`.
- [ERROR_PLAYBOOK.md](ERROR_PLAYBOOK.md): every error code, whose fault it is, and the recommended action.
- [WINDOWS_SETUP_AND_DEBUGGING.md](WINDOWS_SETUP_AND_DEBUGGING.md): Windows setup, Node and `npm` problems, long paths and log locations.
- [CLONE_AND_PLATFORM_DIAGNOSTICS.md](CLONE_AND_PLATFORM_DIAGNOSTICS.md): clone progress, platform diagnostics and what the launcher logs show.
- [PETSTORE_STRICT_TEST_GENERATION.md](PETSTORE_STRICT_TEST_GENERATION.md): what strict test generation and its evidence cover (snapshot of the `new-ui` behaviour, 6 October 2026).
- [RELEASING.md](RELEASING.md): versioning, the release flow, unsigned macOS builds and the owner settings checklist.

## Desktop and MCP

- [DESKTOP.md](DESKTOP.md): the Electron app: architecture, development, packaging, unsigned builds and the model key.
- [MCP.md](MCP.md): adding Model Context Protocol servers in Configuration and giving them to chosen stages.

## Plans and historical reviews

Plans give direction. They are not guaranteed to be current.

- [plans/PLAN_2026-10-10_DESKTOP_GREENFIELD.md](plans/PLAN_2026-10-10_DESKTOP_GREENFIELD.md): plan for the desktop app, plugins and greenfield runs. This is the plan the root CLAUDE.md names.
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md): **historical** implementation plan for the harness, with counts from its date.
- [WORKFLOW_SERVICE_MIGRATION_PLAN.md](WORKFLOW_SERVICE_MIGRATION_PLAN.md): **historical** proposal for moving the workflow behind a service.
- [AZURE_DEPLOYMENT_PROPOSAL.md](AZURE_DEPLOYMENT_PROPOSAL.md): Azure hosting proposal. Documentation only. No Azure resources exist.
- [Azure-deployment.md](Azure-deployment.md): short pointer to the Azure proposal and the Terraform in `azure-plan/`.
- [azure-plan-desktop.md](azure-plan-desktop.md): proposal for an Azure workspace for the desktop app. Nothing is built or deployed.
- [../azure-plan/README.md](../azure-plan/README.md): Terraform, price data and validation files behind the Azure proposals.
- [BACKEND_REVIEW_2026-10-01.md](BACKEND_REVIEW_2026-10-01.md): **historical** backend review and approval plan, 1 October 2026.
- [BACKEND_FIX_PLAN_2026-10-02.md](BACKEND_FIX_PLAN_2026-10-02.md): **historical** backend correction and cleanup plan, 2 October 2026.
- [DEAD_CODE_REVIEW_2026-10-01.md](DEAD_CODE_REVIEW_2026-10-01.md): **historical** dead-code review, 1 October 2026.
- [DEAD_CODE_REVIEW_2026-10-07.md](DEAD_CODE_REVIEW_2026-10-07.md): **historical** dead-code review, 7 October 2026. It lists candidates only. Removal still needs approval.
- [GAP_REVIEW_2026-10-07.md](GAP_REVIEW_2026-10-07.md): **historical** gap review after the implementation waves, 7 October 2026.
- [UI_VERIFICATION.md](UI_VERIFICATION.md): **historical** verification notes for the Next.js migration, 1 October 2026.
- [RUN_FAILURE_AND_SKILL_EXECUTION_REVIEW.md](RUN_FAILURE_AND_SKILL_EXECUTION_REVIEW.md): **historical** review of one recorded run failure and skill execution.
- [WINDOWS_RUN_E9681C3_REVIEW.md](WINDOWS_RUN_E9681C3_REVIEW.md): **historical** review of one saved Windows run deliverable.
- [agent-framework.pdf](agent-framework.pdf): reference copy of the Microsoft Agent Framework documentation. Per the root CLAUDE.md, do not commit or share it. It is currently tracked in git, so the owner should decide whether to untrack it.

### audit/ (historical evidence, do not edit)

- [audit/ERRORS.md](audit/ERRORS.md): defect register.
- [audit/E2E_RESULTS.md](audit/E2E_RESULTS.md): results of live end-to-end runs.
- [audit/STRICT_GENERATION_2026-10-06.md](audit/STRICT_GENERATION_2026-10-06.md): **historical** strict-generation report, 6 October 2026.
- [audit/TOKENTELEMETRY_LIVE_2026-10-01.md](audit/TOKENTELEMETRY_LIVE_2026-10-01.md): **historical** live token-telemetry report, 1 October 2026.
- [audit/TOKEN_TELEMETRY_FRESH_2026-10-02.md](audit/TOKEN_TELEMETRY_FRESH_2026-10-02.md): **historical** token-telemetry report, 2 October 2026.
- [audit/UI_RESTART_VERIFICATION_2026-10-02.json](audit/UI_RESTART_VERIFICATION_2026-10-02.json): **historical** data from a UI restart check, 2 October 2026.
- [audit/backend_repro.py](audit/backend_repro.py): script that reproduces a backend finding from the audit.
- [audit/token_failure_repro.py](audit/token_failure_repro.py): script that reproduces a token failure from the audit.
- [audit/reverse-e2e.xml](audit/reverse-e2e.xml) and [audit/reverse-patched-e2e.xml](audit/reverse-patched-e2e.xml): JUnit results of the reverse-engineering end-to-end run, before and after the patch.
- [audit/petstore-baseline-e2e.xml](audit/petstore-baseline-e2e.xml) and [audit/petstore-patched-e2e.xml](audit/petstore-patched-e2e.xml): JUnit results of the Petstore end-to-end run, before and after the patch.
- [audit/agentic-qe-platform-light.png](audit/agentic-qe-platform-light.png) and [audit/agentic-qe-platform-dark.png](audit/agentic-qe-platform-dark.png): screenshots of the workspace in light and dark themes.
- [audit/agentic-qe-platform-prompts.png](audit/agentic-qe-platform-prompts.png): screenshot of the Prompts tab.
- [audit/modular-workflow-new-run.png](audit/modular-workflow-new-run.png): screenshot of a new run in the modular workflow.
- [audit/playwright-report-light.png](audit/playwright-report-light.png) and [audit/playwright-report-dark.png](audit/playwright-report-dark.png): screenshots of a Playwright HTML report.
- [audit/screenshots/petstore-cart.png](audit/screenshots/petstore-cart.png): screenshot of the Petstore cart page.
- [audit/screenshots/reverse-dashboard.png](audit/screenshots/reverse-dashboard.png): screenshot of the reverse-engineering run dashboard.

### ui/ (screenshots, historical)

- [ui/agent-skills-desktop.png](ui/agent-skills-desktop.png): screenshot of the agent skills view in the desktop app.
- [ui/audit-desktop.png](ui/audit-desktop.png): screenshot of the Audit view in the desktop app.
- [ui/live-audit-desktop.png](ui/live-audit-desktop.png): screenshot of the Audit view during a live run.
- [ui/live-qa-desktop.png](ui/live-qa-desktop.png): screenshot of the QA report during a live run.
- [ui/log-view.png](ui/log-view.png): screenshot of the Logs view.
- [ui/playwright-report-view.png](ui/playwright-report-view.png): screenshot of a Playwright report.
- [ui/petstore-wrong-password.png](ui/petstore-wrong-password.png): screenshot of a Petstore sign-in case (wrong password).
- [ui/qa-failure-view.png](ui/qa-failure-view.png): screenshot of a QA report with failures.
- [ui/skill-document-light.png](ui/skill-document-light.png) and [ui/skill-document-dark.png](ui/skill-document-dark.png): screenshots of a skill document in the light and dark themes.
- [ui/tokentelemetry-qa-view.png](ui/tokentelemetry-qa-view.png) and [ui/tokentelemetry-report-view.png](ui/tokentelemetry-report-view.png): screenshots of token telemetry in the QA and report views.

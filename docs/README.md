# Documentation index

New to the code? Read the [Developer guide](DEVELOPER_GUIDE.md) first. Per-directory context for Claude Code and humans lives in `CLAUDE.md` files next to the code (`src/maf_test_harness/**/CLAUDE.md`, `frontend/`, `desktop/`, `skills/`, `scripts/`, `tests/`). Project rules: [../CLAUDE.md](../CLAUDE.md); contribution flow: [../CONTRIBUTING.md](../CONTRIBUTING.md).

## Current (kept in sync with the code)

| Doc | Use it for |
|---|---|
| [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) | Setup, layout, lifecycle, how-to recipes, debugging, testing, glossary |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Design, stages, guarantees |
| [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md) | Generated module table with imports and importers (`python scripts/gen_architecture.py`, never hand-edit) |
| [CATALOG.md](CATALOG.md) | Personas, tools and Agent Skills |
| [ENVIRONMENTS.md](ENVIRONMENTS.md) | Isolated Python/Java/.NET environments and toolchains |
| [ERROR_PLAYBOOK.md](ERROR_PLAYBOOK.md) | Known error codes, whose fault, what to do |
| [AUTOMATION.md](AUTOMATION.md) | Bounded recovery, diagnosis agent, prompt assembly |
| [HOOKS.md](HOOKS.md) | Hook engine and typed tools |
| [CACHING.md](CACHING.md) | Token spend and prompt caching |
| [OBSERVABILITY.md](OBSERVABILITY.md) | Tracing and the Observability tab |
| [SKILL_PLUGINS.md](SKILL_PLUGINS.md) | Skill plugin format and trust model |
| [NEXTJS_UI.md](NEXTJS_UI.md) | Local Next.js workspace |
| [DESKTOP.md](DESKTOP.md) | Electron app: architecture, packaging, secrets |
| [RELEASING.md](RELEASING.md) | Versioning, release flow, owner checklist |
| [WINDOWS_SETUP_AND_DEBUGGING.md](WINDOWS_SETUP_AND_DEBUGGING.md) | Windows setup and log locations |
| [CLONE_AND_PLATFORM_DIAGNOSTICS.md](CLONE_AND_PLATFORM_DIAGNOSTICS.md) | Clone progress and platform diagnostics |
| [PETSTORE_STRICT_TEST_GENERATION.md](PETSTORE_STRICT_TEST_GENERATION.md) | Strict generation and evidence scope |
| [MODULAR_WORKFLOW_IMPLEMENTATION.md](MODULAR_WORKFLOW_IMPLEMENTATION.md) | Modular workflow and deferred execution setup |
| [DETERMINISTIC_SKILL_REVIEW.md](DETERMINISTIC_SKILL_REVIEW.md) | Deterministic repository analysis and navigation |

## Plans and proposals (direction, not guaranteed current)

[plans/PLAN_2026-10-10_DESKTOP_GREENFIELD.md](plans/PLAN_2026-10-10_DESKTOP_GREENFIELD.md) (desktop, plugins, greenfield) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · [WORKFLOW_SERVICE_MIGRATION_PLAN.md](WORKFLOW_SERVICE_MIGRATION_PLAN.md) · Azure (documentation-only): [AZURE_DEPLOYMENT_PROPOSAL.md](AZURE_DEPLOYMENT_PROPOSAL.md), [Azure-deployment.md](Azure-deployment.md), [azure-plan-desktop.md](azure-plan-desktop.md), [../azure-plan/README.md](../azure-plan/README.md).

## Historical (dated reports and evidence; do not edit, counts are not current claims)

- [BACKEND_REVIEW_2026-10-01.md](BACKEND_REVIEW_2026-10-01.md), [BACKEND_FIX_PLAN_2026-10-02.md](BACKEND_FIX_PLAN_2026-10-02.md)
- [DEAD_CODE_REVIEW_2026-10-01.md](DEAD_CODE_REVIEW_2026-10-01.md), [DEAD_CODE_REVIEW_2026-10-07.md](DEAD_CODE_REVIEW_2026-10-07.md) (removal still needs approval)
- [GAP_REVIEW_2026-10-07.md](GAP_REVIEW_2026-10-07.md)
- [UI_VERIFICATION.md](UI_VERIFICATION.md), [RUN_FAILURE_AND_SKILL_EXECUTION_REVIEW.md](RUN_FAILURE_AND_SKILL_EXECUTION_REVIEW.md), [WINDOWS_RUN_E9681C3_REVIEW.md](WINDOWS_RUN_E9681C3_REVIEW.md)
- [audit/](audit/) - defect register ([ERRORS.md](audit/ERRORS.md)), live E2E results ([E2E_RESULTS.md](audit/E2E_RESULTS.md)), dated token-telemetry and strict-generation reports, screenshots and JUnit evidence.

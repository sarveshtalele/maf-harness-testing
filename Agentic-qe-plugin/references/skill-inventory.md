# Existing MAF skills included in Agentic QE

Reviewed all 13 tracked `skills/*/SKILL.md` packages on `develop` at `f5e4f42`. Eleven are now **native Claude Code plugin skills with their original MAF names** under `Agentic-qe-plugin/skills/`. Each has an adapted procedure for source-traced E2E case generation; specialist agents preload these skills through their `skills:` frontmatter. The original MAF packages and lock remain unchanged.

| Original MAF skill | Plugin skill | Agent | Adaptation |
| --- | --- | --- | --- |
| `reverse-engineering` | `reverse-engineering` | Change scope analyst | Approved Git diff, bounded static reads and parser gaps. |
| `repository-requirements` | `repository-requirements` | Change scope and requirements analysts | Code observations are kept separate from BA requirements. |
| `spec-authoring` | `spec-authoring` | Story intake analyst | Jira/BRD role-action-benefit stories, AC and control IDs. |
| `requirement-analysis` | `requirement-analysis` | Requirements analyst | Source-cited FR/TR E2E obligations. |
| `test-planning` | `test-planning` | E2E test planner | Journey planning with risks and explicit gaps. |
| `test-design-generator` | `test-design-generator` | E2E scenario designer | Case records and expected outcomes, no runnable test files. |
| `traceability-generator` | `traceability-generator` | Planner, reviewer and artifact generator | Exact source-to-case links and orphan checks. |
| `coverage-analyzer` | `coverage-analyzer` | Reviewer and artifact generator | Static design coverage counts only. |
| `api-contract-analyzer` | `api-contract-analyzer` | Requirements analyst | Optional supplied contract constraints. |
| `test-data-generator` | `test-data-generator` | E2E scenario designer | Source-backed example values, no fixtures. |
| `playwright-test-generator` | `playwright-test-generator` | E2E scenario designer | Optional read-only MCP observation; the inherited name is retained for discoverability, but `.spec.ts` generation is removed. |
| `generic-test-generator` | excluded | none | Runnable test source generation is outside the requested case catalog. |
| `junit-generator` | excluded | none | Java unit/integration code is outside E2E case design. |

The four `qe-run`, `qe-status`, `qe-workflow` and `qe-export` skills are plugin commands. All 11 adapted domain skills are available under the `agentic-qe:` namespace and are loaded by the seven specialist agents. Original `plugin.yaml`, host scripts and runtime coverage tools are not copied because they are tied to the older MAF application's execution host. Their evidence and validation rules are carried into the native skills above.

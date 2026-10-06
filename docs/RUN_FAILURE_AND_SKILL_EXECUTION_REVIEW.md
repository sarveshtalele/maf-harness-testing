# Run failure, skill execution and progress review

## Recorded failure

Run `0a13aeaa2c90` stopped at Test design. Its saved errors were unknown requirement source IDs (`text-statistics:compute-metrics`, `text-statistics-compute-metrics`) and an unknown actor (`user`). These were generated-model output errors, not application test failures: target test execution had not started. Eight provider calls across planning/design used 320,115 reported tokens before the stop. The original run and approvals remain unchanged.

## Fixed mechanisms

- Static adapters previously called host Python functions directly. The new reviewed skill entry points now actually execute through the host-tier script runner. Requests and structured results use contained temporary files. Script SHA-256, stage, duration, exit code and outcome are persisted and appear in per-step observability and the skill execution map. No target script, installation or model is used for these two phases.
- Observability omitted static skills because it only joined model provenance. It now joins host records too, distinguishes host vs model, reports script counts, and retains truthful historical adapter records without pretending scripts ran in those old runs.
- Evidence views previously stopped polling at a gate and depended only on busy-state changes. Observability and audit now refresh at gates and during inspection, reject aborted fetch results, and support a workflow-step filter.
- Planning/design prompts duplicated the whole reverse-engineering inventory and up to 120 KB of source. They now use compact repository metadata and bounded source excerpts. Approved IDs and actors are listed explicitly, constrained in the prompt's output contract, and remain strictly validated. Feedback stays at the end to preserve cache prefixes. Arbitrary source links are not silently remapped.
- Skill cards now show configured workflow steps and recorded executions by run, step, mode, script and outcome. Loading instructions is distinct from executing a script. The map covers the latest 100 runs and states its limit.
- Progress reports approved-stage milestones divided by the saved run's stage count. A waiting/failed/stopped run does not automatically become 100%. A completed cycle is 100% even when QA failed: completion and verdict are separate.
- Script input-file names are now checked for containment, with resolved temporary paths for macOS aliases and Windows compatibility.
- Coverage repair feedback now lists every uncovered requirement ID, rather than the first 12 with a further 900-character truncation. This gives the model actionable coverage feedback without weakening validation.

## Local verification

Run `e1646d8a3751` cloned the same public repository and reached the Test design review gate. Both static scripts ran once, exited successfully and recorded their hashes, with zero provider calls and zero tokens in those stages. Planning/design reported 158,967 tokens across eight provider calls, compared with 320,115 in the stopped run; this single comparison is not a general savings guarantee. The design needed two coverage repairs and retains explicit coverage gaps, including frontend cases needing a live application URL. It has not executed target tests, so this is not a passing target-application report.

Application validation: 749 backend tests passed (15 skipped), all seven Playwright browser tests passed, production frontend build/type checks passed, and lint/format checks passed. The final coverage-feedback change also passed 28 focused regressions. Existing files and the stopped run were preserved.

## Remaining boundaries

Static analysis remains bounded declaration evidence, not complete semantic requirements. Unknowns must remain visible. Planning/design/generation still require a model for arbitrary repositories; scripts/skills are not interchangeable with a trained semantic test generator. Test execution, report parsing and progress calculation remain host-controlled; optional model diagnosis is separately traceable. Source ID failures remain blocked rather than accepted as accurate.

Checks include real script subprocesses with zero model calls for the first two stages, containment rejection, static-stage observability, progress behavior, stable prompt ordering, and Playwright navigation/reporting/progress/skill-map verification. The standalone verification run uses the existing repository without rewriting the stopped run.

## Strict generation update — October 6, 2026

[Current intake, batching, Java quality gates and scoped QA](PETSTORE_STRICT_TEST_GENERATION.md) supersede earlier truncation and final-retry acceptance behavior. New deterministic requirements retain all discovered declarations; installation/runtime checks remain at Test execution. Maven baseline unit/integration and generated results are separate, and runtime coverage is explicitly unmeasured. Run logs default to the checkout’s `.maf-harness` directory.

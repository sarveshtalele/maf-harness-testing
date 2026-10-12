# workflow_steps/ (stage operations mixed into Harness)

`Harness` (in `workflow.py`) inherits these classes; it keeps checkpoint and approval ownership, they hold the stage logic. Stage list: `workflow_support.STAGES`.

## Key files
- `planning.py`: `PlanningSteps.model(stage, state, ctx)` generic model-stage runner; `planning_batches.py`: `planning_is_batched`, `merge_plans`.
- `design.py`: `design_tests_batched`, `requirement_batches` (component-batched, packs small components). `design_review.py`: confidence reviewer of design cases (rubric 30/25/25/20, caps, quote check, hard gate): `review_design_cases`, `score_case`, `weak_cases`, `gate_message`, `apply_design_reviews`. Target caps only for a confident code symbol (`qa/confidence.confident_target`); a non-code case (UI, CLI, route, flow) reaches 100 with a verified quote from target_source or the approved requirement evidence (`requirement_evidence`). `plan_review.py`: confidence reviewer of PLN areas (rubric 40/30/30, caps, hard gate): `review_plan_areas`, `score_area`, `weak_areas`; evidence may be any tracked file, `no_controls` is a note, `controls_uncovered` (cap 70) counts a same-directory evidence file. Both repair inside `_model_artifact` up to `MODEL_REPAIR_ATTEMPTS`, keep the last gate failure's feedback in `state["gate_feedback"]` (sent as the next repair, Retry included), then raise `Confidence gate: test planning|test design ...` when `settings.full_accuracy(state)` is on (gate off: advisory). A reviewer that cannot answer raises `Confidence reviewer unavailable: <stage> ...` at once (no author repair spent; gate off: advisory audit note). A design repair that drops a requirement link or control of an earlier attempt fails with "keep or replace, do not delete". Whole-prompt overrides never reach reviewer calls.
- `generation.py` `GenerationSteps`: batched generate -> gates -> review; `test_review.py`: independent reviewer of generated cases.
- `execution.py` `ExecutionSteps`: `plan_environment` (install approval) and `plan_execution` (run approval).
- `greenfield.py`: `is_greenfield`, `has_no_code`, `GreenfieldSteps`, `SCAFFOLDS`. `rerun.py` `RerunSteps`: replay a stage, keeping evidence under `attempts/<id>`.

## Generation gate order
validate_generated_files -> validate_java_traceability -> _check_imports -> _check_paths_are_new -> collisions -> _build_gate -> review_generated_tests. Failed batches are fail-soft (`generation_failures`, `HARNESS_MAX_FAILED_BATCH_RATIO` default 0.5); failed cases show `generation_failed` and block PASS. Cancel points: between batches and before model calls.

Accuracy gate (`settings.full_accuracy(state)`: run `require_full_accuracy`, else `HARNESS_REQUIRE_FULL_ACCURACY`, default on): `_generate_tests_batched` runs its batches through the `batch_passes()` generator, which then yields up to `HARNESS_ACCURACY_ROUNDS` regeneration rounds for cases not generated and accepted (`review_accepted`). Rounds use batch indices above `total_batches` and carry the earlier reviewer note as `repair_feedback`. On the last batch attempt a partly rejected batch keeps its verified cases (files and reviews); only the rejected IDs fail and enter the rounds (gate off: recorded in `generation_failures`). The batch cache is reused only for the same case layout, and a cached batch that is missing cases regenerates just those cases. The cache signature includes `HARNESS_TEST_REVIEW_MIN_CONFIDENCE` and the policy `-v4`. Cases not source-review accepted after the rounds raise `Accuracy gate: X of N ...`, which stops the stage before environment_setup. The artifact keeps `case_verification` and `verification_summary`; gate off keeps the fail-soft path. Greenfield runs without code skip the gate (no source to review against).

## Invariants
- Workflow version 2: no workspace, install or runtime collection before `environment_setup`; old runs keep their saved stage order.
- Final retry must still reject unexplained coverage omissions; never drop requirements.
- Gates are MAF `request_info` events (`Harness.ask`) and state is checkpointed under `run_dir/checkpoints`; keep stage code resumable (resume = rerun from the active stage).

## Test
`uv run pytest -q tests/test_harness_gates.py tests/test_fail_soft_generation.py tests/test_accuracy_gate.py tests/test_confidence.py tests/test_generation_gates.py tests/test_planning_batches.py tests/test_design_review.py tests/test_execution_stage.py tests/test_stop.py tests/test_case_budget.py`

## Recipe: add a stage
Add to `STAGES` (and `CORE_STAGES` if pre-execution) in `workflow_support.py`; persona in `agent/personas.py`; handler in `Harness.enter` / a new steps mixin; ETA default in `ui/eta.py` `DEFAULTS`; progress in `ui/progress.py`; step template `qa/templates/NN-*.md`; frontend `label`/tabs; tests; regenerate the architecture map.

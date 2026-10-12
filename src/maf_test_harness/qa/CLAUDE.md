# qa/ (verdict, triage, step reports, ZIP)

Deterministic: requirement -> approved case -> generated test -> executed result. No model calls.

## Key files
- Two signals, never merged: "source-review accepted" (review) and "passed" (execution). `review_miss` marks an accepted case that then failed an assertion; the report has a "Review vs execution" table.
- `report.py`: `build_qa_report` (also `verdict_for`, `design_cases`, `render_markdown`) writes `qa_report.json` / `qa_report.md`.
- `triage.py`: `locations`, `classify`, `remedy_for`, `build_updates`; `REMEDIES` is an ordered list of `(regex, advice)` for known generated-test mistakes.
- `confidence.py`: pure host confidence. `RUBRIC` (five dimensions summing to 100), `evidence_caps` (target_not_invoked 40, target_mocked 40, no_assertion 30, target_source_missing 50 forcing insufficient_evidence, needs_revision 49, insufficient_evidence 59), `score_review`, `review_accepted`, `verification_note`. Rules are best-effort regexes for Python, JS/TS and Java, not parsers.
- `derivation.py`: static D1 (lifted by reviewer evidence, see `confidence.settle_deterministic`) `count_assumes_no_filter` and D2 `expectation_contradicts_source` (AST only, no exec) plus blind literal masking (`mask_expected_literals`). Host caps for the reviewer's derivations live in `confidence.py` (`derivation_findings`).
- `quality.py`: `existing_requirement_witnesses` (static match that an existing test invokes the symbol), `existing_test_evidence`, `require_complete_intake`, `validate_java_traceability`.
- `step_reports.py`: `build_step_reports` fills one template per step from `templates/00-summary.md` ... `08-qa-verdict.md`.
- `ledger.py`: the confidence ledger, `<run_dir>/confidence.jsonl` (one line per host-computed plan/design/test score: caps, verdict, derivations, prompt_record_id and sha256, persona override hash, model, round). `append_score`, `entries` (with line numbers); old runs without a ledger read as empty.
- `confidence.py` also holds `PLAN_RUBRIC`, `DESIGN_RUBRIC`, `RUBRICS`, `rubric_score` (points clamped, verdict caps revise 79 / reject 0) and `quote_verified`.
- `packaging.py`: `build_deliverable` (ZIP with repo, generated tests, evidence, `traceability.json/csv`), `run_instructions`, `portable_plan`.
- `controls.py`: pure end-to-end traceability. `assign_controls` (CTL-nnn per requirement, PLN-xx by file overlap, PLN-00 fallback), `case_controls` (exact ID tokens), `trace_rows` / `trace_matrix` (final_status and failed_at per control). Host-assigned only; models never write IDs.

## Invariants
- Confidence is host-computed: the model's `confidence` is kept as `model_confidence`; the final value is `min(sum of scores, caps)`. A case with no declared target skips target rules.
- Every requirement is a new case, a `pre_existing_witness`, or an ID-linked gap with a reason; states: new / pre_existing_witness / excluded / unexplained. IDs match exact tokens (REQ-1 != REQ-10).
- Never label static traceability as measured line/branch/mutation or complete application coverage.
- Scope generated, baseline unit and baseline integration results separately.
- Step reports use no clock or randomness (they must be reproducible; `run_completed_at` derives from artifacts).

## Test
`uv run pytest -q tests/test_triage.py tests/test_requirement_witnesses.py tests/test_step_reports.py tests/test_petstore_quality.py tests/test_confidence.py tests/test_accuracy_gate.py`

## Recipes
- New known test mistake: add `(re.compile(...), "advice")` to `REMEDIES` in `triage.py` (specific before generic) and a case in `tests/test_triage.py`. If it is detectable before running, also add a static gate in `tools/mock_pitfalls.py`.
- New step report field: edit the matching file in `templates/` and `build_step_reports`.
- New traceability step: extend `_case_outcome` and `FAILED_AT_ORDER` in `controls.py`, then add a case to `tests/test_traceability_controls.py`.

# qa/ (verdict, triage, step reports, ZIP)

Deterministic: requirement -> approved case -> generated test -> executed result. No model calls.

## Key files
- `report.py`: `build_qa_report` (also `verdict_for`, `design_cases`, `render_markdown`) writes `qa_report.json` / `qa_report.md`.
- `triage.py`: `locations`, `classify`, `remedy_for`, `build_updates`; `REMEDIES` is an ordered list of `(regex, advice)` for known generated-test mistakes.
- `quality.py`: `existing_requirement_witnesses` (static match that an existing test invokes the symbol), `existing_test_evidence`, `require_complete_intake`, `validate_java_traceability`.
- `step_reports.py`: `build_step_reports` fills one template per step from `templates/00-summary.md` ... `08-qa-verdict.md`.
- `packaging.py`: `build_deliverable` (ZIP with repo, generated tests, evidence, `traceability.json/csv`), `run_instructions`, `portable_plan`.
- `controls.py`: pure end-to-end traceability. `assign_controls` (CTL-nnn per requirement, PLN-xx by file overlap, PLN-00 fallback), `case_controls` (exact ID tokens), `trace_rows` / `trace_matrix` (final_status and failed_at per control). Host-assigned only; models never write IDs.

## Invariants
- Every requirement is a new case, a `pre_existing_witness`, or an ID-linked gap with a reason; states: new / pre_existing_witness / excluded / unexplained. IDs match exact tokens (REQ-1 != REQ-10).
- Never label static traceability as measured line/branch/mutation or complete application coverage.
- Scope generated, baseline unit and baseline integration results separately.
- Step reports use no clock or randomness (they must be reproducible; `run_completed_at` derives from artifacts).

## Test
`uv run pytest -q tests/test_triage.py tests/test_requirement_witnesses.py tests/test_step_reports.py tests/test_petstore_quality.py`

## Recipes
- New known test mistake: add `(re.compile(...), "advice")` to `REMEDIES` in `triage.py` (specific before generic) and a case in `tests/test_triage.py`. If it is detectable before running, also add a static gate in `tools/mock_pitfalls.py`.
- New step report field: edit the matching file in `templates/` and `build_step_reports`.
- New traceability step: extend `_case_outcome` and `FAILED_AT_ORDER` in `controls.py`, then add a case to `tests/test_traceability_controls.py`.

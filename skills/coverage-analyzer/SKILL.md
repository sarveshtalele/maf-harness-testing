---
name: coverage-analyzer
description: "Analyze evidence-based test coverage and identify actionable gaps by comparing traceable test cases with requirements, validation rules, and optional API contracts. Use as the final pipeline validation step when a quantitative coverage or gap report is needed."
---

# Coverage Analyzer

## Goal

Compare supplied source IDs with explicit test links, after test design and, when available, test execution, and produce a quantitative coverage and gap report. Static traceability is never measured coverage: never label a link-based figure as line, branch, mutation or runtime coverage; report those only from executed tool evidence.

## Inputs

| Input | Canonical source | Produced by | Required contract |
|---|---|---|---|
| Requirement model | Requirement model supplied by the host | requirement analysis | Stable requirement and acceptance-criterion IDs. |
| Validation rules | Normalized rules in the requirement model | requirement analysis | Stable validation IDs and explicit constraints. |
| API contracts | Normalized contract model, when supplied | `api-contract-analyzer` | Stable endpoint or contract IDs; optional. |
| Test design | Test cases with traceability links | `test-design-generator` | Unique test IDs and explicit traceability links. |
| Execution evidence | Host execution results | host test run or CI | Optional; use it only to report execution status, not to infer linkage. |

Return `INVALID_INPUT` when the requirement model or test design is absent, malformed, or lacks stable IDs. Treat missing validation rules, API contracts, and execution evidence as unavailable dimensions: report their status in `gaps_report.notes`; never treat them as 100% covered.

## Output contract

`assets/coverage_templates.yaml` is the single authoritative schema. Return one schema-valid report. Include all required sections, including empty arrays and maps where a supplied dimension has no items.

Each `coverage_details` entry maps a supplied source ID to the test IDs that explicitly cover it. Use its entries to derive both the aggregated metrics and the gap lists; do not hand-maintain conflicting totals.

## Steps

1. Normalize source IDs and test IDs by trimming whitespace; compare IDs exactly after normalization and preserve their original case in output.
2. Mark an item covered only when at least one test explicitly links to that same source ID and link type. Aggregate multiple matching tests into one coverage item and count that item once.
3. Calculate each percentage as `(covered / total) * 100`, rounded to two decimal places. For a supplied dimension with zero items, set `percentage` to `0`, not `100`.
4. Treat partial coverage as uncovered unless an explicit, separately linked source ID exists for the covered portion. Do not award fractional coverage.
5. Include every uncovered supplied ID in the matching `gaps_report` array. Create deterministic gap IDs using `GAP-<dimension>-<source-id>` when a consumer needs issue identifiers.
6. Assign each source item one coverage state: `new` (covered by a generated test linked to it), `pre_existing_witness` (an existing repository test that directly invokes and asserts it, cited as `path::test name`), `excluded` (ID-linked reason it cannot be tested), or `unexplained` (none of these; an error that must be resolved or reported). Only `new` and `pre_existing_witness` count as covered; report the four counts separately.
7. Keep API coverage absent from the assessment when no API source was supplied; state that limitation in `gaps_report.notes`.

## Ambiguity Resolution and Evidence Rules

- Flag a test link to an unknown source as an `orphaned_linkage` entry; it never contributes coverage.
- Treat a test without traceability links as unlinked and do not count it as evidence.
- Deduplicate repeated links from the same test to the same source ID.
- Do not infer coverage from matching prose, similar names, test proximity, test execution success, or an "assumed covered" claim.
- Do not invent source IDs, tests, validation rules, endpoints, execution outcomes, or explanations for missing evidence.

## Self-check

1. Validate the output against `assets/coverage_templates.yaml`.
2. Recalculate every `covered`, `total`, and percentage from `coverage_details` and reconcile the result with `summary`.
3. Confirm each covered item has at least one supplied test ID and each uncovered item has a source ID in `gaps_report`.
4. Confirm orphaned and unlinked tests are reported without increasing coverage.
5. Ensure `gaps_report.notes` explains every missing optional dimension or non-empty gap list.

## Structured Error Object

For unrecoverable input failures, return this object instead of a coverage report:

```json
{
  "error": true,
  "code": "INVALID_INPUT",
  "message": "Test design is missing stable test IDs.",
  "missing_files": ["test_cases"],
  "details": {"field": "test_cases[].id", "issue": "missing or duplicate"}
}
```

Use `MISSING_INPUT`, `INVALID_INPUT`, or `SCHEMA_VALIDATION_FAILED` as the code, with a specific message and only the affected files or fields.

## Configuration

| Setting | Description | Default |
|---|---|---|
| `MIN_AC_COVERAGE` | Acceptance-criteria threshold used for reporting. | `80` |
| `MIN_VALIDATION_COVERAGE` | Validation-rule threshold used for reporting. | `80` |
| `COVERAGE_TOOL` | Runtime line-coverage tool used by `scripts/run-coverage.js`. | `nyc` |
| `OUTPUT_FORMAT` | Runtime coverage report format. | `json-summary` |

Thresholds identify risk; they never change the evidence-based calculation.

## Success Criteria

- Produce schema-valid, reproducible metrics from explicit links.
- Make every covered source item traceable to one or more test IDs.
- Make every uncovered, missing, orphaned, or ambiguous item actionable in the gap report.

See also: `examples/input.json`, `examples/output.json`, and `references/coverage_rules.md`.

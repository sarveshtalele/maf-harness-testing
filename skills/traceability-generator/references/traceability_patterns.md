# Traceability Patterns

## Evidence-based bi-directional tracing

Use only declared requirement-to-test links. For each declared link, confirm the requirement exists and the test exists; then attach every artifact whose `test_case_id` matches that test. The reverse view must also identify tests without declared requirement links.

## Coverage and change impact

For a changed requirement ID, filter matrix rows by `requirement_id` to identify impacted test cases and their automation artifacts. If it has no rows, treat it as an uncovered requirement, not as having no impact.

## Audit expectations

Retain source IDs unchanged, record UTC generation time, generator version, and contributing source skills. Store the matrix alongside the pipeline output so a reviewer can reproduce both the source and the gap report.

## Diagnostics

Unresolved requirement/test IDs and duplicate IDs invalidate reliable coverage; put them in `gaps_report.missing_links`. Missing artifact types are non-fatal: use `unknown` and add a warning. Never create a relationship to make coverage appear complete.

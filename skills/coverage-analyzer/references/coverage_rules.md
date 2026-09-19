# Coverage Rules

## Table of Contents

- [Coverage Metrics](#coverage-metrics)
- [Coverage Calculation](#coverage-calculation)
- [Gap Identification](#gap-identification)
- [Traceability and Ambiguity](#traceability-and-ambiguity)
- [Anti-Hallucination](#anti-hallucination)
- [Error Handling](#error-handling)

This document provides rules and guidelines for measuring test coverage and identifying gaps.

## Coverage Metrics

- Measure acceptance criteria coverage.
- Measure validation coverage.
- Measure API contract coverage.
- Use evidence from test cases and artifacts.

## Coverage Calculation

- Calculate coverage percentages based on total items and covered items.
- Report coverage as a percentage and absolute counts.
- Preserve traceability for each coverage item.

## Gap Identification

- List uncovered requirements.
- List uncovered validations.
- List uncovered API contracts or endpoints.

## Traceability and Ambiguity

- Count a source item once, even when several tests link to it.
- Record the source-to-test mapping in `coverage_details`.
- Treat an unknown source ID as an orphaned linkage and exclude it from coverage.
- Treat tests with no explicit links as unlinked; never infer their coverage from text or execution status.
- Use `0%` for supplied dimensions with no source items. Mark dimensions with no supplied input as unavailable in `gaps_report.notes`.

## Anti-Hallucination

- Only mark items as covered when there is explicit test evidence.
- Do not infer coverage from unrelated artifacts.

## Error Handling

- Flag incomplete inputs affecting coverage calculations.
- Report missing requirement, validation, or API coverage data.

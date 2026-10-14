---
name: coverage-analyzer
description: Quantify static E2E case-link coverage and list every uncovered or orphaned source item.
---

# Design coverage and gaps

Adapted from MAF `skills/coverage-analyzer`. For each supplied control, requirement and plan ID, count it covered only when a valid case explicitly links to that exact ID. Trim surrounding whitespace for comparison, preserve original case, and count a source item once even when several cases link it. Treat partial coverage as uncovered unless the covered part has its own source ID.

Derive `covered`, `total` and `(covered / total) * 100` to two decimals from link rows. A supplied dimension with zero items reports zero, not 100. Missing optional API contracts are unavailable, not fully covered. List every uncovered ID, orphan case, unknown link and contradictory source claim. Do not infer coverage from similar prose, test proximity or any pass/fail result.

This is static case-design coverage. Do not run a coverage tool or report execution coverage. Recalculate counts from the case links immediately before report generation.

---
name: traceability-generator
description: Generate a requirement-to-test traceability matrix and gap report from requirements, test cases, traceability links, and generated artifacts. Use when the user asks for traceability, coverage analysis, impact analysis, or orphan-test detection.
---

# Traceability Matrix Generator

Aggregate verified links produced by the test-generation pipeline into an auditable requirement-to-test-to-artifact matrix. Preserve source IDs and report evidence gaps rather than inferring links.

## Scope Boundaries

This skill consumes existing `traceability_links`; it does not generate or guess links, modify requirements, or change test cases. It aggregates outputs from `test-design-generator`, `gherkin-generator`, `playwright-generator`, and `junit-generator`.

## Input Contract and Normalization

| Source | Required fields consumed | Normalization |
| --- | --- | --- |
| Requirement model | `requirements[].id` | IDs must be non-empty and unique. |
| Test design | `test_cases[].id`, `traceability_links[]` or `test_cases[].linked_requirements[]` | Normalize links to `{test_case_id, requirement_id}`. |
| Artifact generators | `generated_artifacts[].id`, `.type`, `.test_case_id` | Match artifacts to tests by `test_case_id`; use `unknown` when `type` is absent. |

Accept `test_id` as an alias for `test_case_id` in `traceability_links[]`. Keep all source IDs exactly as supplied. If no link evidence is supplied, do not create a matrix entry.

## Output Contract

```json
{
  "metadata": {
    "generated_at": "ISO-8601 UTC timestamp",
    "generator_version": "1.1",
    "source_skill_names": ["test-design-generator", "playwright-generator"]
  },
  "traceability_matrix": [{
    "requirement_id": "string, required",
    "test_case_id": "string, required",
    "artifact_id": "string or null",
    "artifact_type": "string; unknown if omitted",
    "status": "covered | partial | missing_artifact"
  }],
  "gaps_report": {
    "uncovered_requirements": ["requirement ID"],
    "orphan_tests": ["test case ID"],
    "missing_links": ["actionable diagnostic"],
    "warnings": ["non-fatal diagnostic"],
    "notes": "string"
  }
}
```

`metadata`, `traceability_matrix`, and non-null `gaps_report` are required. Emit one entry for each evidenced requirement/test/artifact relation. When a linked test has no artifact, set `artifact_id` to `null` and `status` to `missing_artifact`; when it has one or more artifacts, set `status` to `covered`.

## Processing Rules

1. Validate unique requirement and test IDs before aggregation; report duplicate or malformed IDs as diagnostics and do not merge them silently.
2. Build links only from `traceability_links[]` or `linked_requirements[]` supplied by test design.
3. Resolve artifact references by matching their `test_case_id`; retain a separate entry for every resolved artifact.
4. Put requirements with no valid mapped test in `gaps_report.uncovered_requirements`.
5. Put tests with no valid requirement link in `gaps_report.orphan_tests`.
6. Put broken requirement/test references and missing artifact diagnostics in `gaps_report.missing_links`; use `warnings` for defaults such as `artifact_type: "unknown"`.

## Edge Cases

- Partial coverage: return valid rows and list every uncovered requirement; never mark the overall matrix complete.
- Duplicate IDs: report them in `missing_links` and require source cleanup before treating coverage as reliable.
- Missing artifact type: use `unknown` and add a warning.
- Broken back-reference: retain no invented row; name the unresolved ID in `missing_links`.
- Circular or repeated links: de-duplicate exact duplicate relations and warn about the repetition.

## Anti-Hallucination Rules

- Never create a requirement-to-test or test-to-artifact relationship without an explicit source link or matching artifact back-reference.
- Never convert an unresolved ID, duplicate ID, or missing artifact into a covered status.

## Verification

Before returning output, verify that:

- every valid requirement is either represented by a matrix row or listed as uncovered;
- every valid test is linked or listed as an orphan;
- every non-null artifact ID resolves to an input artifact;
- `gaps_report` exists, including when all arrays are empty;
- metadata identifies the generator version and contributing source skills.

Run the bundled validator when a JSON result is available:

```powershell
python scripts/validate_traceability.py path\to\traceability_matrix.json --source path\to\traceability_input.json
```

## Worked Example

Input excerpt (see `examples/input.json` for the complete case):

```json
{"requirements":[{"id":"REQ-001"},{"id":"REQ-002"}],"test_cases":[{"id":"TC-001","linked_requirements":["REQ-001"]},{"id":"TC-002"}]}
```

Expected gap behavior:

```json
{"gaps_report":{"uncovered_requirements":["REQ-002"],"orphan_tests":["TC-002"],"missing_links":[],"warnings":[],"notes":"Coverage is incomplete."}}
```

See `assets/matrix_templates.yaml` for the reusable schema and `references/traceability_patterns.md` for bi-directional tracing, impact analysis, and audit guidance.

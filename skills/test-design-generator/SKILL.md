---
name: test-design-generator
description: "Generate structured, deterministic test cases from analyzed requirements, acceptance criteria, validation rules, and optional API contracts. Use after requirement analysis when a traceable test design, coverage analysis, or gap report is needed."
---

# Test Design Generator

## Goal

Generate evidence-based, deterministic, and traceable test cases from the normalized requirement model. It consumes the approved requirement model (and optionally an API contract model) and supplies test design to test-data, generation, traceability, and coverage consumers.

## Inputs

| Input | Canonical source | Required | Contract |
|---|---|---|---|
| Requirement model | Approved requirement model supplied by the host | Yes | Every requirement and acceptance criterion must have a stable ID and evidence text. |
| Validation rules | Normalized rules in the requirement model | No | Each usable rule must have a stable ID, target, and explicit constraint or outcome. |
| API contracts | Normalized contract model from `api-contract-analyzer`, when supplied | No | Generate contract tests only for supplied endpoints and documented responses. |

Return `INVALID_INPUT` when the requirement model is missing, malformed, or lacks stable IDs. A missing optional source is not an error; record its effect in `gaps_report.notes` instead.

## Output contract

`assets/testcase_templates.yaml` is the single authoritative schema. Emit all top-level sections defined there, including empty arrays/objects where no evidence exists. Each test must contain an ID, title, type, priority, expected result, an `oracle`, and at least one `traceability_links` entry.

Each `oracle` states where the expected result comes from: `kind` is one of `specified` (a verbatim documentation or spec quote), `derived` (a verbatim quote from the target implementation), `metamorphic` (a stated relation between runs), `golden` (a fixture scenario with an answer derived by hand from a documented rule), `property` (a stated property of the output) or `characterization` (current behaviour only). `source` ({file, line, quote}) is required for `specified`, `derived` and `golden`; `relation` is required for `metamorphic`, `golden` and `property`. The host checks each quote verbatim against its file and marks the oracle `verified`. A `characterization` case is never reported as verified.

Return the design in the host-supplied structured output; any human-readable report accompanies it.

## Steps

1. Generate at least one positive test for each explicit acceptance criterion.
2. Generate at least one negative or validation test for each explicit validation rule.
3. Generate boundary tests only for explicit minimum/maximum, length, date, or range constraints; respect `max_boundary_cases`.
4. Generate contract tests only when API contracts exist and only for documented methods, schemas, and responses.
5. Generate deterministic test IDs with UUID v5 using `deterministic_namespace`; never use random IDs.
6. Give every test a `traceability_links` entry for each source artifact it directly verifies. Valid link types are `requirement`, `acceptance_criterion`, `validation`, and `api_contract`.
7. Account for every supplied requirement with exactly one of: a new traceable case; an existing repository test that directly invokes and asserts it (cite `path::test name` in `gaps` with the exact requirement ID); or an ID-linked gap with a concrete reason. Unexplained omissions are errors.
8. Target the method that actually implements the expected behavior (arithmetic done in a callee belongs to the callee), and invoke the method each case claims.
9. Choose the oracle before the expected result. Prefer `specified`, `derived`, `metamorphic` and `golden` over `characterization`. For ranking, scoring or analysis tools, use metamorphic relations (determinism, irrelevant-input invariance, order invariance) and golden scenarios whose answers come from the documented rule (see references/test_design_patterns.md), never from the implementation's output.
10. Assign priority from source impact: `P0` for release-blocking, security, payment, or data-loss behavior explicitly identified in evidence; `P1` for core behavior and validations; `P2` otherwise. Do not infer criticality when source evidence is silent; use `P1` and note the default in `gaps_report.notes`.

## Ambiguity Resolution and Anti-Hallucination

- Never invent business rules, validations, bounds, expected errors, API responses, or criticality.
- When a rule is vague, incomplete, or contradictory, add its source ID and explanation to `gaps_report.incomplete_requirements`; generate tests only from the explicit portion.
- Do not create a boundary test when a bound is absent or contradictory.
- Do not create stub tests to make coverage metrics look complete.
- Treat absent API contracts as unavailable evidence, not permission to infer an API.

## Self-check

1. Validate the output against `assets/testcase_templates.yaml`.
2. Confirm every test ID is unique and reproducible for the same normalized input.
3. Confirm every traceability link references a supplied source artifact and that every test has at least one link.
4. Build the traceability matrix and coverage analysis directly from test-level links; do not hand-maintain conflicting values.
5. Reconcile all summary counts and ensure `total_tests_generated` equals the sum of the five test-type counts.
6. Place uncovered, ambiguous, incomplete, and untestable source items in `gaps_report`; coverage may be below 100% only when the report explains why.

## Structured Error Object

For unrecoverable input failures, return:

```json
{
  "error": true,
  "code": "INVALID_INPUT",
  "message": "Detailed description of the problem",
  "details": {"field": "requirements", "issue": "missing id"}
}
```

## Configuration

| Setting | Description | Default |
|---|---|---|
| `deterministic_namespace` | UUID namespace for deterministic test IDs. | `6ba7b810-9dad-11d1-80b4-00c04fd430c8` |
| `max_boundary_cases` | Maximum variations per explicit constraint. | `3` |
| `traceability_prefix` | Source-link prefix convention. | `REQ-` |

Configuration may be provided through a sibling `config.yaml` or environment variables prefixed with `TEST_DESIGN_`.

## Local Validation Helper

Finalize generated JSON through the bounded local adapter. It validates the authoritative schema and source links, assigns deterministic IDs, and rebuilds derived summaries without writing files:

```bash
python scripts/validate_design.py --input test_cases.json --source requirements.json --repo-url <repository-url>
```

## Success Criteria

- Every generated test is schema-valid, deterministic, and traceable.
- Every explicit acceptance criterion has a positive test or a documented gap.
- Every usable validation rule has a negative/validation test or a documented gap.
- Every coverage shortfall and ambiguity is visible in `gaps_report`.

## Host integration

The host processes the full discovered inventory in component batches of at most 12 requirements, then merges and validates the complete design. Each supplied ID needs a linked case, a cited existing test, or an explicit ID-linked gap; unexplained omissions remain errors after the final retry. Preserve distinct method-level evidence: constructor initialization does not cover setter behavior. Design positive fixtures from real source constraints, and complement existing repository tests. Static declaration intake and test traceability do not establish full application or measured runtime coverage.

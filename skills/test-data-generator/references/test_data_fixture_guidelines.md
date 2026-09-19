# Test-Data Fixture Guidelines

This document provides guidelines for generating deterministic and valid test-data fixtures from test cases, validation rules, and API schemas.

## Schema Fields

- Generate values only for fields defined in the schema.
- Preserve required field definitions and field types.
- Do not create fields that are not explicitly present.

## Enum Values

- Use only values listed in `enum` definitions.
- If enum values are absent, use values consistent with type constraints.

## Request and Response Payloads

- Generate request payload examples based on API schemas and required fields.
- Generate response payload examples aligned with response schemas.
- Preserve schema structure and nested object definitions.

## Deterministic Data

- Use deterministic seed generation to produce repeatable fixtures.
- Ensure data remains consistent across runs for the same input.

## Boundary Values

- Derive boundary fixtures from min/max and length constraints.
- Include both valid boundary values and invalid boundary cases.

## Validation Rules

- Map field-level rules such as required, optional, min, max, pattern, format, and enum constraints.
- Translate cross-field and business validation rules into concrete fixture variants.
- Document missing validation rules and ambiguous rule definitions.

## API Testing Rules

- Generate request payload examples for valid success cases and invalid contract-breaking cases.
- Include headers, query parameters, path parameters, and body payloads when defined by the API contract.
- Support expected status codes for success and validation/failure responses.
- Preserve response schema structure for both success and error payload examples.

## Traceability

- Every fixture MUST include a `traceability_links` array.
- Each traceability entry MUST contain `source_id` (the ID of the source artifact) and `type` (one of `requirement`, `validation`, `api`). An optional `label` field can provide context.
- Verify that all traceability links reference artifacts actually present in the input — flag orphaned links in the gap report.

## Data Consistency

- Maintain foreign-key integrity across related fixtures (e.g., an order's customer_id must match the customer fixture's ID).
- Generate shared constants (IDs, dates, enums) once and reuse across all related fixtures in the same set.
- Document cross-fixture relationships in summary_metadata.notes.

## Summary Metadata

- Emit metadata that describes generated fixture counts and coverage mapping.
- Include counts for positive, negative, boundary, and API payload fixtures.
- Report coverage percentage and uncovered constraints in the summary.

## Gap Reports

- Generate reports listing missing constraints, incomplete schemas, and uncovered requirements.
- Provide coverage metrics and suggested actions for additional rule definition.
- Note when input validation or API contract details are insufficient to generate complete fixtures.

## Self-Check

Before returning output, validate that:
1. All fixtures conform to the schemas in `assets/fixture_templates.yaml`.
2. All traceability links reference real artifacts from the input.
3. Coverage percentage meets the expected target (default 100%).
4. No fixtures are orphans without a test_case_id or traceability link.
5. All IDs and payloads derive from deterministic seeds.

## Error Handling

- Report missing constraints or schema details.
- Flag incomplete schemas that prevent reliable fixture generation.

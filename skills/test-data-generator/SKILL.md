---
name: test-data-generator
description: "Generate deterministic test-data fixtures and payloads from test cases, validation rules, and API contracts. Use when test cases have been designed and concrete fixture data is needed for execution."
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
metadata:
  version: 1.2
  category: Test Case Generation
  phase: Test Data Generation
---

# Test Data Generator

## Purpose

Generate deterministic test-data fixtures and payloads for generated test cases.

## Inputs

- Test Cases (from test-design pipeline step)
- Validation Rules
- API Contracts (optional)

## Configuration

| Setting | Description | Default |
|---|---|---|
| `deterministic_seed` | Seed value for repeatable fixture generation. | `42` |
| `max_boundary_variations` | Maximum number of boundary variations per numeric or length constraint. | `3` |
| `output_format` | Output format for fixtures and gap reports. | `JSON` |

Configuration can be supplied via the skill invocation context or as part of the input payload.

## Output Contract

All outputs MUST conform to the schemas defined in `assets/fixture_templates.yaml`. The following top-level sections are produced:

- **Positive Fixtures** — Valid examples that satisfy all schema and validation constraints.
- **Negative Fixtures** — Invalid examples that intentionally violate one or more constraints.
- **Boundary Fixtures** — Edge values at or just beyond constraint limits.
- **API Payload Examples** — Structured request and response payloads for API test execution.
- **Summary Metadata** — Coverage overview, fixture counts, generation rationale, and constraint mapping.
- **Gap Reports** — Coverage gaps, missing validations, and suggested extensions.

Canonical traceability contract: Every fixture MUST include a `traceability_links` array. Each link entry uses the `source_id` + `type` pattern, where `source_id` is the ID of the source artifact and `type` is one of `requirement`, `validation`, or `api` (see `assets/fixture_templates.yaml` for the exact schema). Downstream consumers should expect this same format when reading fixture outputs.

## Validation Rules

- Map field-level rules such as required/optional, min/max, pattern, format, enum values, default values, and conditional constraints.
- Respect API contract rules like content type, accepted formats, authentication requirements, and response schemas.
- Translate business validation rules (for example, cross-field, dependency, or decision rules) into concrete fixture variants.
- Flag and document missing or ambiguous validation rules so they can be reviewed before test execution.

## Advanced Schema Handling

When the input API contract or validation rules use complex JSON Schema constructs, follow these rules:

### `$ref` references
- Resolve every `$ref` before generating fixtures. Inline the target schema definition in place of the `$ref` key.
- If the `$ref` target is not present in the input (e.g., an external file not provided), flag the unresolvable reference in the gaps report and skip that field.

### Composition keywords (`allOf`, `oneOf`, `anyOf`)
- **`allOf`**: Merge all sub-schemas together. Generate one fixture that satisfies every sub-schema simultaneously. If the sub-schemas conflict (e.g., `type: string` in one and `type: integer` in another), flag as an incomplete schema and skip.
- **`oneOf`**: Generate one fixture per sub-schema variant. Label each variant with the sub-schema index or description in `traceability_links.label`. If a sub-schema cannot be satisfied given the available data, include a gap report entry.
- **`anyOf`**: Same as `oneOf` — generate one fixture per variant. Unlike `oneOf`, a fixture that satisfies multiple variants simultaneously is also valid; generate the combined fixture as an additional variant.
- **`not`**: Do not attempt to generate a fixture that satisfies a `not` schema. Add a note to the gaps report: "`not` constraint skipped — cannot deterministically enumerate the complement set."

### Conditional validation (`if`/`then`/`else`)
- Evaluate the `if` schema against the generated payload. If the payload satisfies `if`, apply the `then` schema as an additional constraint. If it does not, apply the `else` schema.
- Generate fixture variants for both branches (if-condition-true and if-condition-false) when both produce distinct constraints.

### Additional JSON Schema draft-07+ keywords
- **`patternProperties`**: Generate keys that match the regex pattern and ensure each generated value conforms to the sub-schema. If the regex is too broad or impossible to enumerate, generate at least one valid example and flag the remaining pattern coverage in the gaps report.
- **`dependentRequired`**: If field `A` is present, field `B` is required. Generate fixture variants for both the dependency-present and dependency-absent branches and document the dependency in the fixture notes.
- **`dependentSchemas`**: Apply the dependent schema only when the dependency field is present; generate a variant that satisfies the dependency and a variant that does not, when both are meaningful.

### Recursion and depth guardrails
- For nested schemas deeper than 3 levels, cap recursive generation to 3 levels and record the omitted deeper branch in the gaps report.
- For circular `$ref` patterns (for example, self-referential objects), stop recursion after 2 reference expansions and flag the circular dependency as an incomplete schema.

### String format constraints
For fields with a JSON Schema `format`, use the following generation rules:

| Format | Positive fixture rule | Boundary / negative hints |
|---|---|---|
| `date` | `YYYY-MM-DD` in range 2024-01-01 to 2026-12-31, include Feb 29 in leap years | Invalid month 13, invalid day 32, non-date string |
| `date-time` | ISO 8601: `2025-04-15T14:30:00Z` | Missing T separator, timezone offset out of range |
| `time` | `HH:MM:SS` (24-hour), e.g. `14:30:00` | Hour ≥ 24, minute ≥ 60 |
| `uuid` | UUID v4: `f47ac10b-58cc-4372-a567-0e02b2c3d479` | Truncated, non-hex chars, missing dashes |
| `email` | Local part + `@` + domain, e.g. `user@example.com` | Missing `@`, multiple `@`, invalid chars |
| `uri` | `https://example.com/resource` | Missing scheme, invalid host |
| `hostname` | `api.example.com` | Leading/trailing dot, segment > 63 chars |
| `ipv4` | `192.168.1.1` | Octets > 255, missing octet |
| `ipv6` | `2001:0db8:85a3::8a2e:0370:7334` | Too many segments, invalid hex |
| `byte` (base64) | Base64-encoded string, e.g. `SGVsbG8gV29ybGQ=` | Invalid base64 chars, wrong padding |
| `binary` | Hex-encoded string, e.g. `48656c6c6f` | Odd-length hex, non-hex chars |

### Array constraints
When the schema defines array-type properties with constraints, apply boundary-variation treatment following the same `max_boundary_variations` configuration used for numeric/string constraints:

| Constraint | Positive fixture | Boundary / negative fixture |
|---|---|---|
| `minItems: N` | Array with N items | Empty array (if N > 0), array with N-1 items |
| `maxItems: N` | Array with N items | Array with N+1 items (if data allows) |
| `uniqueItems: true` | All items distinct | Duplicate items (negative fixture) |
| `contains: {schema}` | Array includes at least one item matching the schema | No item matching the schema (negative fixture) |
| `prefixItems` (tuple) | Array length matching the defined prefix, each position conforming to its schema | Wrong type at a given position (negative fixture) |

## API Testing Rules

- Generate request payloads aligned to the API contract, including positive success cases and negative contract-breaking cases.
- Include data variants for HTTP status validation: 200/201 success, 400 validation error, 401/403 authorization error, 404 not found, and 500 server error where applicable.
- Create both valid and invalid header, query, path, and body combinations when API contracts define those inputs.
- Support multiple content types if the API contract specifies them (for example, JSON, form-data, XML).
- Include fixture variants for rate-limit, pagination, and idempotency behaviors when the API contract defines them. Examples: `X-RateLimit-Remaining`, `page`, `limit`, `cursor`, and `Idempotency-Key`.
- Assign status codes to response payload fixtures according to the scenario type:

  | Scenario type | Status code(s) |
  |---|---|
  | Positive / resource creation | `200` or `201` |
  | Validation error | `400` or `422` |
  | Authentication / authorization error | `401` or `403` |
  | Resource not found | `404` |
  | Server error | `500` |

- Ensure response payload fixtures match the expected output schema and error response formats.

## Data Consistency

- **Foreign-key integrity** — When generating fixtures for related objects (e.g., an order references a customer ID), ensure the referenced value matches across fixtures. Generate shared constants (IDs, dates, enums, codes) once and reuse across all related fixtures in the same set.
- **Cross-fixture correlation** — Document cross-fixture relationships (shared IDs, dependent fields) in `summary_metadata.notes` so downstream consumers understand how fixtures relate.
- **Deterministic constants** — Use the `deterministic_seed` configuration value to derive all shared constants, ensuring the same input always produces the same cross-fixture relationships. The bundled script `scripts/deterministic_helpers.py` can be executed to produce repeatable ID sequences, timestamps, usernames, and email addresses from the seed — use it when generating constants for a new fixture set.
- **API payload consistency** — When generating both request and response payloads for the same endpoint, ensure field types and structures are consistent between them.

## Example

### Input (excerpt)
```json
{
  "test_cases": [
    {"id": "TC-001", "title": "Create user successfully", "type": "positive", "linked_requirements": ["REQ-001"]}
  ],
  "validation_rules": [
    "Username must be 3-20 characters.",
    "Email must match a valid format."
  ],
  "api_contracts": [
    {"path": "/users", "method": "post", "request_schema": {"type": "object", "required": ["username", "email"],
      "properties": {"username": {"type": "string", "minLength": 3, "maxLength": 20}, "email": {"type": "string", "format": "email"}}}}
  ]
}
```

### Output (excerpt)
```json
{
  "positive_fixtures": [
    {"test_case_id": "TC-001", "payload": {"username": "john_doe", "email": "john@example.com"},
     "description": "Valid user creation payload.", "traceability_links": [{"source_id": "REQ-001", "type": "requirement"}]}
  ],
  "negative_fixtures": [
    {"test_case_id": "TC-001-NEG-01", "payload": {"username": "ab", "email": "john@example.com"},
     "invalid_reason": "Username below minimum length (3).", "traceability_links": [{"source_id": "VR-001", "type": "validation", "label": "username-min-length"}]}
  ],
  "boundary_fixtures": [
    {"test_case_id": "TC-001-BND-01", "payload": {"username": "abc", "email": "john@example.com"},
     "boundary_condition": "Username at minimum length boundary (3).", "traceability_links": [{"source_id": "VR-001", "type": "validation", "label": "username-boundary-min"}]}
  ],
  "summary_metadata": {"fixture_count": 3, "positive_count": 1, "negative_count": 1, "boundary_count": 1, "coverage_percentage": 100,
    "notes": "All constraints covered for TC-001. Traceability uses source_id+type pattern per fixture_templates.yaml."},
  "gaps_report": {"coverage_percentage": 100, "missing_constraints": [], "uncovered_requirements": [], "notes": "No gaps identified."}
}
```

## Anti-Hallucination Rules

- Never invent field constraints, validation rules, or API responses that are not present in the input.
- Generate data only for fields explicitly defined in the schema.
- Use only enum values listed in the schema; if no enum is defined, use values consistent with the field's type constraints.
- Do not fabricate API endpoints, HTTP methods, status codes, or response schemas — only use what is given.
- When input is ambiguous or insufficient, flag it in the gaps_report rather than inventing missing details.

## Self-Check / Validation

Before returning any output, Claude MUST perform the following checks:

1. **Schema conformance** — Validate every generated fixture against the schemas in `assets/fixture_templates.yaml`. Reject and regenerate any fixture that does not match the expected structure, field types, or required properties.
2. **Traceability integrity** — Verify that every `traceability_links` entry references a real requirement ID, validation rule, or API contract from the input. Flag orphaned links in the gaps report.
3. **Coverage threshold** — Confirm that coverage_percentage meets the expected target (default 100%). If not, include uncovered items in the gaps report.
4. **No orphaned fixtures** — Check that every fixture has a corresponding `test_case_id` or traceability entry linking it to a source artifact. Report any orphaned fixtures.
5. **Determinism proof** — Verify that all generated IDs and payloads derive from deterministic seeds, not random values.
6. **Validation script pass** — If `scripts/validate_fixtures.py` is available, run it against the generated JSON and fix any failing entries before returning output.

If any check fails, correct the output before returning it and note the correction in `summary_metadata.notes`.

## Pipeline Integration

This skill is designed to consume output from the `test-design-generator` skill. The expected handoff contract is:

### Input shape (from test-design-generator)

This skill normalizes upstream inputs automatically. The input `test_cases` array items should match the following shape (aligned with `test-design-generator` output):

```json
{
  "id": "TC-001",
  "title": "Create user successfully",
  "type": "positive|negative|boundary|validation|contract",
  "linked_requirements": ["REQ-001"],
  "steps": [],
  "traceability_links": [
    {"test_id": "TC-001", "source_id": "REQ-001", "type": "requirement"}
  ]
}
```

If the input test cases use a different structure (e.g., `positive_tests`/`negative_tests` as separate arrays), normalize them into this unified shape before generating fixtures. This skill owns the normalization step automatically; do not assume the caller pre-normalizes input. Map the following common variants automatically:

| test-design-generator field | This skill's expected field |
|---|---|
| `id` inside test objects | `id` (same) |
| Separate arrays (`positive_tests`, `negative_tests`, etc.) | Unified `test_cases` array with `type` discriminator |
| `traceability_links` on output artifact | `traceability_links` on each test case (inline) |

### Output shape (to downstream consumers)

The output of this skill is consumed by test-execution tooling. Downstream consumers should read:
- `positive_fixtures`, `negative_fixtures`, `boundary_fixtures` — the concrete payloads for test execution
- `api_payload_examples.requests` and `.responses` — for API-level test assertions
- `summary_metadata` — for coverage reporting and gap visibility

### Troubleshooting pipeline breaks

- If the input `test_cases` array is empty or missing entirely, treat the `validation_rules` and `api_contracts` as the sole source of fixture generation.
- If `validation_rules` is an empty array but test cases exist, generate fixtures from the API contract only and flag missing validation rules in the gaps report.
- If neither test cases nor API contracts are provided, return a gaps report with the note "Insufficient input to generate fixtures" and do not produce a fixture file.

## Gap Reports

- Generate gap reports that highlight uncovered validation rules, missing field coverage, and unsupported constraint types.
- Include metrics such as coverage percentage, uncovered requirement IDs, and remaining data gaps by field or endpoint.
- Identify areas where the input does not provide enough rule detail to create meaningful fixtures.
- Provide suggested next steps, such as adding validation rules, API contract details, or additional acceptance criteria.
- Output gap reports in JSON or Markdown format when requested by the skill invocation.

## Error Handling

- Report missing or incomplete validation rules that prevent fixture generation.
- Report invalid or inconsistent API contract definitions.
- Report when required output targets are undefined or unsupported.
- Continue generating available data while clearly documenting any unresolved gaps.

## Success Criteria

- Generated fixtures align precisely with validation rules and API contract schemas.
- Generated outputs include positive, negative, and boundary data for all supported constraints.
- The summary metadata clearly describes fixture coverage, data types, and rule mapping.
- Gap reports surface missing coverage and provide actionable recommendations.
- Outputs are deterministic and repeatable for the same input set.

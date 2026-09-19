# Test Design Patterns

This document describes common test design patterns for generating structured test cases.

## Positive Test Patterns

- Validate that each acceptance criterion maps to at least one happy-path test.
- Use the normal expected behavior described in the requirement.
- Preserve the original requirements text in traceability metadata.

## Negative Test Patterns

- Generate tests for invalid inputs and unsupported states.
- Map each validation rule to at least one negative test.
- Confirm failure behavior and error handling.

## Boundary Test Patterns

- Create tests at minimum and maximum valid values.
- Include edge cases for numeric ranges, string lengths, and date boundaries.
- When constraints are open-ended, generate tests for the nearest known boundary.

## Validation Test Patterns

- Validate required fields, formats, and data type constraints.
- Include tests for missing required values and invalid formats.
- Cover rule-driven constraints explicitly present in the source.

## Contract Test Patterns

- Generate contract checks only when API contract models are available.
- Validate request payload structure, response structure, and status codes.
- Preserve endpoint and operation traceability.

## Traceability Patterns

- Link each generated test back to requirement IDs.
- Include acceptance criteria and validation rule references.
- Preserve deterministic test case identifiers.

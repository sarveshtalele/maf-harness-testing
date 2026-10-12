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

## Oracle Patterns

Choose the oracle kind first; the expected result must follow from it.

- `specified`: quote the documentation or specification sentence that states the behaviour (README, SKILL.md, docs, references). Quote verbatim with file and line.
- `derived`: quote the implementation lines the expected value follows from (the target's source). Derive the value step by step from the fixture.
- `metamorphic`: state a relation between two runs when no single expected value is documented. Useful relations for analysis, ranking and scoring tools:
  - determinism: the same input twice gives the same output;
  - irrelevant-input invariance: adding an input the rule ignores (for example an unrelated file) does not change the top candidate or the score;
  - order invariance: reordering inputs does not change the result.
- `golden`: a scenario fixture (its files, values or repository layout) whose answer is computed by hand from a documented rule, for example a scoring formula in references/*.md. Quote the rule as `source`, describe the fixture and the derivation in `relation`. Never copy the answer from the implementation's output; a human approves golden answers at the design gate.
- `property`: a stated property of the output, such as "every score is between 0 and 1" or "the list is sorted by descending score". State the property in `relation`.
- `characterization`: records what the code does today. Use it only when no statement above applies. It is reported as characterization and is never counted as verified.

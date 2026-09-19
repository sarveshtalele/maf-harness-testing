---
name: junit-generator
description: Generate standalone JUnit 5 Java test skeletons and a traceability manifest from structured test cases, fixtures, or test-design-generator output. Use when creating new JUnit automation from requirements or test designs; do not use for TestNG, Spock, Kotlin tests, or extending existing handwritten JUnit classes.
---

# JUnit Generator

Generate reviewable JUnit Jupiter scaffolds without inventing application behavior. Read [JUnit guidance](references/junit_best_practices.md) when selecting annotations, fixtures, or assertions.

## Input and pipeline integration

Accept either `{ "test_cases": [...] }` or test-design-generator output. Normalize each upstream array into `test_cases`, setting `type` as follows: `positive_tests` → `positive`, `negative_tests` → `negative`, `boundary_tests` → `boundary`, and all other `*_tests` arrays → their prefix. Preserve `id`, `title`/`name`, `steps`, `expected_result`, `fixture_id`, and traceability links. Supply fixtures as `{ "fixtures": [{ "id": "FX-1", "data": {...} }] }`.

For programmatic output, run `node scripts/generate_junit.js --input <input.json> --output <dir> [--package <java.package>]`. The script requires Node.js; generated projects require JUnit 5 Jupiter.

## Output contract

- Write one `Test{sanitized-id}.java` class per test case and `test_classes.json` beside it.
- Write files directly to `--output`; include `package <value>;` only when `--package` is supplied.
- Record `{ id, class, file, path, type }` for every generated class in the manifest.
- Use Java-valid identifiers, JUnit 5 imports, a class-level traceability comment, and AAA method bodies.

## Generation rules

- Generate a standard `@Test` for positive and unclassified cases.
- Generate `@Test`, `@Tag("negative")`, and `assertThrows` scaffolding for negative cases.
- Generate `@ParameterizedTest` plus `@MethodSource` scaffolding for boundary cases.
- Preserve a non-empty `expected_result` in a TODO-linked assertion comment; never claim an unverified assertion is implemented.
- Map scalar fixture values to private fields initialized in `@BeforeEach`; map lists to `@MethodSource` candidates; map nested objects to static inner data classes/placeholders. Omit fixture references when the fixture is absent and emit a warning.
- Do not invent endpoints, payload fields, method calls, expected values, or domain classes.

## Edge cases and failures

- If a test has no steps, produce one placeholder test method.
- If no test cases are present, write an empty manifest and exit successfully with a warning.
- Treat a missing input file as `MISSING_INPUT`, invalid JSON as `INVALID_JSON` (include parse location when available), and a case missing `id` as `MISSING_REQUIRED_FIELD`; return a non-zero exit code for the first two and skip invalid individual cases for the third.

## Worked example

`{ "negative_tests": [{ "id": "TC-9", "title": "Reject bad token", "expected_result": "Unauthorized" }] }` produces `TestTC_9.java` with `@Tag("negative")`, `assertThrows`, and a `// TODO: Expected result: Unauthorized` assertion note.

## Self-check

Before returning output, verify every valid input case appears in the manifest, Java identifiers and filenames are deterministic, imports match generated annotations, traceability is present, and no assertion or API detail was invented.

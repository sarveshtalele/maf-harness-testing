---
name: junit-generator
description: Generate executable Java tests in the repository’s declared JUnit version from approved test cases and source-backed fixtures. Use for JUnit 4 or 5 test generation; do not invent framework dependencies or emit placeholder skeletons for application qualification.
---

# JUnit Generator

Generate executable tests using the repository’s declared JUnit version without inventing application behavior. Detect JUnit 4 (`org.junit.Test`) vs JUnit 5 (`org.junit.jupiter`) from `pom.xml` or `build.gradle` and never override it. Read [JUnit guidance](references/junit_best_practices.md) when selecting annotations, fixtures, or assertions.

## Input and pipeline integration

Accept either `{ "test_cases": [...] }` or test-design-generator output. Normalize each upstream array into `test_cases`, setting `type` as follows: `positive_tests` → `positive`, `negative_tests` → `negative`, `boundary_tests` → `boundary`, and all other `*_tests` arrays → their prefix. Preserve `id`, `title`/`name`, `steps`, `expected_result`, `fixture_id`, and traceability links. Supply fixtures as `{ "fixtures": [{ "id": "FX-1", "data": {...} }] }`.

The bundled `scripts/generate_junit.js` is a JUnit 5 scaffold example, not a qualifying test generator. It is not an approved model-executable script. The harness receives structured source output, rejects placeholders and validates the repository’s actual framework. Do not run that script or introduce a different JUnit generation than the build files declare.

## Output contract

- Return structured source files under the harness generation contract, one concrete test per approved case/file, with stable case IDs and repository-compatible paths/packages.
- Use Java-valid identifiers, imports for the repository's JUnit version, a traceability comment, and arrange/act/assert method bodies. The host writes approved files after validation.

## Generation rules

- Generate a standard `@Test` for positive and unclassified cases.
- Negative cases must assert the actual source-supported rejection or error; use exception APIs supported by the declared JUnit version.
- Boundary cases must supply real values and assert behavior established by source. Use parameterized annotations only when available in the repository's dependencies and permitted by the one-test-per-case contract.
- Implement `expected_result` as a meaningful assertion. A comment, TODO, placeholder or constant assertion is not a test.
- Build fixtures using real constructors and methods, obeying source constraints. If a required fixture or behavior cannot be established, return an explicit design/evidence gap instead of inventing it.
- Do not invent endpoints, payload fields, method calls, expected values, or domain classes.
- Do not use `@InjectMocks` when the constructor acquires static or external resources (singletons, JNDI, files); construct the object explicitly or use the repository's public API.
- JavaFX code needs toolkit initialisation (for example `new JFXPanel()` or `Platform.startup`) and FX-thread execution for node access.

## Edge cases and failures

- If a case lacks executable steps, revise the design or report the missing evidence; never emit placeholders.
- If no approved cases are present, stop with an explicit missing-input error; an empty suite cannot pass.
- Return an explicit input error for missing inputs, invalid JSON or missing case IDs. Never silently skip an approved case.

## Worked example

A case titled "Reject bad token" with only "Unauthorized" as its expected result is insufficient to generate code. Read the real authentication method, fixture contract and exception/response behavior first. If those are unavailable, request a design revision; do not turn the title into a guessed API or an assertion comment.

## Self-check

Before returning output, verify every approved case appears exactly once in the structured files, Java identifiers and filenames are deterministic, imports match available dependencies, traceability is present, and assertions use verified APIs and source-backed behavior.

## Strict source evidence

Invoke and assert each method claimed by a Java unit requirement on its source type. Constructors and comments do not cover setters. Positive literal fixtures must obey source `@Size` constraints. Complement existing tests; exact duplicate test bodies and constant assertions are rejected. Existing unit/integration regression and generated-case execution are separate evidence scopes. Static links and invocation witnesses never imply measured line, branch, mutation or complete application coverage.

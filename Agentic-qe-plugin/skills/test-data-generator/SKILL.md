---
name: test-data-generator
description: Select safe, source-backed E2E example values and boundaries without creating executable fixtures.
---

# E2E data examples

Adapted from MAF `skills/test-data-generator`. For each case, use explicit business validation or contract constraints to choose a valid example, an invalid example and a boundary example where each is meaningful. Cite the case/control and exact rule for every value. For composed schemas and conditional rules, represent only resolvable branches; record unresolved `$ref`, recursive or conflicting constraints as gaps. Never use real credentials or customer data.

Use deterministic synthetic identifiers so a case can be reviewed again with the same example. An illustrative value may clarify a step but must be labeled illustrative and cannot establish an expected result. Leave unknown values unspecified. Do not generate fixture files, payloads for execution, API calls or target test code.

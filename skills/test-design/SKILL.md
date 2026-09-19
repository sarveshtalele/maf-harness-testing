---
name: test-design
description: Convert an approved test plan plus repository evidence into traceable positive, negative, boundary, error, security, and regression test cases. Use only after the test-planning stage is approved.
compatibility: Python host using Microsoft Agent Framework Agent Skills.
metadata:
  sdlc: Testing
  source-inspiration: awesome-agent-skills test-design-generator
---

# Test Design

## Goal
Generate test cases that are grounded in the approved plan and repository evidence. The host application supplies the output JSON schema and replaces case IDs deterministically after validation.

## Rules
1. Generate positive coverage for explicit behavior visible in the evidence.
2. Generate negative or error coverage only where input validation, failure paths, authorization checks, or exceptions are visible in evidence.
3. Generate boundary cases only when a real bound, length, range, enum, date rule, or similar constraint is explicit.
4. Generate contract cases only when explicit `api_contracts` are supplied, and link every contract test to a matching `api_contract` ID. If no explicit API contracts exist, keep `contract_tests` empty and represent API-like behavior as ordinary positive, negative, integration, or validation coverage.
5. Every test must cite evidence file paths in `evidence_files`; use `traceability` for plan areas, symbols, routes, or explicit rules.
6. Use P0 only for evidence-backed release blocking, security, payment, or data-loss behavior. Use P1 for core behavior and P2 for secondary behavior.
7. Never invent business rules, expected error codes, selectors, endpoints, database constraints, or security requirements.
8. Put ambiguous, incomplete, and untestable items in `gaps` rather than fabricating a test.
9. The same approved inputs should yield semantically stable cases. The host will derive reproducible UUIDv5-based IDs.

## Output
Return exactly one JSON object matching the schema supplied by the host application. Do not wrap it in Markdown fences and do not create files.

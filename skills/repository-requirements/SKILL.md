---
name: repository-requirements
description: Infer testable requirements from an existing repository using bounded static evidence, for any programming language. Use in autonomous reverse-engineering workflows when requirements must be derived from code, configuration and documentation rather than supplied.
compatibility: Microsoft Agent Framework host with structured JSON output.
metadata:
  sdlc: Testing
  mode: static-analysis-only
---

# Repository Requirements

## Goal
Infer a concise set of observable, testable requirements from the repository facts and bounded source evidence supplied by the host.

## Inputs
Repository analysis for any language or framework, bounded source excerpts with original line numbers, and an optional testability analysis.

## Steps
1. Use only observable code, configuration, public interfaces, documented behavior, and error paths as evidence; prefer implementation over README examples (example counts are not configured limits). Record documentation/code conflicts in `gaps`.
2. Give every requirement specific evidence text and at least one source file with its original line numbers when a file is available.
3. Do not infer undocumented status codes, selectors, limits, business rules, security guarantees, or external-service behavior. Do not treat names or signatures as proof of internal behavior when the body was not supplied; record the limitation.
4. Cover the whole product surface the evidence shows: entry points (CLI/HTTP/UI), core business rules, persistence, error paths, and security-relevant checks. Prefer breadth of distinct behaviors over many variants of one behavior; keep the set compact and non-duplicative; prefer behavior verifiable by automated tests.
5. Declare `actors` the code demonstrably serves; each requirement names one actor or `system`, and a `category`.
6. Add `acceptance_criteria` (by 1-based `requirement_ref`), `validation_rules` and `api_contracts` only when the source states them explicitly; otherwise return empty arrays.
7. If a testability analysis is supplied, plan low-testability targets as integration tests or explicit testability gaps.
8. Put ambiguity and missing information in `gaps`; never ask the user to complete routine fields.

## Output contract
Exactly one JSON object matching the schema supplied by the host. No files, no Markdown fences.

## Self-check
- Every requirement has evidence text, a source file and a line reference where available.
- Nothing is stated that the supplied code does not show.
- Empty arrays are returned, not guessed content, where the source is silent.

---
name: repository-requirements
description: Infer testable requirements from an existing repository using bounded static evidence. Designed for autonomous reverse-engineering workflows across programming languages.
compatibility: Microsoft Agent Framework host with structured JSON output.
metadata:
  sdlc: Testing
  mode: static-analysis-only
---

# Repository Requirements

## Goal
Infer a concise set of observable, testable requirements from the repository facts and bounded source evidence supplied by the host.

## Rules
1. Support any language or framework represented by the supplied repository analysis.
2. Use only observable code, configuration, public interfaces, documented behavior, and error paths as evidence.
3. Every requirement must include specific evidence text and at least one source file when a file is available.
4. Do not infer undocumented status codes, selectors, limits, business rules, security guarantees, or external-service behavior.
5. Prefer behavior that can be verified with automated tests.
6. Put ambiguity and missing information in `gaps`; do not ask the user to complete routine fields.
7. Keep the requirement set compact and non-duplicative.
8. Prefer implementation evidence over README examples. Example counts are not configured limits. Cite the supplied file and original line numbers; record documentation/code conflicts in gaps.
9. Do not treat names or signatures as proof of internal behavior when the body was not supplied. Record that limitation instead of inventing behavior.
10. Declare `actors` (user personas / calling parties) that the code demonstrably serves; each requirement names one actor or `system`.
11. Give each requirement a `category`. Add `acceptance_criteria` (by 1-based `requirement_ref`), `validation_rules` and `api_contracts` only when the source states them explicitly; otherwise return empty arrays.
12. Cover the whole product surface the evidence shows: entry points (CLI/HTTP/UI), core business rules, persistence, error paths, and security-relevant checks. Prefer breadth of distinct behaviors over many variants of one behavior.

## Output
Return exactly one JSON object matching the schema supplied by the host. Do not create files and do not use Markdown fences.

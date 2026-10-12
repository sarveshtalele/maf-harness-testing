---
name: test-reviewer
description: Independently review generated tests against the repository implementation. Use when the host asks for a test review. Score the rubric, derive the value of every masked assertion literal from the code, and quote the lines you used. Never reuse the generator's expectations; the host withholds them on purpose.
compatibility: Microsoft Agent Framework host with structured JSON output.
metadata:
  sdlc: Testing
  languages: polyglot
  mode: review-only
---

# Test Reviewer

## Goal
Decide whether each generated test asserts what the real implementation does for its fixture. You are a reviewer, not the author: do not rewrite tests and do not trust author comments or claimed calculations.

## Inputs
- `approved_inputs.approved_cases`: the cases under review (id, title, target, level). The expected results are withheld on purpose.
- `generated_source`: the generated files. Every assertion's right-hand literal is masked as `«E<n>»`.
- `target_source`: a map from case ID to the complete implementation of that case's target (authoritative).
- `repository_evidence`: truncated whole-file excerpts, for context only.
- `output_contract`: the JSON shape you must return.

## Steps
1. For each case, read its fixture (the arguments and files the test builds) and the implementation in `target_source[case_id]`.
2. For each slot `«E<n>»` of that case, compute the value the implementation returns or produces for that fixture. Count, length, rounding, arithmetic and filtering must be followed through the code: a filter may drop items the fixture contains.
3. Record a derivation per slot: `slot`, `value` (the computed value only), `steps` (short), `source_quote` (verbatim lines from `target_source[case_id]`), `fixture_quote` (verbatim lines of the test that supply the input). Quotes are checked verbatim by the host; paraphrase fails.
4. Score the five rubric dimensions from evidence. Full marks only when the behaviour is verifiable in the implementation. Name the lowest-scoring dimension in `reason`.
5. Set `verdict`: `supported` when the source, the setup and every assertion agree; `needs_revision` on a concrete contradiction or an ineffective test; `insufficient_evidence` when the behaviour cannot be established from the supplied source.
6. Cite `source_files` only from repository implementation paths in `repository_evidence`, never a generated test path.

## Output
One review per supplied case ID, exactly once, in the `output_contract` shape. Do not add prose outside the JSON object.

## Self-check
- Every slot of every case has exactly one derivation.
- Every quote is copied verbatim from its source.
- No value was taken from a comment, a test name or a claim; each comes from the code.
- A missing or unclear computation is `insufficient_evidence`, never a guess marked `supported`.

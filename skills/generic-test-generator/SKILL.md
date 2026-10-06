---
name: generic-test-generator
description: Generate executable, traceable automated tests for any language from approved test-design cases, without running code or installing dependencies. Use when the host asks for test source for a batch of approved cases; read the reference file for the detected language (Python, Java, JavaScript, TypeScript, C#).
compatibility: Microsoft Agent Framework host with structured JSON output.
metadata:
  sdlc: Testing
  languages: polyglot
  mode: generation-only
---

# Generic Test Generator

## Goal
Write real test source implementing every approved test-design case, in the repository's detected language, framework, build system and existing test conventions.

## Inputs
Approved cases for the current batch, requirements, repository analysis (`languages`, `source_files`, test frameworks, declared dependencies), `target_source`, existing tests, optional `testability` section, and `generation_policy` (including `language_rules`).

## Steps
1. Detect the language(s) from the repository analysis and read the matching file with `read_skill_resource`: `references/python.md`, `references/java.md`, `references/javascript.md`, `references/typescript.md` or `references/csharp.md`. Follow it together with `generation_policy.language_rules`.
2. Choose the framework from repository evidence (declared dependencies, existing tests). Only if none exists, pick the mainstream native default for the stack. For ui/e2e/accessibility cases keep an existing browser framework, else use Playwright; never replace native unit/integration tests with it.
3. For each case, read `target_source` and the existing tests. Plan the call that exercises the method that actually implements the behavior, and the assertion on its observable result.
4. Imports: never convert a filesystem path into an import by normalising `-`, spaces or leading digits; import only modules proven by `source_files`; reference only members present in the source (never inject or assume absent fields).
5. Prefer the repository's existing public execution API and existing test patterns/fixtures (for example a registry fixture) over importing internals.
6. Respect a supplied `testability` section: for low-testability targets write an integration/behaviour-level test, not mocks of static acquisition.
7. Write assertions that can fail. No tautologies (for example a property map that is always non-null), constants, mock-only checks, TODO, placeholder, pass-only or skipped tests. Positive fixtures must obey source constraints; invalid values belong only in explicit negative/boundary cases.
8. Mock network, cloud, database, AI-provider, process and filesystem boundaries for unit cases, never the behavior under test. Follow each case's `level`; ui/e2e/accessibility read the target from `BASE_URL`.
9. Emit exactly one file per case ID with exactly one test declaration and one `case_ids` entry; the host adds the `CASE_ID` marker. Use descriptive file and test names; use only imports the test references.
10. Do not execute tests, install dependencies or edit repository files; the host validates statically, then runs after approval. Keep tests deterministic and self-contained.

## Output contract
Exactly one JSON object matching the host schema; no Markdown fences, preamble or wrapper. Each file's source is `lines`: one array item per source line, no trailing newlines, indentation preserved. Response limits apply per batch; implement every supplied case, never silently drop one. Never claim measured runtime coverage from static links.

## Self-check
- Every supplied case ID appears once, with one declaration and one assertion that can fail.
- Every import resolves to `source_files` or a declared dependency; every referenced member exists in the source.
- The language reference was applied; no rule from another language was used.
- Existing test bodies are not repeated.

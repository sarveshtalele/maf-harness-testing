# repo/ (intake: clone, analyze, stack, routes, specs)

Read-only, bounded, deterministic. Repository code is never executed here.

## Key files
- `analyzer.py`: `validate_github_url`, `clone_repository`, `analyze_repository`, `build_repository_evidence`, `extract_target_sources` (brace-aware `_brace_body_end`), `collaborator_signatures`, `declared_dependencies`.
- `git_transport.py`: `run_git`, `read_operation` (file-backed git output, cancellable, process-tree lifetime).
- `reverse_engineering.py` `reverse_engineer`; `requirements.py` `requirements_from_facts`: inputs/outputs of the two locked host skills.
- `stack.py`: `StackProfile`, `detect_stack`. `routes.py`: `extract_routes`.
- `testability.py`: `analyze_testability`, `testability_for_targets`, `non_public_targets`, `retarget_cases`.
- `import_recipes.py`: `python_import_recipes`, `recipes_for_files` (literal bootstrap code for Python targets plain imports cannot reach).
- `preview.py` `preview_repository` (GitHub REST only); `specs.py` greenfield: `parse_markdown`, `parse_docx`, `parse_pdf` (needs pypdf), `fetch_jira`, `ingest_specs`.

## Flow
Stage 1: `clone_repository` (after approval) -> `analyze_repository` + `reverse_engineer` -> stage 2 `requirements_from_facts`. Greenfield: `ingest_specs` -> specs (`SPEC-<hash8>-<n>`) -> requirements. Later stages call `extract_target_sources`, `analyze_testability`, `extract_routes`.

## Invariants
- Keep the complete discovered declaration inventory; do not drop requirements to fit a budget (batch instead).
- Bounded reads (`_safe_read`, `_safe_source`); skip symlinks and escapes. The Jira token is memory-only, never written to disk.
- `import_recipes`/autofix output is deterministic: tie-break by test file location, never random.

## Test
`uv run pytest -q tests/test_repo_analyzer.py tests/test_source_extraction.py tests/test_testability.py tests/test_import_recipes.py tests/test_greenfield_specs.py tests/test_clone_transport.py tests/test_repo_preview.py tests/test_stack_and_runners.py`

## Recipe: new detected framework/language
Extend `_detect_frameworks` in `analyzer.py` and `detect_stack`; add fixtures in the matching test; add the language to `LANGUAGE_RULES` (see developer guide).

# llm/ (prompt assembly, token ledger)

Model-facing accounting only; the client lives in `agent/runtime.py`.

## Key files
- `prompts.py`: prompt section order for provider prefix caching. `STAGE_ORDER`, `GENERATION_ORDER`, `GENERATION_INPUT_ORDER`, `ordered`, `build_prompt`, `generation_prompt`, `shared_prefix_chars`, `prefix_ratio`.
- `usage.py`: `DailyUsageLedger` (`snapshot`, `reserve`, `settle`) and `DailyLimitError`; the harness's only spending limit (`HARNESS_DAILY_TOKEN_LIMIT`, dir `HARNESS_USAGE_DIR`).

## Invariants
- **Register every new prompt section key in the right ORDER list** (`STAGE_ORDER` for planning/design/review, `GENERATION_ORDER` for generation, `GENERATION_INPUT_ORDER` inside `approved_inputs`). Stable keys go first, per-batch keys last; an unlisted key still works but lands after the listed ones and can break the cache prefix.
- Keep prompts language-neutral; language rules belong in `LANGUAGE_RULES` (`workflow_support.py`).
- Output schemas stay batch-independent (no per-batch enums).
- The ledger is per machine; tests redirect it (`tests/conftest.py` sets `HARNESS_USAGE_DIR`).

## Test
`uv run pytest -q tests/test_prompt_assembly.py tests/test_cache_metrics.py` (`docs/CACHING.md` explains the metrics).

## Recipe: add a prompt section
Put the key into the dict that the stage builds (in `workflow_steps/*.py`), add it to the correct ORDER tuple at the stable-vs-per-batch position, extend `tests/test_prompt_assembly.py`.

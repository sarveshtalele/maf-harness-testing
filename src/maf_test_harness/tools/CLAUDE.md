# tools/ (deterministic central tools)

Facts the host can compute exactly are answered here, not guessed by the model: import resolution, class constructors, routes, compile/collect results. Two surfaces: plain helpers used by the host, and a typed registry exposed to agents as MAF function tools.

## Key files
- `code_intel.py`: `RepoContext`, `resolve_import`, `module_api`, `find_symbol`, `read_source`, `java_class_index`, `installed_packages`.
- `autofix.py`: mechanical repairs before any model retry: `autofix_files`, `fix_python`, `fix_java`, `fix_import_recipes`, `drop_extra_tests`.
- `build_gate.py`: `try_build` compiles/collects generated tests in the prepared workspace (runs only after execution approval).
- `mock_pitfalls.py` `check_mock_pitfalls`, `python_contracts.py` `check_fixture_contracts`: static checks for known runtime-only mistakes.
- `registry.py`: `ToolSpec`, `ToolRegistry`, `ToolContext`, `ToolResult`, `object_schema`; every tool takes a validated args object and returns one `ToolResult` envelope; calls go through hooks and audit.
- `typed.py`: `default_registry` and `TYPED_STAGE_TOOLS` (stage -> allowed tool names). `env_tools.py`, `file_tools.py`, `skill_tools.py`: the typed tools (`ENV_TOOLS`, `FILE_TOOLS`, `SKILL_TOOLS`).
- `agent_tools.py`: `HarnessToolbox` (older central tools for stage agents) and `tool_instructions`.

## Invariants
- File tools have hard scopes: read inside repo/workspace, write only in writable roots (`file_tools.inside`).
- A tool that fetches software sets `downloads=True` (needs consent); one that writes sets `writes=True`.
- Tools return facts or errors, never raise to the model; keep outputs bounded (`MAX_RESULT_CHARS`).
- Autofixes must be unambiguous; ambiguous cases go back to the model with repair feedback.

## Test
`uv run pytest -q tests/test_central_tools.py tests/test_typed_tools.py tests/test_mock_pitfalls.py tests/test_skill_tools.py tests/test_state_and_fixture_contracts.py`

## Recipe: add a typed tool
1. Write `handler(ctx: ToolContext, args: dict) -> ToolResult` in the matching `*_tools.py` and a `ToolSpec` (with `object_schema`).
2. Append it to that module's tool tuple (`ENV_TOOLS` etc.).
3. Allow it per stage in `TYPED_STAGE_TOOLS` in `typed.py`.
4. Add a test in `tests/test_typed_tools.py`; mention it in `docs/HOOKS.md` / `docs/CATALOG.md` if it is user visible.

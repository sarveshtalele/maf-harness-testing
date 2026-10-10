# hooks/ (fail-closed hook engine)

User- and harness-defined checks before/after tool calls and model calls and at workflow events. Spec and examples: [../../../docs/HOOKS.md](../../../docs/HOOKS.md).

## Key files
- `events.py`: `HookEvent`, `Decision`, `strongest` (verdict combination).
- `engine.py`: `HookSpec`, `HookEngine`, `command_handler`, `python_handler`; runs matching hooks, combines verdicts, fail-closed.
- `builtin.py`: on by default: `secret_redaction`, `path_scope`, `download_consent`, `stage_budget`, `audit_events`; `build_builtins`, `redact`.
- `config.py`: `load_config`, `parse_config`, `config_paths` (`~/.maf-harness/hooks.json`, `./.harness/hooks.json`), `validate_entry`.
- `maf_bridge.py`: `HookFunctionMiddleware`, `HookChatMiddleware`, `hook_middleware`; a deny/stop raises MAF `MiddlewareFailure`.
- `agent_hooks_bridge.py`: optional AGENT-HOOKS-0.1 interceptor (`EngineInterceptor`, `sdk_available`); backend picked by `HARNESS_HOOKS_BACKEND`, hooks on/off by `HARNESS_HOOKS`.

## Invariants
- Fail-closed: a hook that errors or times out denies.
- Built-ins are on by default; a hooks.json `builtins` object may set them true/false/options (only `DEFAULT_BUILTINS` keys are valid). `HARNESS_HOOKS` is `enforce` (default), `evaluate_only` or `off`.
- Run cancel works through `MiddlewareFailure`; do not catch it.

## Test
`uv run pytest -q tests/test_hooks.py tests/test_hooks_builtin.py tests/test_hooks_config.py tests/test_hooks_maf.py tests/test_hooks_sdk.py tests/test_hooks_workflow.py`. `tests/conftest.py` already stubs `config_paths` so developer hook files never leak into tests.

## Recipe: add a built-in hook
Write the handler in `builtin.py`, register it in `build_builtins`, cover with a test in `tests/test_hooks_builtin.py`, and describe it in `docs/HOOKS.md`.

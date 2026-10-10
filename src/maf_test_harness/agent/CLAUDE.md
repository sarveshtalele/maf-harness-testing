# agent/ (model runtime)

Builds MAF agents and runs one stage call at a time with budget, trace, hook and cache middleware.

## Key files
- `runtime.py`: `MAFHarnessRuntime` (`run_stage`, `run_tool_agent`), `BudgetGuard`, `ToolTraceMiddleware`, `extract_json_object`, `response_cache_dir`, `AgentOutputError`.
- `personas.py`: `PERSONAS` (stage -> `AgentPersona`), `persona_instructions`, `chief_architect_persona` (design review).
- `prompt_settings.py`: user-editable prompt templates (`defaults`, `validate`, `load`, `save`); a run snapshots them in `prompts.json`.
- `diagnosis.py`: `DiagnosisNote`, `diagnosis_prompt`; LLM helper used only after deterministic recovery failed.

## Contracts
- A stage call is `run_stage(stage, skill_name, persona, prompt, state, persist, response_format=<models.py schema>, companions=(), temperature=0.0)`, keyword-only; tests replace it with `ScriptedStageRuntime` (`tests/fakes.py`).
- Per-stage model: `settings.model_for` / `HARNESS_STAGE_MODELS`; key/base URL from `settings.current()` (override > env > `.env`).
- Prompt text is assembled in `llm/prompts.py` (stable sections first for prefix caching).

## Gotchas
- Output schemas must stay batch-independent (no per-batch enums), or the provider prefix cache breaks.
- Never log or return API keys; UI shows last 4 only. Redaction lives in `hooks/builtin.py` and `ui/presentation.py`.
- Stop = `MiddlewareFailure` raised from hook middleware; do not swallow it.

## Test
`uv run pytest -q tests/test_native_maf.py tests/test_runtime_utils.py tests/test_response_cache.py tests/test_personas.py tests/test_diagnosis_agent.py`

## Recipes
- New persona: add to `PERSONAS` in `personas.py`; if it needs a response schema add it to `models.py`; add the stage prompt keys in `llm/prompts.py`.
- New middleware: add to the list built in `MAFHarnessRuntime`; add a test using `tests/fakes.py`.

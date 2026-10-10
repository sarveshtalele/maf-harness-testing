# monitoring/ (trace, observability report, telemetry)

Everything is rebuilt from what is on disk in the run directory; nothing here changes workflow state.

## Key files
- `trace.py`: `TraceRecorder` appends `trace.jsonl` (model runs, tool calls, skill loads, tokens, timing); `read_trace`, `redact`, `summarize_arguments`, `tool_category`.
- `report.py`: `build_report(run_dir, skills_root)` and `render_markdown`; powers the UI Observability tab (`GET /runs/<id>/observability`) and the CLI `maf-harness trace <run-dir> [--format md|json]`.
- `telemetry.py`: `configure_telemetry`, optional OpenTelemetry export of MAF spans.

## Contracts
- `workflow.py` attaches `TraceRecorder(run_dir / "trace.jsonl")` to the runtime; audit events go to `audit.jsonl` through `Harness.audit`.
- `ui/eta.py` reads `audit.jsonl` + `trace.jsonl` for ETA, so event names (e.g. `generation_attempt_saved`, `test_reviewer_result`) are an interface: do not rename without updating it.

## Gotchas
- Always `redact` arguments before they reach the trace; never store prompts with keys.
- `DEFAULT_SKILLS_ROOT` must point at the repo-root `skills/` (checked in `tests/test_module_layout.py`).

## Test
`uv run pytest -q tests/test_observability.py tests/test_attempt_audit.py tests/test_ui_eta.py`. See `docs/OBSERVABILITY.md`.

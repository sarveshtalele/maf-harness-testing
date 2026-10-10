# ui/ (loopback API and its services)

The Next.js app and desktop shell talk to this package over HTTP on 127.0.0.1 with a bearer token (`HARNESS_API_TOKEN`). Despite `__init__.py`'s old Streamlit wording, there is no Streamlit UI.

## Key files
- `api.py`: `LocalAPI` (`start`, `respond`, `rerun`, `stop_run`, `read`, `list_runs`, `delete_run`, `rename_run`, `set_case_budget`, `audit`), `make_server` (the `Handler.handle_request` route table), `main`.
- `runtime_service.py`: `RuntimeService` owns one event loop; every MAF client lives and closes on it (`submit`, `stop_active`, `close`).
- `evidence_api.py` `EvidenceAPI`: skills list/import/approve, `download`, `evidence`, `evidence_file`. `evidence.py`: contained read-only evidence access (`inventory`, `file_path`, `clean_content`).
- `settings_api.py` `SettingsAPI`: `prompts`, `save_prompts`, `model_settings`, `save_model_settings`, `test_model_settings`, `config`.
- `eta.py` (`run_eta`, `budget_recommendations`, `case_budget_view`, `DEFAULTS`), `progress.py` (`progress`), `errors.py` (`run_errors`, `categorize`: Errors tab), `presentation.py` (`public`: redaction boundary), `artifact_presenter.py` (`json_to_markdown`).

## Request flow
Browser -> `frontend/app/api/harness/[...path]/route.ts` (adds token, validates path segments) -> `Handler.handle_request` -> `LocalAPI` method -> `RuntimeService.submit(Harness...)`. Replies go through `presentation.public`.

## Invariants
- Loopback only; token required; body limit 2 MiB (28 MB for `runs` and `skills/import`); mirror these limits in the Next proxy.
- Evidence access is contained to the run directory: never serve target source or config; HTML reports carry a CSP.
- Never return the API key (last 4 only). One active operation per service (`busy`).

## Test
`uv run pytest -q tests/test_ui_api.py tests/test_ui_errors.py tests/test_ui_eta.py tests/test_ui_launcher.py tests/test_model_settings.py tests/test_case_budget.py`

## Recipe: add an API route
1. Implement a method on `LocalAPI` (or `EvidenceAPI`/`SettingsAPI`).
2. Add its branch in `handle_request` (GET or POST, `parts` match) in `api.py`.
3. Whitelist is automatic in the Next proxy for new path segments (`[A-Za-z0-9_.-]`); add a body-size exception only if large.
4. Add the panel/call in `frontend/components/*` via `api()` from `frontend/lib/harness.ts`; add a case in `tests/ui_fixture_server.py` + a Playwright spec.
5. Add a test in `tests/test_ui_api.py`.

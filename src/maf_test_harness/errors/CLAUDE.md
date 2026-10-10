# errors/ (taxonomy, classifiers, playbook)

Raw tool output -> stable error codes -> whose fault -> what may be done. Deterministic; no model calls.

## Key files
- `taxonomy.py`: `ErrorCode`, `CODES` registry via `_c(code, category, autonomy, title, hint, recipes, max_attempts)`. Categories: environment, infra, test_defect, app_defect, unknown. Autonomy: auto, auto_once, ask, manual.
- `classifiers/`: `common.py`, `python.py`, `java.py`, `dotnet.py` hold `(code, regex)` rules (first match wins per table); `classifiers/__init__.py` has `BY_ECOSYSTEM`, `COMMON`, `EXECUTION`.
- `engine.py`: `classify`, `classify_result`, `primary`, `is_environmental`, `Diagnosis`, `Context`.
- `playbook.py`: `Recipe`, `validate`, `render_markdown` (renders a recipe table as markdown).

## Contracts
- Callers: `envs/runners.py` (`classify_result`), `envs/recovery.py`, `ui/errors.py`, `qa/triage.py`.
- Every `recipes` id in a code must exist in `playbook.RECIPES`; `playbook.validate()` must return `[]` (asserted in `tests/test_errors.py`).

## Gotchas
- Order is priority: put the more specific pattern before the generic one.
- Code ids are stable (UI, docs and tests cite them); never renumber, add new ones.
- `app_defect` is reported, never auto-"fixed".

## Test
`uv run pytest -q tests/test_errors.py tests/test_errors_wiring.py` (sample messages live in `tests/error_cases.py`).

## Recipe: add a classifier + remedy
1. `_c("ENV-XX-NNN", ...)` in `taxonomy.py`.
2. Add `("ENV-XX-NNN", r"...")` to the ecosystem `RULES` (or `EXEC_RULES` for run-time output) in `classifiers/*.py`.
3. If it can be automated, add a `Recipe` in `playbook.py` and an action in `envs/recovery.py`.
4. Add a sample to `tests/error_cases.py`; update `docs/ERROR_PLAYBOOK.md` by hand.
5. Test-defect hints for the QA report live separately in `qa/triage.py` (`REMEDIES`).

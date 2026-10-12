# envs/ (isolated environments, toolchains, runners)

Turns a detected stack into a reviewable install + run plan, provisions toolchains into a private cache, runs approved commands, and recovers from known failures. Nothing here runs before the install/execution approval gates.

## Key files
- `platform_compat.py`: every OS-specific decision (`venv_python`, `resolve_tool`, `raise_if_cancelled`, `run_cancellable`, `kill_process_tree`, `RUN_CANCEL`).
- `execution.py`: `run_command` (argv only, timeout, process group), `copy_repo`, `write_generated`, `read_junit`.
- `runners.py`: per-ecosystem adapters. `resolve_ecosystem`, `setup_steps`, `generated_groups`, `repository_components`, `Step`, `RunPlan`.
- `environment.py`: `build_environment_plan`, `steps_from_plan`. `spec.py` (Python, JDK, Maven/Gradle and .NET detection) / `python_env.py`: what the repo asks for (`EnvSpec`, `ToolRequirement`).
- `toolchains.py`: detect/provision JDK, Maven, .NET into `cache_root()`. `env_check.py`: `missing_packages`, `ui_tooling`, `installed_distributions`.
- `recovery.py`: bounded, allow-listed recovery (`choose`, `Action`, `test_client_packages`, `wipe_venv`). `app_launcher.py`: start the app under test (`detect_launch`, `running`).
- `regression.py`, `setup_guide.py`: Maven regression scopes; manual setup text (never executed).

## Measured coverage (Python)
`setup_steps` installs `COVERAGE_PACKAGES` (exact pins, optional `install-coverage`, `continue_on_failure`). The pytest step carries `optional_argv` (`--cov=.`, branch config, json report); `execute_plan` adds them only when a probe finds coverage and pytest-cov in the venv, keeps `coverage.json` under the step's evidence folder and reports `coverage` (measured or the reason). Tests and the venv are omitted (`COVERAGE_INI_TEXT`). Other ecosystems are not measured.

## Flow
`workflow_steps/execution.py` (`plan_environment`, `plan_execution`) -> `build_environment_plan` / `runners.build_plan` -> human approves -> `run_command` -> `errors.classify_result` -> `recovery.choose` (one allow-listed action) or `qa/triage.py`.

## Invariants
- Never relax repository dependency pins; incomplete install stays a blocking error (strict env: `HARNESS_STRICT_ENV`).
- Test-tooling installs (httpx probe, `ENV-PY-010`) never touch a package the repo declares.
- No `shell=True`; use `run_command`/`run_cancellable`; pathlib + UTF-8; Windows `Scripts/` vs POSIX `bin/` only via `platform_compat`.
- A working copy is not a security sandbox; say so in docs, never claim isolation.

## Test
`uv run pytest -q tests/test_envs.py tests/test_stack_and_runners.py tests/test_platform_compat.py tests/test_env_recovery.py tests/test_execution.py tests/test_strict_environment.py tests/test_test_tooling_recovery.py`

## Recipes
- New ecosystem/runner: add detection in `envs/spec.py` (pattern: `python_env.py`), adapter branch in `runners.py` (`resolve_ecosystem` + plan), classifier table in `errors/classifiers/`, entry in `BY_ECOSYSTEM`, `LANGUAGE_RULES` in `workflow_support.py`.
- New recovery: add an `Action` in `recovery.py`, reference its recipe id from the code in `errors/taxonomy.py`, document in `docs/ERROR_PLAYBOOK.md`.

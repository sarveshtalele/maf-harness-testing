# Dead-code review — 7 October 2026

Review only. Nothing was deleted or modified; this file is the only change. Branch `task-l` (from `new-ui`).

## Method

* AST scan of every `def`/`class`/module-level constant in `src/maf_test_harness` (103 files), counting identifier occurrences across `src`, `tests`, `scripts`, `skills`, `frontend`, `.github` (code only, docs excluded), split into "0 references outside definition" and "only referenced by tests".
* Module-level import scan: every `src` module name searched in all `.py`/`.toml` files.
* Env vars: every `HARNESS_*` name in `.env.example` and in `src`/`scripts`, cross-checked both ways.
* Frontend: every `export` in `frontend/app|components|lib` counted across frontend sources; `package.json` scripts and file-based routes checked.
* Scripts, skills (`skills/config.py`, `SKILLS.lock.json`, `skills/reports.py`), repo-root leftovers, `docs/` binaries and Markdown links, tests (skips, markers) checked by grep.
* Prior review `docs/DEAD_CODE_REVIEW_2026-10-01.md` was read to avoid repeating its (already actioned) findings: `app.py`, `monitoring/ui.py`, `as_json` are gone.

Headline: the Python and TypeScript source is very clean. The scan found **one** genuinely unreferenced function, no unreferenced module, no unreferenced frontend export and no unreferenced env var. Most of the remaining findings are stale documentation and root-level artifacts.

## A) Safe to remove

Note: `docs/ARCHITECTURE.md:238` already records that the shims were removed, so the `memory.md` claim is the stale one.

| Path:line | Kind | Evidence | Risk | Recommendation |
|---|---|---|---|---|
| `src/maf_test_harness/ui/artifact_presenter.py:154` | function `_display_value` | Private; `grep -rn display_value` over the whole repo returns only its own `def` line. 0 references. Not in `__all__`, no getattr/string use. | safe | Delete the function (and `json`/`_scalar` imports only if they become unused, ruff will say). |
| `memory.md:48,110` | stale doc claim | Says old flat module names (`maf_test_harness.runners`, `.skill_contracts`, ...) are one-line `sys.modules` shims. No such shim file exists: `src/maf_test_harness` has only `cli, models, settings, storage, workflow, workflow_support` at top level, and no `sys.modules` assignment exists anywhere in `src`. No test or doc imports an old flat name. | safe | Reword to say the shims were removed (or never kept); nothing to delete in code. |
| `memory.md:98` | stale command | Pre-commit command still lists `app.py`, removed on 2 October. | safe | Drop `app.py` from the command. |
| `design/MIGRATION_PLAN.md:15` | stale doc | Describes `app.py` calling `Harness.start/respond` through `RuntimeService`; `app.py` was removed. Doc is also only linked from `docs/DEAD_CODE_REVIEW_2026-10-01.md` and `docs/BACKEND_FIX_PLAN_2026-10-02.md` (history). | safe (edit) | Add a "historical" banner, or archive with the design files (see B). |
| `docs/audit/agentic-qe-platform-dark.png`, `-light.png`, `-prompts.png`, `docs/audit/modular-workflow-new-run.png` | image evidence | Basename referenced by no doc, test, script or README (grep over README, CLAUDE, memory, docs, src, tests, scripts, frontend/tests: 0 hits). | safe-ish (binary, tracked evidence) | Remove only with explicit approval, CLAUDE.md forbids deleting user evidence without it. Listed here because nothing links them. |
| `docs/audit/petstore-patched-e2e.xml`, `docs/audit/reverse-patched-e2e.xml`, `docs/audit/UI_RESTART_VERIFICATION_2026-10-02.json` | evidence files | 0 references by basename anywhere. (Their `-baseline-` siblings are referenced.) | safe-ish, same approval caveat | Same as above; or add a link from `docs/audit/E2E_RESULTS.md`. |
| `docs/audit/STRICT_GENERATION_2026-10-06.md`, `docs/audit/TOKENTELEMETRY_LIVE_2026-10-01.md` | audit notes | 0 inbound links from any doc. | safe-ish | Link from an index or archive. Not code. |

## B) Needs owner check

| Path:line | Kind | Evidence | Risk | Recommendation |
|---|---|---|---|---|
| `src/maf_test_harness/envs/java_env.py`, `envs/dotnet_env.py` (11 lines each) | modules | Each is a one-line wrapper `spec(workspace) -> detect_spec(...)`. Not imported by any `src`, `scripts` or test file (`grep java_env\|dotnet_env`: only docs and memory.md). `tests/test_module_layout.py` imports them via `pkgutil.walk_packages`, nothing else. `envs/__init__.py` calls `detect_spec` directly. | check | Phase-3 scaffolding. Either delete both (and the rows in `docs/ARCHITECTURE.md:268`, `ARCHITECTURE_MAP.md`, `WORKFLOW_SERVICE_MIGRATION_PLAN.md:46`, which names them as extraction targets) or keep as the planned per-language extraction seam. Owner decision. |
| `src/maf_test_harness/envs/python_env.py:15` | function `current_interpreter_satisfies` | Only referenced by `tests/test_envs.py:52-59`. 0 production callers. (`python_env.spec` is test-only too.) | check | Delete with its test, or wire into `toolchains` if the intent was to skip provisioning when the running interpreter fits. |
| `src/maf_test_harness/repo/routes.py:72` | function `route_prefixes` | Only imported by `tests/test_generation_gates.py:11,130`. | check | Same: delete with its assertion, or confirm a planned caller. |
| `src/maf_test_harness/settings.py:113` | function `model_for` | Only used inside a subprocess string in `tests/test_fail_soft_generation.py:92`. No production caller (stages read `HARNESS_STAGE_MODELS` elsewhere). | check | Verify stage model resolution does not need it; otherwise remove and update that test. |
| `.env.example` vs `src` | undocumented settings | Read in `src`/`scripts` but absent from `.env.example`: `HARNESS_API_PORT`, `HARNESS_API_TOKEN`, `HARNESS_BUILD_ID`, `HARNESS_DESIGN_REVIEW`, `HARNESS_DESIGN_REVIEW_MIN_CONFIDENCE`, `HARNESS_TOOLCHAIN_DIR`, `HARNESS_TOOLCHAIN_MIRROR`, `HARNESS_TOOL_NAMES`. Reverse direction (documented but never read): none, all 26 are read. | check | Documentation gap, not dead code. Add the user-facing ones to `.env.example`. |
| `agentic-testing-harness-v5-fixed.html` (78 KB, repo root) | design reference | Referenced only by `design/MIGRATION_PLAN.md:9` and two review docs. No code or build step uses it. Kept earlier on purpose as a user-selected visual reference. | check | Move to `design/` or `docs/archive/`, or delete if the Next.js UI superseded it. Needs the user. |
| `design/concept.html`, `design/MIGRATION_PLAN.md` | design records | Referenced only by review/plan docs. `MIGRATION_PLAN.md` is stale (see A). | check | Archive or keep as approval history, owner call. |
| `patches/petstore-verify-password.patch`, `patches/reverse-dashboard-unused-esm.patch` | evidence patches | Referenced only by `docs/audit/ERRORS.md` and `docs/ARCHITECTURE.md`; not applied by any script/test/CI. | check | Keep while `docs/audit/ERRORS.md` is the record; otherwise move under `docs/audit/`. |
| `azure-plan/` (10 files) | Terraform + price evidence | Referenced only by `README.md`, `docs/Azure-deployment.md`, `docs/AZURE_DEPLOYMENT_PROPOSAL.md`. Docs state "not a deployment". `docs/Azure-deployment.md` is a 3-line redirect to the proposal. | check | Keep if the Azure path is live; `docs/Azure-deployment.md` is a candidate to fold into the proposal. |
| `docs/agent-framework.pdf` | tracked binary | `git ls-files` lists it, yet `CLAUDE.md:24` says never to commit `agent-framework.pdf`. Referenced only in `memory.md:91` and CLAUDE.md. | check | Likely accidentally tracked. Decide: `git rm --cached` plus `.gitignore`, or amend CLAUDE.md. |
| `docs/` historical set: `IMPLEMENTATION_PLAN.md`, `MODULAR_WORKFLOW_IMPLEMENTATION.md`, `WINDOWS_RUN_E9681C3_REVIEW.md`, `RUN_FAILURE_AND_SKILL_EXECUTION_REVIEW.md`, `BACKEND_REVIEW_2026-10-01.md`, `DEAD_CODE_REVIEW_2026-10-01.md` | stale-candidate docs | Dated/point-in-time plans; markdown link check (all `docs/*.md`, README, CLAUDE, memory) found **0 broken links**, so none is orphaned by a dead target. Not verified current by this review. | check | Add a "historical" banner or move to `docs/archive/`. |
| `tests/test_dependency_conflicts.py` (streamlit strings), `tests/test_app_launcher.py:18-31` | tests mentioning the retired Streamlit GUI | The strings are sample *target-repo* requirements and `app_launcher.detect_launch` is still live code that detects a Streamlit entry in the repo under test. Not about the retired harness GUI. | keep-ish | No action; noted because a grep for `streamlit` looks alarming. |

## C) Keep (looks dead, used dynamically or by tooling)

| Path:line | Looks dead because | Why it is live |
|---|---|---|
| `ui/api.py:469` `log_message` | 1 definition, only a test reference | Override of `BaseHTTPRequestHandler.log_message`, invoked by stdlib; deliberately silences URL logging. |
| `hooks/agent_hooks_bridge.py:44` `EngineInterceptor.intercept` | only `tests/test_hooks_sdk.py` calls it | Called by the optional `agent-hooks-sdk` (`[hooks]` extra) through the interceptor protocol. |
| `skills/contracts.py:523` `_FlattenWithChains.visit_With` | 0 textual references | `ast.NodeTransformer` dispatches `visit_<Node>` by name. |
| `agent/runtime.py:879` `settle_response_cache` | few callers | Fetched with `getattr(self.runtime, "settle_response_cache", None)` in `workflow_steps/planning.py:101`. `HARNESS_RESPONSE_CACHE(_DIR)` are read in `runtime.py:55-60`, so cache feature is live. |
| `src/maf_test_harness/cli.py:main` | not called in code | `pyproject.toml [project.scripts] maf-harness = "maf_test_harness.cli:main"`. |
| `frontend/app/api/harness/[...path]/route.ts`, `app/page.tsx`, `app/layout.tsx`, `next.config.ts`, `playwright.config.ts`, `next-env.d.ts` | never imported | Next.js file-based routing/config; `playwright.config.ts` launches `tests/ui_fixture_server.py` (otherwise unreferenced). |
| `frontend/components/observability.tsx` `Select` | exported from a "component" file | Imported by `workspace.tsx:8`. All 20 other exports under `frontend/components` and `frontend/lib` have at least one consumer (scan: 0 unreferenced exports). |
| `skills/coverage-analyzer`, `skills/traceability-generator` | not named in `skills/config.py` | Used by name in `skills/reports.py:165-193` (`run_skill_checks`) and hash-locked in `SKILLS.lock.json`. Other 11 skills are named in `config.py` (`SKILL_NAMES`, `HOST_SKILL_NAMES`, `COMPANION_PURPOSE`). |
| `skills/*/scripts`, `assets`, `examples`, `references` | no Python importers | Reviewed scripts executed via `skills/scripts.py`; hash-locked by `SKILLS.lock.json`. Do not prune by filename. |
| `scripts/gen_architecture.py`, `json_to_markdown.py`, `start_ui.py`, `vendor_skills.py` | not imported | Standalone commands: `start_ui` is called by `start-*.cmd/.command` and `tests/test_ui_launcher.py`; `gen_architecture` by `tests/test_architecture_map.py` (generates `docs/ARCHITECTURE_MAP.md`); `json_to_markdown` by `ui/artifact_presenter.py`/`evidence_api.py`; `vendor_skills` maintains the skill lock. |
| `prompts/personas.json`, `prompts/repository-review.md` | data files | Loaded by `agent/personas.py` / `agent/prompt_settings.py`; shown by `frontend/components/prompts.tsx`. |
| `.harness/hooks.json.example` | not read at runtime | Template, covered by `tests/test_hooks_config.py` and `docs/HOOKS.md`. |
| `tests/targets/*`, `tests/ui_fixture_server.py`, `tests/error_cases.py`, `tests/fakes.py`, `tests/conftest.py` | no `test_` prefix or skip-gated | `targets/` are `skipif`-gated live suites (need `PETSTORE_BASE_URL` etc.); the others are fixtures/helpers imported by tests or the Playwright config. |
| Empty `__init__.py` files | empty | Package markers (`test_module_layout` walks the package). |
| `agent/diagnosis.py`, `models.py` draft classes, `workflow_support.py` helpers | few references | Used as pydantic response schemas / single-caller helpers; every one has a non-test reference. |
| `.claude/`, `.tools/`, `.targets/`, `.harness_runs/` | repo-root leftovers | None exists in the tracked tree (`.tools`, `.targets`, `.harness_runs` absent; `.claude/` untracked). All are in `.gitignore`. Nothing to do. |

## Dynamic-use patterns checked

`getattr(..., "name")` call sites (found `settle_response_cache`), `importlib`/`pkgutil.walk_packages` (only `tests/test_module_layout.py`, which imports every module and so makes the two `*_env.py` wrappers look "used"), `sys.modules` assignment (none in `src`, i.e. no shims; the other `sys.modules` hits are prompt text for generated tests), `[project.scripts]` (`maf-harness` only; no `entry-points` groups), `@handler`/MAF decorators (no unreferenced decorated function surfaced), stdlib override names (`log_message`), `ast` visitor names (`visit_*`), optional-SDK protocols (`intercept`), monkeypatch targets in tests (strings like `"maf_test_harness...."` resolve to live modules), skill names as strings (`config.py`, `reports.py`), Next.js file routing, `package.json` scripts, `.github/workflows` (no reference to `design/`, `patches/` or `azure-plan/`).

## Counts

* A) Safe to remove: 7 rows (1 function, 3 stale-doc edits, 3 groups of unreferenced audit evidence = 9 files).
* B) Owner check: 12 rows (4 code rows, 1 settings doc gap, 6 root/docs artifact rows, 1 informational test note).
* C) Keep: 15 rows.

## Status 2026-10-11

Owner-approved deletions were carried out on branch `task-dead-code`, one commit per group.

**Removed**

* Unused code: `ui/artifact_presenter.py` `_display_value`; `frontend/app/globals.css` rule `.filter-label` (`.filters` kept). `.resize-both` was kept: `components/data.tsx` builds it as `resize-${axis}` for the panel corner handle, so a static grep misses it.
* Test-only code, with the tests that exercised only it: `envs/python_env.py` `current_interpreter_satisfies`; `repo/routes.py` `route_prefixes`; `settings.py` `model_for` (the per-stage runtime reads `settings.current()["stage_models"]` directly, and the fail-soft test now checks that path); `paths.py` `logs_dir`.
* Modules: `envs/java_env.py`, `envs/dotnet_env.py`. References updated in `docs/ARCHITECTURE.md`, `docs/ARCHITECTURE_MAP.md` (regenerated), `docs/DEVELOPER_GUIDE.md`, `docs/WORKFLOW_SERVICE_MIGRATION_PLAN.md` and `src/maf_test_harness/envs/CLAUDE.md`.
* Legacy root files: `agentic-testing-harness-v5-fixed.html`, `design/`, `patches/`, `azure-plan/`. Links in the README indexes, the Azure documents, `docs/ARCHITECTURE.md`, `docs/DEVELOPER_GUIDE.md` and `docs/audit/ERRORS.md` now read "(removed 2026-10-11; see git history)".

**Kept, with reason**

* Section D (unlinked audit evidence). The review said no markdown file links these files. That was wrong for every file in the list. `docs/README.md` links all of them (`agentic-qe-platform-*.png`, `modular-workflow-new-run.png`, `petstore-patched-e2e.xml`, `reverse-patched-e2e.xml`, `UI_RESTART_VERIFICATION_2026-10-02.json`, `backend_repro.py`, `token_failure_repro.py`). The owner rule was to delete only files that no markdown links to, so nothing was deleted.
* `settings.clear_runtime`, `qa/confidence.target_symbol`, `skills/contracts.parse_case_marker`, `skills/config` `HOST_SKILL_NAMES` and `COMPANION_PURPOSE`. Not approved for deletion, and still useful.
* `HARNESS_LOG_DIR` stays. Its readers are `scripts/start_ui.py` and `ui/settings_api.py`; only the `paths.logs_dir` helper was removed.
* Dated reviews, audit notes and plans are unchanged. Their references to removed paths are history. `memory.md` is the project log and is also unchanged.

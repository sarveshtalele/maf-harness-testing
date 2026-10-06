# memory.md — working notes for the Agentic QE Harness v2

Current branch is **new-ui**. Product: **Agentic QE Platform**. New runs store evidence inside `<repo-root>/.maf-harness`; setup and all runtime checks follow generation at Test execution. Requirements use deterministic host scripts; full discovered intake, component design batches and strict Java quality gates are documented in [the current guide](docs/PETSTORE_STRICT_TEST_GENERATION.md).

The phase tables and run measurements below are historical implementation notes; they do not override current workflow behavior.
Full approved plan: `C:\Users\SarveshTalele\.claude\plans\now-analyse-the-exsiting-curried-crescent.md`.

## 1. What we are building

An **agentic QE harness** (behaves like Claude Code / Codex / Cursor inside each stage): typed tools, hooks, plan/execute
modes, todos, the existing **Agent Skills with their reviewed scripts**, isolated environments for **Python, Java
(Maven + Gradle) and .NET**, bounded automatic recovery from environment and execution errors, and far fewer tokens
through prefix-cache-friendly prompts. The 7 human-gated stages stay (safe, resumable).

Repo: `https://github.com/sarveshtalele/maf-harness-testing` (private). Working branch: **`new-ui`**. Never force-push `main`.

## 2. Decisions taken (do not re-litigate without the user)

| Topic | Decision |
|---|---|
| Agent model | Keep the 7 gated stages; each model stage becomes an autonomous agent loop (`create_harness_agent`) |
| Skills | Use the 10 existing skills strictly with their scripts; keep the hash-locked reviewed-script policy (`skills/SKILLS.lock.json`); no new/unreviewed scripts |
| Isolation | Local process + auto-provisioned toolchains in `~/.maf-harness/toolchains` (no admin, no Docker); Windows + macOS (+ Linux); not a security sandbox |
| Shell | Typed tools only, no raw shell |
| Languages | Python, Java (Maven + Gradle), .NET; Node/Go adapters stay as they are |
| Caching | Prefix-cache-friendly prompt assembly + cached-token metrics + session reuse/compaction (gateway: `deepseek-v4-flash` via LiteLLM). No local response cache |
| Hooks | Both: user `.harness/hooks.json` compiled onto MAF Agent Hooks (`agent_hooks` SDK optional) with an in-house fallback |
| Structure | Subpackages + import shims, small phases, one branch |

## 3. Phases (7 in total: 0–6) and status

| # | Phase | Status | Commit |
|---|---|---|---|
| 0 | Baseline: cached-token accounting, prefix stability metric, `docs/CACHING.md` | **done** | `2d31533` |
| 1 | Restructure into subpackages + import shims (no behaviour change) | **done** (30 modules moved, old names alias the same module object) | `a640453` |
| 2 | Error taxonomy, classifiers, playbook, 56 fixtures, wired into install/build gate/execution/QA | **done** | `6425f5b` |
| 3 | `envs/` adapters + toolchain provisioning (JDK, Maven, Gradle, .NET SDK, Python) | **done** | see log |
| 4 | Typed tool registry + hooks engine (in-house) + built-in hooks | **done** (`docs/HOOKS.md`) | `16cad6b` |
| 5 | Agent runtime: bounded recovery, diagnosis agent, stable-prefix prompts, skill validators as a tool | **done** (see log; `docs/AUTOMATION.md`); deviations listed there | see log |
| 6 | AGENT-HOOKS-0.1 backend (optional SDK), Automation tab, docs | **done** (see log) | see log |

Earlier work on the same branch (before the phases): Windows compatibility (`platform_compat.py`), pip per-package
fallback, `env_check.py` (declared vs installed), `app_launcher.py` + `--start-app` for Playwright E2E, `environment_gaps`
and `app_launch_plan` tools, mermaid fixes, `docs/CATALOG.md`, CI Windows leg.

## 4. Where things live now

Modules are in subpackages of `src/maf_test_harness/`. The old flat names (`maf_test_harness.runners`, `.skill_contracts`, ...)
are one-line shims that replace themselves in `sys.modules` with the real module, so imports *and* monkeypatches through the old
name reach the real module. New code imports the subpackage path. `tests/` were not touched by the move.

| Area | Files |
|---|---|
| Top level | `workflow.py` (gates/checkpoints), `workflow_steps/` (stage operations), `cli.py`, `settings.py`, `models.py`; `frontend/` (Next.js) |
| `repo/` | `analyzer.py` (clone + static analysis), `stack.py`, `routes.py` |
| `envs/` | `environment.py` (plan, probes), `runners.py` (`setup_steps`, `execute_setup`, `execute_plan`, pip per-package fallback), `execution.py`, `platform_compat.py`, `env_check.py`, `app_launcher.py`, and Phase 3: `spec.py`, `toolchains.py`, `{python,java,dotnet}_env.py` |
| `errors/` | `taxonomy.py`, `engine.py`, `playbook.py`, `classifiers/{python,java,dotnet,common}.py`; `docs/ERROR_PLAYBOOK.md` is generated; fixtures in `tests/error_cases.py` |
| `skills/` | `config.py`, `registry.py`, `contracts.py`, `scripts.py`, `reports.py`; the skill folders and `SKILLS.lock.json` stay at repo-root `skills/` |
| `tools/` | `agent_tools.py` (11 tools), `code_intel.py`, `autofix.py`, `build_gate.py`; Phase 4: `registry.py` (`ToolResult` envelope, `ToolRegistry`), `env_tools.py`, `file_tools.py`, `skill_tools.py`, `typed.py` |
| `agent/` | `runtime.py` (`create_harness_agent`, `BudgetGuard`, `ToolTraceMiddleware`, `run_tool_agent`), `personas.py`, `diagnosis.py` |
| `llm/` | `usage.py` (daily ledger), `prompts.py` (stable-first prompt assembly) |
| `qa/` | `report.py`, `triage.py`, `packaging.py` |
| `monitoring/` | `report.py` (incl. *Prompt cache* report), `trace.py`, `telemetry.py` |
| `ui/` | `artifact_presenter.py`, `runtime_service.py` |
| `hooks/` | Phase 4/6: `events.py`, `engine.py` (fail-closed verdicts), `builtin.py`, `config.py` (`.harness/hooks.json`), `maf_bridge.py` (MAF middleware), `agent_hooks_bridge.py` (optional AGENT-HOOKS-0.1 backend) |
| Docs | `docs/ARCHITECTURE.md`, `CATALOG.md`, `CACHING.md`, `ERROR_PLAYBOOK.md`, `ENVIRONMENTS.md`, `OBSERVABILITY.md`, `CLAUDE.md` |

Deliberate deviations from the plan: `workflow.py` stays a single top-level module (a `workflow/` package would clash with
the old module name and it is the highest-risk file); the observability package is called `monitoring/` because
`observability/` would clash with the old `observability.py` shim; `settings.py` and `models.py` stay top level (leaf
modules used everywhere, and checkpoints reference the model classes by module path).

## 5. Measured baseline (Phase 0)

`test_generation` = 83% of input tokens (about 23k input tokens per call for about 1k output). Three real runs:
1.21M / 0.92M / 0.21M input tokens. Target: at least 60% cache hit on generation stages, billed tokens −40–60%.

## 6. Gotchas learned the hard way (save yourself the time)

* **Files use CRLF on this Windows checkout.** A Python `str.replace` with `"\n"` silently does nothing; `\n` inside a
  replacement string inside a heredoc becomes a real newline and breaks string literals. Prefer the Edit tool, or use
  `chr(10)`, or edit bytes with explicit `\r?\n` handling. Verify with `python -c "import ast; ast.parse(...)"`.
* Bash heredocs containing apostrophes/`'EOF'` sometimes fail in this shell; use the Write tool for big files or scripts, and never put `\n` inside a non-raw triple-quoted Python string written from a heredoc (it becomes a real newline).
* `uv run` python and system `python` differ; run tests with `uv run pytest -q --ignore=tests/targets`.
* Windows: a relative `argv[0]` is resolved against the *caller's* directory, not the child's `cwd` — pass absolute
  interpreter paths. A bare `python3` can trigger the Python Install Manager and download an interpreter.
* `pip install -r` is all-or-nothing; one package that must be compiled (e.g. `chromadb` → `chroma-hnswlib`, needs MSVC)
  aborts everything. `execute_setup` now retries per package.
* The repo is **private**: the GitHub API returns 404 for Actions logs; ask the user to paste the failing step.
* `agent_hooks` SDK is **not installed** (MAF Agent Hooks needs it) → the in-house hooks engine is the default.
* The PDF `agent-framework.pdf` is mostly .NET examples; installed Python `agent_framework` 1.18.0 already has
  `create_harness_agent`, compaction, todo/mode, file memory/access, skills, background agents, looping, hooks middleware.
  It has **no** KV-cache feature (caching is a prompt-construction + provider matter).
* Old runs in `.harness_runs/` may contain stale debris (e.g. a downloaded `Python/` dir); never resume a `complete` run.

## 7. Conventions

* Run before every commit: `uv run ruff check src tests app.py scripts && uv run ruff format --check src tests app.py scripts && uv run pytest -q --ignore=tests/targets && uv run maf-harness lint-skills`.
* Commit style: imperative subject, body explains *why*; end with `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
* Do not commit `.env`, `.maf-harness/`, `.harness_runs/`, `agent-framework.pdf`.
* The user prefers terse replies (caveman style) in chat; code, commits and docs are written normally.
* Additive changes; existing behaviour and tests must stay green (currently 690 passed, 1 skipped, with the optional SDK installed).
* CRLF-safe edit helper used in this session: a throwaway `.scratch_edit.py` (JSON pairs on stdin). It is not committed; recreate it if needed.

## 8. Log

* Phase 0 — cached-token accounting in `BudgetGuard`, `_cache_report` in observability, `docs/CACHING.md`.
* Phase 2 — errors package, 37 codes, 56 fixtures, wiring into `runners`, `build_gate`, `qa_report`, `triage`.
* Phase 3 — `envs/` package (`spec.py`, `toolchains.py`, `python_env.py`, `java_env.py`, `dotnet_env.py`), wired into `environment.build_environment_plan` (lists downloads, no longer blocks on provisionable tools), `workflow.run_environment` (provisions after approval, `state["toolchains"]`, audit `toolchains_ready`/`toolchains_failed`), `plan_execution` and `build_gate` (`apply_toolchains`). 30 offline tests (`tests/test_envs.py`, local mirror server). Live smoke on this machine: Temurin 17, .NET 8.0 and Gradle metadata resolve; Maven 3.9.9 really downloaded + checksum-verified and its real error output was classified `ENV-JAVA-002`. `docs/ENVIRONMENTS.md`.
* Phase 1 — `git mv` of 30 modules into `repo/ envs/ skills/ tools/ agent/ llm/ qa/ monitoring/ ui/` with relative imports rewritten by a script and `sys.modules` alias shims at the old paths; 394 existing tests pass unchanged; `tests/test_module_layout.py` (34 tests) checks every alias, patching through an old name, every module importing, and the skills-root path (`monitoring/report.py` moved one level deeper: `parents[3]`). `app.py` and `scripts/` use the new paths. Docs updated (`CLAUDE.md` module map, `ARCHITECTURE.md`, `CATALOG.md`).
* Phase 4 — `hooks/` (8 events, verdicts allow/deny/transform/ask, deny > ask > transform > allow, first enforced deny ends the event, crash/timeout/garbage = deny on enforcing events, evaluate-only mode, command hooks over stdin/stdout JSON with exit 2 = deny and a credential-free env, python hooks), built-ins (secret redaction that keeps JSON valid, write scope, download consent, optional stage budget, audit), `.harness/hooks.json` loader with strict validation (user file, then project file in the working directory, never the cloned repo), `HARNESS_HOOKS=enforce|evaluate_only|off`. Wiring: `Harness` builds the engine, fires SessionStart / StageGate (a veto leaves the approval pending) / OnEnvError / OnExecError / Stop and sets `allow_downloads` when the environment plan is approved; `MAFHarnessRuntime._middleware` orders `[HookChat, BudgetGuard, ToolTrace, HookFunction]`. MAF hands middleware tool results as `list[Content]`, not a string: the bridge rewrites text items only. Typed tools: `ToolResult` envelope, `ToolRegistry` (validate, hooks, handler, hooks; never raises), env tools (`detect_stack`, `detect_toolchains`, `provision_toolchain`, `list_missing_packages`, `diagnose_env_error`, `diagnose_exec_error`) and scoped file tools (`read_file`, `list_dir`, `write_test_file`, `delete_file`); not yet offered to any stage (Phase 5). Tests: `test_hooks*.py` (real MAF loop with a scripted transport, real subprocess hooks, real workflow gates), `test_typed_tools.py`; `tests/conftest.py` isolates the developer's real hooks files. Not done: `apply_env_fix` (recipes are descriptive data until the environment agent), the `agent_hooks` SDK bridge (Phase 6).
* Phase 5 — `envs/recovery.py`: bounded automatic recovery in `execute_setup` (autonomy auto/auto_once only, once per recipe per step, 4 per run, own evidence dir per attempt; recipes `retry-longer`, `retry-update`, `retry-restore`, `recreate-venv` with rewind to `create-venv`; ask/manual reported as `needs_approval`/`manual`); `plan_env_fix` typed tool. `MAFHarnessRuntime.run_tool_agent` + `agent/diagnosis.py`: opt-in (`HARNESS_DIAGNOSIS_AGENT=on`) read-only diagnosis agent with the todo provider, advisory note in the review artifact, never blocks a gate. `llm/prompts.py`: stable-first key order for stage and generation prompts (prefix share 60.7% -> 82.2% on the fixture repo). `tools/skill_tools.py`: `check_skill_output` (reviewed scripts of the stage's own skills, lock policy unchanged), opt-in `HARNESS_SKILL_EVALUATORS=on`. Deviations from the original plan, all documented in `docs/AUTOMATION.md`: no session reuse across retries, no skill-priming removal, no compaction, no per-stage agent loops for the model stages (they stay one structured call with central tools), no plan/execute mode; the agent loops are only verified with a scripted transport, never against a live model.
* Phase 6 — optional AGENT-HOOKS-0.1 backend: `hooks/agent_hooks_bridge.py` (`EngineInterceptor` + `hook_bundle`, `HARNESS_HOOKS_BACKEND=sdk`, extra `hooks` = `agent-hooks-sdk` 0.1.0a5, alpha and native; tests skip without it and CI installs only `dev`). Observability: `automation` section in the report (hook decisions/denies/would-block/errors, typed tool calls, recoveries incl. those left to a human, diagnosis notes), markdown section and an Automation tab. `docs/AUTOMATION.md`, `docs/HOOKS.md` updated.
* Review + two live runs on `sarveshtalele/requirement-analysis-skill` (run 2, with the fix: reached execution review, 16 passed 3 failed, no generation repair; both stopped by the operator; live prefix share only 8.3%, so the fixture 82% figure does not carry over) (stdlib-only Python, `scripts/engine/` package, no tests): found and fixed the long-standing "`engine.X` import path" defect. `tools/code_intel.RepoContext.dependency_text` was the raw text of the package files, and the import gate substring-matched library names against it, so the word "engine" in a comment of `assets/requirements.txt` made `import engine` a "declared dependency": it passed every static gate and failed only at `pytest --collect-only`, three times. Now only declarations count (comments stripped, `pyproject.toml` reduced to dependency names); regression tests in `tests/test_dependency_text.py` fail on the old code. Also: the CI secrets-scan greps the whole repo for `sk-...`/`ghp_...`, so test fixtures must assemble fake credentials at run time; `HARNESS_HOOKS_BACKEND=sdk` without the SDK now fails at runtime construction with an install hint; the Overview tab shows the prompt-cache table. Docs brought up to date (README, ARCHITECTURE, CATALOG, OBSERVABILITY, IMPLEMENTATION_PLAN milestones M11-M17, defect register section H, `docs/audit/E2E_RESULTS.md`). The error taxonomy has 37 codes (an earlier note said 41).
* ENV-PY-009 (installed packages mutually incompatible): live failure on personal-finance-using-RAG (batch 2/9, `Router.__init__() ... on_startup`): the per-package pip fallback lost joint resolution and `streamlit>=1.32.0` pulled `starlette 1.6.0` next to `fastapi==0.110.0`. Fixed with joint re-resolution after the fallback, `pip check` + `repair_conflicts` after install (verified live on that environment), classification (`errors/`, `Context.conflicts`), build gate, `list_dependency_conflicts` and `environment_gaps`. 38 codes now.
* STRICT GUARDRAIL (user rule, 2026-09-21): install ALL requirements, only then proceed. `HARNESS_STRICT_ENV` (default on): after the install steps `Harness._environment_shortfalls` checks failed steps, declared packages missing, `pip check` conflicts remaining; any of them makes the environment gate an error gate (retry/stop only, `state.environment.status = incomplete`), and `Harness.enter` refuses every later stage while incomplete. For `ENV-PY-001/002/003` `envs/recovery.python_fallback` proposes 3.11/3.10/3.12 (installed or installable with uv); Retry wipes the venv and re-plans with `python_override` (an `exact` toolchain requirement listed as a download, approved as usual). Also from the personal-finance-using-RAG run: generation prompt carries `environment` (python, unavailable_packages, ui_tooling); live UI levels only when the environment can run them (Playwright, or Streamlit AppTest for ui); `Could not import X python package` recognised; QA environment actions for never-installed packages; `tools/mock_pitfalls.py` static gates (all 7 real defective tests caught, 6 passing ones unaffected). CLAUDE.md has a Guardrails section; auto-memory `strict-environment-guardrail`. Live check of the guardrail on the real repo: see the log below.
* Phase 3 gaps (carry forward): no scaffolding of a Java/.NET *generated test project*; a JDK download was not exercised live (190 MB, network here was ~60 KB/s); Python provisioning depends on `uv`; Node/Go untouched; the approved plan shows bare `mvn`/`gradle`/`dotnet` and the harness rewrites them to absolute paths after provisioning (audit records it).
* Full-project audit (2026-09-24): removed the Phase 1 flat-module compatibility shims entirely (27 files, down from the
  original 30 minus 3 already-dead ones removed earlier the same day). They were only ever bridging *this repo's own*
  `tests/*.py` from the old flat paths to the new subpackage ones — a private repo with no PyPI publication and no
  external consumers of the old import paths, so once every test file was updated to import the subpackage path
  directly (`maf_test_harness.envs.runners`, `.skills.contracts`, `.agent.runtime`, …), the shims had nothing left to
  protect. `tests/test_module_layout.py` trimmed from 34 tests (alias/patch/import checks per old name) to 2 (every
  module imports, skills root resolves) since the thing it was testing no longer exists. `CLAUDE.md`, `ARCHITECTURE.md`,
  `IMPLEMENTATION_PLAN.md` updated to stop describing the shim layer as current. Also fixed `docs/AUTOMATION.md`
  misattributing `install-each-package`/`fix-conflicts` to `envs/recovery.py` (they're unconditional parts of
  `envs/runners.py`'s install pipeline, not playbook recipes) and two new `mock_pitfalls.py` static checks found live:
  `patch.object(app.state, "x", ...)` before the FastAPI lifespan that sets `x` has run, and `st.session_state` read
  instead of `at.session_state` after driving the app with `AppTest`.

## 9. Phase 3 — design (implemented; kept for reference)

Goal: the harness detects which toolchain a repository needs and provisions it into a private cache when the system
lacks it, so Java/.NET/Python runs stop failing on "wrong JDK / no SDK / wrong Python".

* `envs/spec.py` — detect requirements from repository files: Python (`.python-version`, `requires-python`,
  `runtime.txt`), Java (pom `maven.compiler.release|source|target`, `java.version`, Gradle `sourceCompatibility` /
  `JavaLanguageVersion`, `.sdkmanrc`, `.tool-versions`, wrappers), .NET (`global.json`, csproj `TargetFramework`).
* `envs/toolchains.py` — `Toolchain` (kind, version, home, source), local detection, a download catalogue
  (Adoptium JDK, Apache Maven, Gradle distributions, Microsoft .NET release metadata), checksum-verified download,
  safe extraction, cache layout `~/.maf-harness/toolchains/<kind>/<version>-<os>-<arch>/`, mirror override
  `HARNESS_TOOLCHAIN_MIRROR`, cache dir override `HARNESS_TOOLCHAIN_DIR`. Nothing touches the system PATH.
* `envs/{python,java,dotnet}_env.py` — adapters: `detect`, `requirements`, `env_vars` (JAVA_HOME, PATH,
  DOTNET_ROOT, DOTNET_CLI_HOME, NUGET_PACKAGES, …).
* Integration — environment plan lists required toolchains (found / to download, size, source) so the human approves
  the download inside the existing environment approval; `run_environment` provisions, stores `toolchain_env`, and
  `execute_setup` / `execute_plan` merge it into every step's environment.
* Tests are offline (local HTTP server + crafted archives); live network smoke tests are opt-in (`HARNESS_NETWORK_TESTS=1`).
* Out of scope for Phase 3: scaffolding a .NET/Java *generated test project* (later), Node/Go.

## 10. Core memory log (harness accuracy & MAF alignment, 2026-10-06)

Plan: `~/.claude/plans/loading-older-messages-how-dynamic-coral.md`. Each task runs as a separate worktree subagent with its own context: Sonnet for logic, Haiku for mechanical edits, nothing on Opus. Only the orchestrator writes this log, after reviewing and merging each task. Baseline before the work: 13 pytest failures, all caused by sandbox socket-bind EPERM (launcher, app_launcher, toolchain-download, and HTTP token tests). None are code defects.

Root causes being fixed:
* **Truncated reviewer evidence.** Files were capped at 12k characters, and non-Python methods were sliced as `+70` lines.
* **No remedy for path-normalised imports.**
* **Coverage gate.** It ignored existing repo tests and matched IDs by substring.
* **No testability analysis.**
* **Python/Java-only prompts and skills with stale steps.**
* **Aborting the whole run on one failed batch.**
* **Stop gaps.** There was no confirm, no MiddlewareFailure, no between-batch checks, no CLI stop, and threads outlived "stopped".
* **Node 24 leftovers.** `@types/node` was on 26 and `ui.yml` used old action majors. The "16" in the repo is Next.js, not Node.

### 2026-10-06 T3 Import validator + remedies (Sonnet, merged 2f42392)
- **What changed:**
  - `check_python_imports` detects hyphen/space-normalised paths before the generic "top-level exists" error, and names the real file and the bad component.
  - `qa/triage.py` REMEDIES gained entries for path-normalised imports, structured-output/JSON failures, absent-field injection, static-constructor `@InjectMocks`, reviewer insufficient_evidence/truncated, JavaFX toolkit, TS `Cannot find module` and C# CS0246/CS0234. They are placed before the generic import rule.
  - `runtime._structured_value` turns pydantic `ValidationError` into `AgentOutputError`, so the retry loop repairs it.
- **Tests:** `tests/test_triage.py` covers the mcp-skills-registry regression and checks that old messages still map to their old remedies.

### 2026-10-06 T8 Node 24 alignment (Haiku, merged 3dd2a6d + 015193a)
- **What changed:**
  - `@types/node` is now `^24`. The lockfile was regenerated by the orchestrator (resolves 24.19.1), because the agent's hand edit would have broken `npm ci`.
  - Added `frontend/.nvmrc` (24) and `.npmrc` (`engine-strict=true`).
  - Both launchers fail fast on non-24 Node. They list every node on PATH and show the fix: winget or nvm-windows on Windows, nvm on macOS/Linux. `NODE_VERSION` is reset before detection.
  - `ui.yml` moved to checkout@v7, setup-uv@v7, setup-node@v5 (`node-version-file`) and upload-artifact@v5. `ci.yml` gained a macOS leg.
  - README and Windows docs now say that Next.js 16 is the framework version, not Node.
- **Decision:** `.gitattributes` forces LF on `.cmd`. That is fine because there are no labels or `goto`.

### 2026-10-06 T2 Accurate source evidence (Sonnet, merged a33e3ee)
- **What changed:**
  - New `analyzer._brace_body_end` finds the end of a method body. Strings, text blocks, chars, templates and comments are blanked first. It falls back to 70 lines if no brace is found and caps at 400 lines.
  - `_DEFINITION` is widened to cover Java, C#, Kotlin and TS signatures, generics and arrows, and excludes control keywords.
  - `build_repository_evidence(target_symbols=...)` splices in whole target bodies.
  - `test_review.py` now sends an authoritative `target_source` section, so truncated whole-file evidence alone is no longer grounds for insufficient_evidence.
- **Reviewer isolation:** already held. The persona differs, so the reviewer gets a new agent and does not use the generator's primed session. A test guards this.
- **Tests:** `tests/test_source_extraction.py`, plus an isolation test in `test_native_maf.py`.

### 2026-10-06 T6 Stop-run hardening (Sonnet, merged + 355e160)
- **What changed:**
  - The UI asks `window.confirm` before stopping. A "stopping" status is added: the run reads as busy, the Stop button is hidden and a "Stopping…" notice shows.
  - `RuntimeService.stop_active` waits for the task to end, bounded by 30 s, before marking the run `stopped` and auditing.
  - New `platform_compat` helpers: `is_cancelled`, `raise_if_cancelled` and `run_cancellable`. The last runs the child in its own process group, polls cancel, kills the whole tree and decodes UTF-8; it is used by the env probe and hook commands.
  - Cancel points sit before each generation and design batch, at the start of environment setup and execution, and before every model call.
  - MAF `HookFunctionMiddleware` raises `MiddlewareFailure` when cancelled, so tool loops and sibling calls stop.
  - CLI: Ctrl-C or SIGBREAK leads to status `stopped`, an audit entry and exit code 130.
- **Checkpoints:** they contain only builtins and `agent_framework` types, so no `allowed_checkpoint_types` are needed. A test guards this.
- **Tests:** `tests/test_stop.py` (10 tests). The Playwright stop spec now checks both dismissing and accepting the confirm dialog.
- **Follow-ups:**
  - Resuming after stop means rerunning from the active stage.
  - A thread stuck in a non-cancellable call is abandoned after 30 s.

### 2026-10-06 T1 Generic prompts + skills (Sonnet, merged ec48acb)
- **What changed:**
  - `workflow_support.LANGUAGE_RULES` (python/java/javascript/typescript/csharp), with `language_rules()` and `retry_reminder(policy)`. Only the detected stack's rules reach the model.
  - Universal generation rules: no path-to-import normalisation, reuse the repo's execution API and fixtures, no absent members, no tautologies, respect testability.
  - The design prompt requires every requirement to be a new case, a cited existing test, or an ID-linked gap, and each case must target the implementing method.
  - Skills rewritten as Goal → Inputs → Steps → Output → Self-check:
    - generic-test-generator has `references/<lang>.md`.
    - Playwright SKILL.md went from 359 to about 45 lines, with detail moved to references.
    - The Petstore, gherkin and `.github` stale references are removed.
    - Coverage states added: new / pre_existing_witness / excluded / unexplained.
  - Lint now treats SKILL.md over 500 lines as an error and requires `allowed-tools` to be a string.
  - Lock hashes were refreshed for the edited files only.
- **Merge note:** T3's `_retry_reminder(state)` was dropped in favour of `retry_reminder(policy)`.
- **Follow-up:** `docs/PETSTORE_STRICT_TEST_GENERATION.md` still mentions JUnit 4. It is historical and was left alone.

Wave 1 verification: 815 passed. The 13 failures are the sandbox EPERM baseline. ruff, format, lint-skills and tsc all pass.

### 2026-10-06 T4 Existing-test witnesses (Sonnet, merged)
- **What changed:**
  - `qa/quality.existing_requirement_witnesses` scans test files in any language for direct calls to each requirement's `source_symbol`. It skips definitions and comments. Generic symbols only count when the class or module name appears in the same file. Reads are capped at 2 MB.
  - `existing_test_evidence` ranks test files by witness hits first, then by non-generic file stems.
  - The coverage gate now does four things:
    - drops witnessed requirements from the missing list;
    - matches requirement IDs as exact tokens, so REQ-1 is no longer satisfied by REQ-10;
    - requires at least 10 characters of reason for every gap;
    - writes `gaps_report.pre_existing_test_witnesses` and `excluded_requirements`.
  - The design prompt gets an `existing_test_witnesses` section.
  - The QA report adds `requirement_state` per requirement (new / pre_existing_witness / excluded / unexplained) and `requirement_state_counts`.
- **Decision:** `uncovered_requirements` is unchanged, because existing consumers read it. A witness is a static call match, not measured coverage.
- **Follow-up:** `skills/reports.py` does not report witness states yet.
- **Tests:** `tests/test_requirement_witnesses.py` replays the mcp-server-market-v2 case and the REQ-1/REQ-10 regression. 820 passed after the merge.

### 2026-10-06 T5 Testability analysis (Sonnet, merged)
- **What changed:**
  - New `repo/testability.py`. It is deterministic, regex-based and makes no model calls. `analyze_testability` grades each class:
    - **low:** the constructor does static or singleton acquisition or I/O.
    - **medium:** the class uses a UI toolkit (JavaFX, Swing, WinForms/WPF, React `.tsx`) or creates non-injectable locals.
    - **high:** neither applies.
  - `mockStatic` is suggested only when mockito-inline or mockito 5+ is declared.
  - Results are stored in `repo_analysis["testability"]`, and only entries relevant to the current batch reach the design and generation prompts.
  - `retarget_cases` moves an arithmetic expectation to the single same-file callee that implements it, for example setPrice → calculatePrice. Each move is audited as `design_case_retargeted` and recorded in `host_notes`.
- **Merge:** planning.py and prompts.py conflicted with T4's `existing_test_witnesses`. Both sections were kept.
- **Follow-ups:**
  - Retargeting does not yet check that field names match.
  - Nested classes are not analysed.
  - Runs from before this change get no testability data until repo_analysis is re-run.

### 2026-10-06 T7 Fail-soft batches + per-stage models (orchestrator, on top of the Haiku worktree)
- **Rejected:** the Haiku branch moved the batch loop into a copied 240-line `_generate_batch`. That copy lost T5's testability section and re-derived the cache, so it was not merged.
- **What was taken from the branch:** settings, runtime and `.env.example`.
- **Fail-soft:** implemented as a small edit to the existing loop.
  - A batch that exhausts its attempts is recorded in `generation_failures` and audited as `test_generation_batch_failed`.
  - The run continues. It aborts only when more than `HARNESS_MAX_FAILED_BATCH_RATIO` (0.5) of batches fail, or when no files were generated.
  - Failed batches are not cached, so a rerun or heal retries them. Heal rounds keep the batch's passing files.
  - Failed cases show as `generation_failed` in the catalog and the QA report, and they block a PASS verdict.
- **Per-stage models:** `HARNESS_STAGE_MODELS` takes a strict JSON object. The runtime uses `STAGE_MODELS.get(stage) or self.model`, and `state["stage_models"]` records the model used.
- **Dropped:** the separate reviewer budget, as YAGNI. Once failures are fail-soft, a reviewer rejection no longer aborts the stage.
- **Tests:** `tests/test_fail_soft_generation.py` covers continue, ratio abort, report verdict, stage-model parsing and the tool budget. `test_attempt_audit` now pins the ratio at 0. 832 passed.

### 2026-10-06 T9 CLAUDE.md (Haiku, merged)
- CLAUDE.md grew from 49 to 74 lines. It now covers the module map, gate order, coverage states, prompts and skills conventions, stop semantics, strict cross-OS rules, and the orchestration and memory convention. Every symbol it names was checked to exist.

Final verification on `new-ui`:
- pytest: 832 passed, 13 failed. All 13 failures are the sandbox socket EPERM baseline.
- ruff check and format: clean. lint-skills: OK, including the lock. `tsc`: OK. `next build`: OK on Node v24.21.0.
- Not run here:
  - Playwright and the launcher smoke tests, because the sandbox blocks local port binding. CI runs both on all 3 OSes.
  - The live re-runs against the mcp-server-market-v2, mcp-skills-registry and store-pos repos, which need the gateway.

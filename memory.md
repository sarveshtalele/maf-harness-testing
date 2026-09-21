# memory.md — working notes for the Agentic QE Harness v2

Living document. Read this first when resuming. Update the *Status* table and *Log* at the end of every phase.
Full approved plan: `C:\Users\SarveshTalele\.claude\plans\now-analyse-the-exsiting-curried-crescent.md`.

## 1. What we are building

An **agentic QE harness** (behaves like Claude Code / Codex / Cursor inside each stage): typed tools, hooks, plan/execute
modes, todos, the existing **Agent Skills with their reviewed scripts**, isolated environments for **Python, Java
(Maven + Gradle) and .NET**, bounded automatic recovery from environment and execution errors, and far fewer tokens
through prefix-cache-friendly prompts. The 7 human-gated stages stay (safe, resumable).

Repo: `https://github.com/sarveshtalele/maf-harness-testing` (private). Working branch: **`windows-compat-docs`**
(pushed; PR link: `.../pull/new/windows-compat-docs`). Never force-push `main`.

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
| 1 | Restructure into subpackages + import shims (no behaviour change) | pending (deliberately after 2/3: churn is pointless before the new modules exist) | — |
| 2 | Error taxonomy, classifiers, playbook, 56 fixtures, wired into install/build gate/execution/QA | **done** | `6425f5b` |
| 3 | `envs/` adapters + toolchain provisioning (JDK, Maven, Gradle, .NET SDK, Python) | **done** | see log |
| 4 | Typed tool registry + hooks engine (in-house) + built-in hooks | pending | — |
| 5 | Agent runtime: per-stage loops, persistent sessions, todos/modes, compaction, skill-script evaluators, environment + diagnosis agents, stable-prefix `PromptAssembler` | pending | — |
| 6 | MAF Agent Hooks bridge (optional SDK), UI panels, docs (`ENVIRONMENTS`, `HOOKS`, updated `CATALOG`/`ARCHITECTURE`) | pending | — |

Earlier work on the same branch (before the phases): Windows compatibility (`platform_compat.py`), pip per-package
fallback, `env_check.py` (declared vs installed), `app_launcher.py` + `--start-app` for Playwright E2E, `environment_gaps`
and `app_launch_plan` tools, mermaid fixes, `docs/CATALOG.md`, CI Windows leg.

## 4. Where things live now

| Area | Files |
|---|---|
| Workflow / gates / checkpoints | `src/maf_test_harness/workflow.py` (1.5k lines), `cli.py`, `app.py` (Streamlit) |
| Model runtime | `maf_runtime.py` (`create_harness_agent`, `BudgetGuard`, `ToolTraceMiddleware`), `usage.py` |
| Environment | `environment.py` (plan, probes), `runners.py` (`setup_steps`, `execute_setup`, `execute_plan`, per-package pip fallback), `execution.py`, `platform_compat.py`, `env_check.py`, `app_launcher.py`, `build_gate.py` |
| Errors (Phase 2) | `errors/` (`taxonomy.py`, `engine.py`, `playbook.py`, `classifiers/{python,java,dotnet,common}.py`), `docs/ERROR_PLAYBOOK.md` (generated), `tests/error_cases.py` |
| Skills | `skills/` (10 skills + `SKILLS.lock.json`), `skills_config.py`, `skill_contracts.py`, `skill_scripts.py`, `skill_reports.py` |
| Tools for agents | `agent_tools.py` (11 tools), `code_intel.py`, `autofix.py` |
| Observability | `observability.py` (now has a *Prompt cache* report), `trace.py` |
| Docs | `docs/ARCHITECTURE.md`, `CATALOG.md`, `CACHING.md`, `ERROR_PLAYBOOK.md`, `OBSERVABILITY.md`, `CLAUDE.md` |

## 5. Measured baseline (Phase 0)

`test_generation` = 83% of input tokens (about 23k input tokens per call for about 1k output). Three real runs:
1.21M / 0.92M / 0.21M input tokens. Target: at least 60% cache hit on generation stages, billed tokens −40–60%.

## 6. Gotchas learned the hard way (save yourself the time)

* **Files use CRLF on this Windows checkout.** A Python `str.replace` with `"\n"` silently does nothing; `\n` inside a
  replacement string inside a heredoc becomes a real newline and breaks string literals. Prefer the Edit tool, or use
  `chr(10)`, or edit bytes with explicit `\r?\n` handling. Verify with `python -c "import ast; ast.parse(...)"`.
* Bash heredocs containing apostrophes/`'EOF'` sometimes fail in this shell; use the Write tool for big files.
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
* Do not commit `.env`, `.harness_runs/`, `agent-framework.pdf`.
* The user prefers terse replies (caveman style) in chat; code, commits and docs are written normally.
* Additive changes; existing behaviour and tests must stay green (currently 394 passed, 1 skipped).
* CRLF-safe edit helper used in this session: a throwaway `.scratch_edit.py` (JSON pairs on stdin). It is not committed; recreate it if needed.

## 8. Log

* Phase 0 — cached-token accounting in `BudgetGuard`, `_cache_report` in observability, `docs/CACHING.md`.
* Phase 2 — errors package, 41 codes, 56 fixtures, wiring into `runners`, `build_gate`, `qa_report`, `triage`.
* Phase 3 — `envs/` package (`spec.py`, `toolchains.py`, `python_env.py`, `java_env.py`, `dotnet_env.py`), wired into `environment.build_environment_plan` (lists downloads, no longer blocks on provisionable tools), `workflow.run_environment` (provisions after approval, `state["toolchains"]`, audit `toolchains_ready`/`toolchains_failed`), `plan_execution` and `build_gate` (`apply_toolchains`). 30 offline tests (`tests/test_envs.py`, local mirror server). Live smoke on this machine: Temurin 17, .NET 8.0 and Gradle metadata resolve; Maven 3.9.9 really downloaded + checksum-verified and its real error output was classified `ENV-JAVA-002`. `docs/ENVIRONMENTS.md`.
* Phase 3 gaps (carry forward): no scaffolding of a Java/.NET *generated test project*; a JDK download was not exercised live (190 MB, network here was ~60 KB/s); Python provisioning depends on `uv`; Node/Go untouched; the approved plan shows bare `mvn`/`gradle`/`dotnet` and the harness rewrites them to absolute paths after provisioning (audit records it).

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

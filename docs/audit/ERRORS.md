# Defect register — MAF test harness (audit of 2026-09-19)

Method: reviewed every first-party module, skill, test and script; ran the suite; compared behavior with the Microsoft Learn
*Agent Skills* documentation and the agentskills.io specification; ran live end-to-end workflows through a real model
gateway. "Found by" tells you how each defect was established. All rows are **fixed** in this repository unless marked
otherwise; each fix has a regression test.

Severity: **H** breaks the stated purpose or gives false confidence · **M** wrong/fragile behavior · **L** hygiene.

## A. MAF and Agent Skills

| # | Sev | File | Defect | Found by | Fix |
|---|---|---|---|---|---|
| A1 | H | `maf_runtime.py` | Agent Skills were **not used through MAF**: `SKILL.md` was pasted into instructions with tools disabled. No `SkillsProvider`, no `load_skill`, no progressive disclosure (contradicts MS Learn "Agent Skills"). | doc comparison | `SkillsProvider(FilteringSkillsSource(FileSkillsSource))`, exactly the assigned skill; explicit `preloaded` fallback labelled as such |
| A2 | H | `maf_runtime.py` | "Skill loaded" check was a tautology (`loaded_skills[stage]` is set by the code that then checks it) — no proof a skill influenced a stage. | code review | host reads a `FunctionMiddleware` trace; output rejected unless `load_skill(<assigned>)` succeeded |
| A3 | H | `maf_runtime.py` | **Live:** with structured output enabled, the gateway model skipped the tool call → every stage failed 3/3 ("did not load the assigned Agent Skill"). Also hallucinated tool names (`read_repo`, `resolve_app_context`). | live run 1–2 | two-phase priming (tool-only turn, then structured turn on the primed session snapshot), 3 attempts, "only tools are load_skill/read_skill_resource" prompt |
| A4 | M | `tests/test_native_maf.py` | Baseline test failed: asserted skill text in chat *messages*; MAF sends instructions in `options["instructions"]`. The claim it made was untested. | test run | rewritten around a scripted transport that records tools/instructions |
| A5 | M | `maf_runtime.py` | Whole MAF session (skill text + prompts + evidence) persisted into `run.json` and hashed into every approval request. | code review | sessions are not persisted; primed snapshot kept in memory |
| A6 | L | `maf_runtime.py` | Dead approval-replay path (`decisions`, `tool_approval` status) with no caller. | code review | removed; approval requests are rejected explicitly |
| A7 | M | MAF 1.18 | Docs: `run_skill_script` advertised only when scripts exist. Implementation: registered always. | probe + source | mitigated (needs approval, never auto-approved, script discovery disabled) and asserted so upgrades are noticed |
| A8 | M | `skills/` | No validation of skills against the spec (name = directory, description ≤ 1024, body < 500 lines). | spec comparison | `skills_registry.py`, `maf-harness lint-skills`, `require_valid` before every stage (8/8 clean) |

## B. Test quality: requirements, design, generation

| # | Sev | File | Defect | Found by | Fix |
|---|---|---|---|---|---|
| B1 | H | `models.py`, `skill_contracts.py` | Requirements schema had no **actors/personas**, acceptance criteria, validation rules or API contracts; finalizer hard-coded empty arrays. The design skill's AC/contract rules could never fire and contract tests were always reclassified. | code review | schema + finalizer produce actors, categories, ACs (by index → stable ID), rules, explicit contracts |
| B2 | H | `models.py` | Design cases had no `level`, `actor`, `target`, `preconditions`, data → generator guessed unit vs UI vs API. | code review | fields added; host validates level/actor |
| B3 | M | `workflow.py` | Design could emit UI/E2E cases for repos with no running app → untestable cases. | design review | `live_app` policy: those levels only with `--app-url`, enforced by host |
| B4 | H | `skill_contracts.py` | Skipped tests (`test.skip`, `@Disabled`, `pytest.mark.skip`) counted as valid tests → false coverage. | code review | `_SKIP_MARKERS` rejection |
| B5 | M | `skill_contracts.py` | `\bplaceholder\b` rejected legitimate UI tests (`[placeholder="Search"]`); JS regex `\b(?:it\|test)\(` counted `re.test(x)` calls. | code review | targeted placeholder patterns; declaration regex anchored to line start |
| B6 | M | `skill_contracts.py` | No syntax check for non-Python generated source. | code review | `node --check` / `gofmt -e` when installed (Java/C#/TS compile gates: roadmap) |
| B7 | H | live run 3 | **Live:** the model returned source with all newlines removed (`"import sysimport pytest…"`); 9/9 attempts failed with "invalid syntax". This is the class of failure in the original report ("no recognized concrete test declarations"). | live run | source returned as `lines[]` (one item per line) — structure survives; validators unchanged |
| B8 | H | live run 4 | **Live:** model guessed import paths (`from reverse_engineer_agent import …`) → collection errors. | live run | static import-resolution gate (stdlib / repo path / declared dependency) with "did you mean" hints; persona rule |
| B9 | M | `skill_contracts.py` | `_case_marker_comment` had two identical branches. | code review | simplified |
| B10 | H | live runs 6-7 | **Live:** design stage left 10 of 22 requirements with no test and no stated reason (54% coverage) and the workflow still passed the gate. | live run metrics | host requires every requirement to be covered or its ID cited in `gaps` (repair loop, last attempt accepted with the gap recorded in `gaps_report.notes`) |
| B11 | H | live Petstore run 2 | **Live:** generated Java used RestAssured, which the build file does not declare → one compile error made all 30 tests "not executed". | live run | Java/Kotlin import gate against build files + repository packages |
| B12 | H | live Petstore run 3 | **Live:** generated Java files were truncated (`reached end of file while parsing`) → module compile failure, 41/41 not executed. | live run | `javac` syntax-only check (semantic errors ignored) and brace-balance check for brace languages; fully-qualified `@org.junit.Test` recognized |
| B13 | M | live run 6 | **Live:** stage repair carried no reason (temperature 0 repeats the same wrong answer), e.g. requirements citing files never supplied. | live run | `MODEL_REPAIR_ATTEMPTS` loop feeds the host error back; allowed evidence paths listed in the prompt |

## C. Execution and QA

| # | Sev | File | Defect | Found by | Fix |
|---|---|---|---|---|---|
| C1 | H | `workflow.py` | Workflow **never executed** generated tests and ended with `not_run_by_design`; "complete" proved nothing about the tests. | design review | execution stage: reviewed plan → approval → run → evidence → QA report |
| C2 | H | `execution.py` | Standalone tool only (not integrated), single command, POSIX only, no per-ecosystem setup. | code review | adapters for pytest, jest/vitest/playwright, maven, gradle, go, dotnet; plan hash prevents change after approval |
| C3 | M | `execution.export_tests` | Workspace copy filtered `bin/ vendor/ build/` (breaks Go vendoring, Node CLIs) and `rglob` descended into `.git`/`node_modules`. | code review | prune-walk with a small skip set |
| C4 | H | live run 4 | **Live:** pytest ran inside the harness tree, so it walked up and adopted the *harness's* `pyproject.toml` (rootdir, testpaths). | live run | `-c`/`--rootdir` isolation unless the repo ships pytest config |
| C5 | H | live run 4 | **Live:** two collection errors aborted the session → 24 valid tests never ran → whole run `FAIL/not_executed`. | live run | `--continue-on-collection-errors`, importlib mode |
| C6 | M | live run 4 | Model-generated tests used `mocker` but `pytest-mock` was not installed. | live run | allow-listed helper install (never arbitrary model-named packages) |
| C7 | M | `execution.py` | Env for commands was too narrow for Java/Go/.NET/Playwright toolchains yet had to exclude secrets. | design | explicit forwarded-env allow-list; secrets-not-forwarded test |
| C9 | M | live Petstore run 2 | **Live:** a build failure produced zero per-test results, so nothing explained *why* cases were not executed. | live run | decisive build/collection lines captured as `diagnostics` and attributed to each case's file in the QA report (also feeds the heal loop) |
| C10 | M | live run 6 | **Live:** a full heal cycle exceeded the 1M token default budget (each generation call carried ~25k tokens of evidence). | live run | generation evidence trimmed to 16 files/40k chars; budgets are env-configurable (`HARNESS_RUN_TOKEN_LIMIT`) |
| C8 | M | `workflow.py` | Heal loop was impossible: failing tests could not go back to generation with evidence. | design | human-approved `test_execution → test_generation` edge, ≤3 rounds, only failing batches regenerated |

## D. Input, repository analysis, hygiene

| # | Sev | File | Defect | Found by | Fix |
|---|---|---|---|---|---|
| D1 | M | `repo_analyzer.py` | URL policy accepted only `owner/repo`; page URLs with `/tree/<ref>` rejected; no ref pinning. | user question (URL vs clone URL) | both forms accepted, normalized; ref pinned |
| D2 | M | `repo_analyzer.py` | `git clone` could block on a credential prompt (private/typo repos). | code review | `GIT_TERMINAL_PROMPT=0` |
| D3 | M | `repo_analyzer.py` | `package.json` that is a list (or non-dict deps) raised `AttributeError`. | fuzz test | guarded parsing |
| D4 | M | `repo_analyzer.py` | `bin/` ignored during analysis (real source in Node/Ruby repos). | code review | removed from ignore set |
| D5 | L | `repo_analyzer.py` | Thin stack detection (no Spring Boot/Quarkus, JUnit 5 vs 4, TestNG, Streamlit/Click/Typer). | code review | added; new `stack.py` profile (runner, UI, service, OpenAPI, CI) |
| D6 | L | `workflow.py`, `models.py`, `__init__.py` | Dead legacy model/workflow code (`Stage`, `WorkflowState`, `TestCase`, `normalize_test_case_ids`, …) and functions defined before imports (`E402` waiver); function-local imports. | code review | removed; imports hoisted; waiver dropped |
| D7 | M | `settings.py` | 1M "input/context" limits unrealistic; budgets not configurable. | code review | env-configurable, realistic defaults |
| D8 | M | (earlier session) | Gateway credential was embedded in source. | earlier audit | env-only. **Rotate that credential.** |
| D9 | L | `app.py` | Hard-coded "Not run", generic interruption text, no execution/QA UI, deprecated `use_container_width`. | UI review + browser test | QA tab, execution approval, app URL, error shown, `width="stretch"` |
| D10 | L | repo | No CI, no secrets scan, machine paths in audit logs, `.claude/` local state. | repo review | GitHub Actions, secrets scan, ignore rules |
| D11 | H | (original report) | "Generation failed after 3 attempts / Interrupted after approval". Root causes: README-starved evidence (fixed earlier), no retryable gate after downstream failure (fixed earlier), and now B7/B8 for the model-side causes. | run audit | see B7, B8, `tests/test_recovery.py` |

## E. Findings in the systems under test (from qualification runs; upstream fixes as patches)

| Target | Finding | Evidence |
|---|---|---|
| reverse-engineering-skill | Generated dashboard raises `Unexpected token 'export'` (ESM `@hpcc-js/wasm` loaded as a classic script) | `reverse-e2e.xml`, `patches/reverse-dashboard-unused-esm.patch` |
| agoncal-application-petstore-ee7 | `AccountBean.doLogin()` selects the customer by login and **never verifies the password** | `petstore-baseline-e2e.xml`, `patches/petstore-verify-password.patch` |

Still open (not defects fixed here): sandboxed execution, application lifecycle manager, compile gates for typed languages,
full-repo retrieval — see [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md).
